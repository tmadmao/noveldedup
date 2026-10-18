"""图形界面（tkinter，Python 标准库，零依赖）。

界面结构：**分组 → 组内成员(勾选) → 书对 → 左右原文对照**
--------------------------------------------------------
┌ 工具栏：目录 / 档位 / 开始 / 自检 ───────────────────────────────┐
├ 状态栏：阶段 + 进度 + 已用时间 + 当前文件 ────────────────────────┤
├────────┬──────────────────┬────────────────────────────────┤
│ 分组列表 │ 组成员（勾选要删的） │  A 原文  │  B 原文             │
│ (过滤框) │ 组内书对           │  （高亮匹配起点）                  │
│         │ 匹配段             │                                  │
├────────┴──────────────────┴────────────────────────────────┤
│ 删除工具条：一键清理 hash 一致 / 全库预勾选 / 勾选本组多余项 / 删除勾选 │
├ 运行日志（可折叠）                                                │
└ 导出 CSV / JSON / 复制路径 / 打开文件夹 ───────────────────────────┘

四条工程纪律（都踩过坑）
------------------------
1. **日志面板与状态栏是"必须"的**：没有它们，用户点完扫描只能看到"准备中"，
   无法判断是卡住还是单纯慢。
2. **轮询用 finally 重新挂载**：`after` 链条一旦因异常断掉，界面永久不刷新（像卡死）。
3. **分组列表按需展开**：上万 本书有 1,768 组 / 17,290 对，
   一次性把书对和段落都塞进 Treeview 会卡；只建分组行，书对与段落选中时才建。
4. **删除一律走回收站 + 二次确认**：查重会一次性指向几千个文件，
   任何"直接删"的实现都是迟早要出事的设计。
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
import traceback
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __version__, core, deleter, formats, plan, report

APP_TITLE = f"小说查重 v{__version__}"
MAX_LOG_LINES = 800
MAX_CONFIRM_ROWS = 800      # 确认框里最多列这么多行，再多只给数量

# 各阶段在总进度条上占的区间（起点%, 跨度%），避免每阶段从 0 重走
PHASE_SPAN = {
    "准备扫描": (0, 2),
    "枚举文件": (0, 3),
    "检查缓存": (3, 7),
    "指纹计算": (10, 60),
    "候选比对": (70, 8),
    "精算证据": (78, 20),
    "完成": (100, 0),
}


def _dpi_fix() -> None:
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def _mb(n: int) -> str:
    if n >= 2 ** 30:
        return f"{n / 2 ** 30:.2f} GB"
    if n >= 2 ** 20:
        return f"{n / 2 ** 20:.1f} MB"
    return f"{n / 1024:.0f} KB"


class _QueueLogHandler(logging.Handler):
    """把日志塞进队列，由界面线程消费（Tk 只能在主线程碰）。"""

    def __init__(self, q: queue.Queue) -> None:
        super().__init__()
        self.q = q
        self.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.q.put(("log", self.format(record), record.levelname))
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------- 二次确认


class ConfirmDelete(tk.Toplevel):
    """删除前的人工确认：列全清单 + 勾选"我已确认"才能点删除。

    为什么不用 messagebox.askyesno
    -----------------------------
    它塞不下几千个路径，也拦不住"手快按回车"。这里三件事一起做：
    ① 把要删的文件全部列出来（可滚动、可暂停看）② 打勾才能提交 ③ Enter 不触发删除。
    """

    def __init__(self, master, items: list, title: str, note: str,
                 unit: str = "文件", headline: str = "") -> None:
        super().__init__(master)
        self.approved = False
        self.title(title)
        self.geometry("880x600")
        self.minsize(680, 420)
        self.transient(master)

        total = sum(i.size for i in items)
        # 空文件夹是**直接删**的（里面没有文件，回收站没意义；网络盘上回收站也不支持），
        # 措辞必须说清楚，不能让用户以为还能从回收站捞回来。
        action = "直接删除" if unit == "文件夹" else "移入回收站"
        head_text = headline or f"即将{action} {len(items):,} 个{unit}"
        head = ttk.Frame(self, padding=(12, 10, 12, 4))
        head.pack(fill="x")
        tk.Label(head, text=head_text,
                 font=("Microsoft YaHei UI", 12, "bold"), fg="#b3261e").pack(anchor="w")
        tk.Label(head, text=(f"合计释放 {_mb(total)}　·　可在系统回收站还原" if total
                             else "这些文件夹里已经没有任何文件　·　删除不影响任何内容"),
                 font=("Microsoft YaHei UI", 9), fg="#666666").pack(anchor="w", pady=(2, 0))
        if note:
            tk.Label(head, text=note, font=("Microsoft YaHei UI", 9),
                     fg="#1a5fb4", wraplength=820, justify="left").pack(anchor="w", pady=(4, 0))

        mid = ttk.Frame(self, padding=(12, 4))
        mid.pack(fill="both", expand=True)
        txt = tk.Text(mid, wrap="none", font=("Consolas", 9), background="#fbfbfb")
        ys = ttk.Scrollbar(mid, orient="vertical", command=txt.yview)
        xs = ttk.Scrollbar(mid, orient="horizontal", command=txt.xview)
        txt.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        txt.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        mid.rowconfigure(0, weight=1)
        mid.columnconfigure(0, weight=1)

        shown = items[:MAX_CONFIRM_ROWS]
        for i in shown:
            txt.insert("end", f"{_mb(i.size):>9}  {i.name}\n")
            txt.insert("end", f"{'':>9}  ↳ {i.reason}"
                              + (f"　保留：《{i.keep_name}》\n" if i.keep_name else "\n"))
        if len(items) > len(shown):
            txt.insert("end", f"\n… 还有 {len(items) - len(shown):,} 个未显示（同样会被删除）\n")
        txt.configure(state="disabled")

        foot = ttk.Frame(self, padding=(12, 6, 12, 12))
        foot.pack(fill="x")
        self.var_ok = tk.BooleanVar(value=False)
        confirm_text = ("我已确认删除以上空文件夹（里面没有任何文件）" if unit == "文件夹"
                        else "我已确认以上文件都要删除（移入回收站，可还原）")
        cb = ttk.Checkbutton(foot, text=confirm_text,
                             variable=self.var_ok, command=self._sync)
        cb.pack(anchor="w")

        row = ttk.Frame(foot)
        row.pack(fill="x", pady=(8, 0))
        self.btn_ok = ttk.Button(row, text=f"{action}（{len(items):,} 个{unit}）",
                                 style="Danger.TButton", state="disabled",
                                 command=self._confirm)
        self.btn_ok.pack(side="right")
        ttk.Button(row, text="取消", command=self._cancel).pack(side="right", padx=8)

        self.bind("<Escape>", lambda _e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.grab_set()
        self.focus_set()

    def _sync(self) -> None:
        self.btn_ok.configure(state="normal" if self.var_ok.get() else "disabled")

    def _confirm(self) -> None:
        if not self.var_ok.get():
            return
        self.approved = True
        self.destroy()

    def _cancel(self) -> None:
        self.approved = False
        self.destroy()


class ClearCacheDialog(tk.Toplevel):
    """清除缓存：让用户明确选"只清本库"还是"清全部"，别一键误清。"""

    def __init__(self, master, cur_mb: float, all_mb: float, cur_name: str) -> None:
        super().__init__(master)
        self.choice = ""          # "cur" / "all" / ""
        self.title("清除缓存")
        self.geometry("620x300")
        self.minsize(560, 280)
        self.transient(master)

        head = ttk.Frame(self, padding=(14, 12, 14, 4))
        head.pack(fill="x")
        tk.Label(head, text="清除缓存数据", font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w")
        tk.Label(head, wraplength=560, justify="left", font=("Microsoft YaHei UI", 9),
                 fg="#444444",
                 text=("缓存是本工具自己生成的中间数据（每本书的指纹 + 判断结果），"
                       "用来让「下次扫描很快」。\n\n"
                       "· 清除**不会影响你的书**，只删缓存；\n"
                       "· 清除后下次扫描要**重新计算**（1.7 万本约 40 分钟）；\n"
                       "· 建议在删完重复文件、暂时不打算再扫的时候清一次，回收空间。")
                 ).pack(anchor="w", pady=(6, 0))

        info = ttk.Frame(self, padding=(14, 8))
        info.pack(fill="x")
        tk.Label(info, font=("Microsoft YaHei UI", 9), fg="#1a5fb4", anchor="w",
                 text=f"当前书库：{cur_name}　缓存 {cur_mb:,.0f} MB").pack(fill="x")
        tk.Label(info, font=("Microsoft YaHei UI", 9), fg="#666666", anchor="w",
                 text=f"全部书库合计：{all_mb:,.0f} MB").pack(fill="x")

        row = ttk.Frame(self, padding=(14, 10, 14, 12))
        row.pack(fill="x", side="bottom")
        ttk.Button(row, text="取消", command=self._cancel).pack(side="right")
        ttk.Button(row, text=f"清除全部（{all_mb:,.0f} MB）",
                   command=lambda: self._pick("all")).pack(side="right", padx=8)
        ttk.Button(row, text=f"只清本库（{cur_mb:,.0f} MB）", style="Danger.TButton",
                   command=lambda: self._pick("cur")).pack(side="right")

        self.bind("<Escape>", lambda _e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.grab_set()
        self.focus_set()

    def _pick(self, which: str) -> None:
        self.choice = which
        self.destroy()

    def _cancel(self) -> None:
        self.choice = ""
        self.destroy()


# ---------------------------------------------------------------- 主窗口


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        _dpi_fix()
        self.title(APP_TITLE)
        self.geometry("1480x940")
        self.minsize(1180, 740)

        self.result = None
        self._q: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None

        self._groups: list = []              # 当前显示的组
        self._group_index: dict[str, int] = {}
        self._pair_index: dict[str, tuple] = {}
        self._seg_index: dict[str, object] = {}
        self._member_index: dict[str, int] = {}
        self._bid_group: dict[int, object] = {}

        self._checked: set[int] = set()      # 勾选待删的书 id
        self._deleting = False
        self._ev_pending: set[tuple] = set() # 正在按需计算证据的书对
        self._t2s_used = False

        self._text_cache: dict[int, str] = {}   # 按需读取的原文（最多留 6 本）
        self._cur_pair: tuple[int, int] | None = None
        self._last_dir = self._load_last_dir()
        self._scanning = False
        self._t0 = 0.0
        self._phase = ""
        self._done = 0
        self._total = 0
        self._detail = ""
        self._log_visible = True

        self._setup_logging()
        self._build_style()
        self._build_ui()
        self.after(100, self._poll)
        self.after(300, self._tick)

        # 两个仅供自动化测试用的环境变量（正常使用不会设置）
        auto = os.environ.get("ND_GUI_AUTOSTART")
        if auto:
            self.var_dir.set(auto)
            self.after(400, self.start)
        quit_after = os.environ.get("ND_GUI_AUTOQUIT")
        if quit_after:
            self.after(int(float(quit_after) * 1000), self.destroy)

    # ------------------------------------------------------------ 日志

    def _setup_logging(self) -> None:
        self.log_path = core.data_dir() / "run.log"
        self.logger = core.setup_logging(False, logfile=self.log_path)
        h = _QueueLogHandler(self._q)
        h.setLevel(logging.INFO)
        self.logger.addHandler(h)
        self.logger.info("界面启动 v%s，日志文件：%s", __version__, self.log_path)

    def _append_log(self, text: str, level: str = "INFO") -> None:
        if not hasattr(self, "txt_log"):
            return
        t = self.txt_log
        t.configure(state="normal")
        tag = "err" if level in ("ERROR", "CRITICAL") else ("warn" if level == "WARNING" else "info")
        t.insert("end", text.rstrip() + "\n", (tag,))
        lines = int(t.index("end-1c").split(".")[0])
        if lines > MAX_LOG_LINES:
            t.delete("1.0", f"{lines - MAX_LOG_LINES}.0")
        t.see("end")
        t.configure(state="disabled")

    # ------------------------------------------------------------ 样式

    def _build_style(self) -> None:
        st = ttk.Style(self)
        for name in ("vista", "clam", "default"):
            try:
                st.theme_use(name)
                break
            except tk.TclError:
                continue
        family = "Microsoft YaHei UI"
        st.configure(".", font=(family, 9))
        st.configure("Treeview", rowheight=23, font=(family, 9))
        st.configure("Treeview.Heading", font=(family, 9, "bold"))
        st.configure("Big.TButton", font=(family, 10))
        st.configure("Danger.TButton", font=(family, 10, "bold"), foreground="#b3261e")
        st.configure("Muted.TLabel", foreground="#666666")
        st.configure("Phase.TLabel", font=(family, 10, "bold"), foreground="#1a5fb4")
        st.configure("Pane.TLabel", font=(family, 9, "bold"), foreground="#333333")
        st.configure("Log.TButton", font=(family, 8))

    # ------------------------------------------------------------ 布局

    def _build_ui(self) -> None:
        # ---- 工具栏
        top = ttk.Frame(self)
        top.pack(fill="x", padx=6, pady=4)
        ttk.Label(top, text="小说目录：").pack(side="left")
        self.var_dir = tk.StringVar(value=self._last_dir)
        ttk.Entry(top, textvariable=self.var_dir).pack(side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Button(top, text="浏览…", command=self.browse).pack(side="left", padx=2)

        ttk.Label(top, text="档位").pack(side="left", padx=(12, 2))
        self.var_preset = tk.StringVar(value="自动")
        ttk.Combobox(top, textvariable=self.var_preset, width=8, state="readonly",
                     values=["自动"] + list(core.PRESETS)).pack(side="left")

        self.var_t2s = tk.BooleanVar(value=False)
        self.chk_t2s = ttk.Checkbutton(top, text="繁简视为同一本", variable=self.var_t2s,
                                       command=self._on_toggle_t2s)
        self.chk_t2s.pack(side="left", padx=(8, 0))
        if not self._t2s_ok():
            self.chk_t2s.configure(state="disabled")

        self.btn_start = ttk.Button(top, text="开始扫描", style="Big.TButton", command=self.start)
        self.btn_start.pack(side="left", padx=(10, 2))
        ttk.Button(top, text="自检", command=self.selftest_dialog).pack(side="left", padx=2)
        ttk.Button(top, text="打开结果…", command=self.open_result).pack(side="left", padx=(8, 2))

        # ---- 状态栏
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=6)
        self.pb = ttk.Progressbar(bar, mode="determinate", maximum=100, length=240)
        self.pb.pack(side="left")
        self.var_phase = tk.StringVar(value="空闲")
        ttk.Label(bar, textvariable=self.var_phase, style="Phase.TLabel").pack(
            side="left", padx=(10, 10))
        self.var_detail = tk.StringVar(
            value="选择一个目录，然后点「开始扫描」。首次扫描会建缓存，之后再扫很快。")
        ttk.Label(bar, textvariable=self.var_detail, style="Muted.TLabel").pack(
            side="left", fill="x", expand=True)

        # ---- 主体：三栏
        main = ttk.Panedwindow(self, orient="horizontal")
        main.pack(fill="both", expand=True, padx=6, pady=(6, 0))
        self.main_panes = main

        # 左：分组
        left = ttk.Frame(main)
        head = ttk.Frame(left)
        head.pack(fill="x")
        ttk.Label(head, text="重复分组", style="Pane.TLabel").pack(side="left")
        self.var_counts = tk.StringVar(value="")
        ttk.Label(head, textvariable=self.var_counts, style="Muted.TLabel").pack(side="right")
        filt = ttk.Frame(left)
        filt.pack(fill="x", pady=(3, 3))
        ttk.Label(filt, text="过滤").pack(side="left")
        self.var_filter = tk.StringVar()
        e = ttk.Entry(filt, textvariable=self.var_filter)
        e.pack(side="left", fill="x", expand=True, padx=(4, 0))
        e.bind("<KeyRelease>", lambda _e: self._populate_groups())

        gcols = ("grp", "n", "struct", "rel", "keep")
        self.gtree = ttk.Treeview(left, columns=gcols, show="headings", selectmode="browse")
        for c, (txt, w, anchor) in {
            "grp": ("组", 46, "center"),
            "n": ("本", 40, "center"),
            "struct": ("结构", 74, "center"),
            "rel": ("关系", 132, "w"),
            "keep": ("建议保留", 190, "w"),
        }.items():
            self.gtree.heading(c, text=txt)
            self.gtree.column(c, width=w, anchor=anchor, stretch=(c == "keep"))
        gys = ttk.Scrollbar(left, orient="vertical", command=self.gtree.yview)
        self.gtree.configure(yscrollcommand=gys.set)
        self.gtree.pack(side="left", fill="both", expand=True)
        gys.pack(side="right", fill="y")
        self.gtree.bind("<<TreeviewSelect>>", self.on_pick_group)
        main.add(left, weight=3)

        # 中：成员 / 书对 / 匹配段（竖排，可拖动）
        mid = ttk.Panedwindow(main, orient="vertical")

        # 中上：组成员（勾选）
        mframe = ttk.Frame(mid)
        mh = ttk.Frame(mframe)
        mh.pack(fill="x")
        ttk.Label(mh, text="组成员（点 ☐ 勾选要删的）", style="Pane.TLabel").pack(side="left")
        self.var_group_info = tk.StringVar(value="")
        ttk.Label(mh, textvariable=self.var_group_info, style="Muted.TLabel").pack(side="right")
        # 合订-分卷结构图（只有这种组才显示，见 _draw_structure）
        self.struct_canvas = tk.Canvas(mframe, height=132, bg="#ffffff",
                                       highlightthickness=1, highlightbackground="#d8d8d8")
        self.var_advice = tk.StringVar(value="")
        self.lbl_advice = tk.Label(mframe, textvariable=self.var_advice, justify="left",
                                   anchor="w", font=("Microsoft YaHei UI", 8),
                                   fg="#1a5fb4", wraplength=520)

        self._tree_holder = ttk.Frame(mframe)
        self._tree_holder.pack(fill="both", expand=True)
        mcols = ("chk", "keep", "name", "ch", "cjk", "ad", "enc")
        self.mtree = ttk.Treeview(self._tree_holder, columns=mcols, show="headings", height=6,
                                  selectmode="browse")
        for c, (txt, w, anchor) in {
            "chk": ("", 28, "center"),
            "keep": ("保留", 48, "center"),
            "name": ("文件名", 300, "w"),
            "ch": ("章节", 56, "center"),
            "cjk": ("汉字", 68, "center"),
            "ad": ("广告", 46, "center"),
            "enc": ("编码", 72, "center"),
        }.items():
            self.mtree.heading(c, text=txt)
            self.mtree.column(c, width=w, anchor=anchor, stretch=(c == "name"))
        self.mtree.tag_configure("keep", background="#eaf6ea")
        self.mtree.tag_configure("checked", background="#ffecec", foreground="#8a1c1c")
        mys = ttk.Scrollbar(self._tree_holder, orient="vertical", command=self.mtree.yview)
        self.mtree.configure(yscrollcommand=mys.set)
        self.mtree.pack(side="left", fill="both", expand=True)
        mys.pack(side="right", fill="y")
        self.mtree.bind("<Button-1>", self._on_member_click)
        self.mtree.bind("<space>", lambda _e: self._toggle_selected_member())
        mid.add(mframe, weight=5)

        # 中中：书对
        pframe = ttk.Frame(mid)
        ttk.Label(pframe, text="组内书对（点一行看证据）", style="Pane.TLabel").pack(anchor="w")
        pcols = ("rel", "a", "b", "jac", "cov")
        self.ptree = ttk.Treeview(pframe, columns=pcols, show="headings", height=5,
                                  selectmode="browse")
        for c, (txt, w, anchor) in {
            "rel": ("关系", 120, "w"),
            "a": ("文件 A", 175, "w"),
            "b": ("文件 B", 175, "w"),
            "jac": ("Jaccard", 62, "center"),
            "cov": ("覆盖A/B", 76, "center"),
        }.items():
            self.ptree.heading(c, text=txt)
            self.ptree.column(c, width=w, anchor=anchor, stretch=(c in ("a", "b")))
        pys = ttk.Scrollbar(pframe, orient="vertical", command=self.ptree.yview)
        self.ptree.configure(yscrollcommand=pys.set)
        self.ptree.pack(side="left", fill="both", expand=True)
        pys.pack(side="right", fill="y")
        self.ptree.bind("<<TreeviewSelect>>", self.on_pick_pair)
        mid.add(pframe, weight=4)

        # 中下：匹配段
        sframe = ttk.Frame(mid)
        ttk.Label(sframe, text="匹配段（点一行定位到左右原文）", style="Pane.TLabel").pack(anchor="w")
        scols = ("span", "chars")
        self.stree = ttk.Treeview(sframe, columns=scols, show="headings", height=4,
                                  selectmode="browse")
        self.stree.heading("span", text="位置（章节 → 章节）")
        self.stree.heading("chars", text="连续字数")
        self.stree.column("span", width=270, anchor="w")
        self.stree.column("chars", width=76, anchor="center")
        sys_ = ttk.Scrollbar(sframe, orient="vertical", command=self.stree.yview)
        self.stree.configure(yscrollcommand=sys_.set)
        self.stree.pack(side="left", fill="both", expand=True)
        sys_.pack(side="right", fill="y")
        self.stree.bind("<<TreeviewSelect>>", self.on_pick_seg)
        mid.add(sframe, weight=4)
        main.add(mid, weight=4)

        # 右：A | B
        right = ttk.Panedwindow(main, orient="horizontal")
        self.var_a = tk.StringVar(value="A 原文")
        self.var_b = tk.StringVar(value="B 原文")
        for side, color, var in (("A", "#fff3b0", self.var_a), ("B", "#cfe8ff", self.var_b)):
            frame = ttk.Frame(right)
            ttk.Label(frame, textvariable=var, style="Pane.TLabel").pack(anchor="w")
            txt = tk.Text(frame, wrap="char", height=10, font=("Microsoft YaHei UI", 9),
                          spacing1=1, spacing3=1)
            txt.tag_configure("hit", background=color)
            txt.tag_configure("head", foreground="#888888")
            txt.configure(state="disabled")
            sc = ttk.Scrollbar(frame, orient="vertical", command=txt.yview)
            txt.configure(yscrollcommand=sc.set)
            txt.pack(side="left", fill="both", expand=True)
            sc.pack(side="right", fill="y")
            if side == "A":
                self.txt_a = txt
            else:
                self.txt_b = txt
            right.add(frame, weight=1)
        main.add(right, weight=6)
        self.after(200, self._set_sash)

        # ---- 删除工具条
        dbar = ttk.Frame(self)
        dbar.pack(fill="x", padx=6, pady=(6, 0))
        self.del_bar = dbar

        self.btn_del_same = ttk.Button(
            dbar, text="⚡ 一键清理「内容完全相同」（每组留 1 本）",
            style="Danger.TButton", state="disabled", command=self.on_delete_identical)
        self.btn_del_same.pack(side="left")

        self.btn_precheck = ttk.Button(
            dbar, text="全库预勾选（只勾不删）", state="disabled", command=self.on_precheck_all)
        self.btn_precheck.pack(side="left", padx=(8, 0))

        self.btn_check_group = ttk.Button(
            dbar, text="勾选本组多余项", state="disabled", command=self.on_check_group)
        self.btn_check_group.pack(side="left", padx=(6, 0))

        self.btn_clear_check = ttk.Button(
            dbar, text="清空勾选", state="disabled", command=self.on_clear_checked)
        self.btn_clear_check.pack(side="left", padx=(6, 0))

        self.btn_del_checked = ttk.Button(
            dbar, text="🗑 删除勾选（移入回收站）",
            style="Danger.TButton", state="disabled", command=self.on_delete_checked)
        self.btn_del_checked.pack(side="left", padx=(18, 0))

        self.btn_clean_dirs = ttk.Button(
            dbar, text="🧹 清理空文件夹", command=self.on_clean_empty_dirs)
        self.btn_clean_dirs.pack(side="left", padx=(18, 0))

        self.var_check = tk.StringVar(value="未勾选")
        ttk.Label(dbar, textvariable=self.var_check, style="Muted.TLabel").pack(side="right")

        # ---- 运行日志
        log_head = ttk.Frame(self)
        log_head.pack(fill="x", padx=6, pady=(6, 0))
        self.btn_log = ttk.Button(log_head, text="▾ 运行日志", style="Log.TButton",
                                  command=self.toggle_log)
        self.btn_log.pack(side="left")
        ttk.Button(log_head, text="打开日志文件", style="Log.TButton",
                   command=self.open_logfile).pack(side="left", padx=6)
        ttk.Button(log_head, text="清空", style="Log.TButton", command=self.clear_log).pack(side="left")

        self.log_frame = ttk.Frame(self)
        self.log_frame.pack(fill="both", padx=6)
        self.txt_log = tk.Text(self.log_frame, height=4, wrap="none",
                               font=("Consolas", 9), background="#fbfbfb")
        self.txt_log.tag_configure("info", foreground="#333333")
        self.txt_log.tag_configure("warn", foreground="#a15c00")
        self.txt_log.tag_configure("err", foreground="#b3261e")
        lys = ttk.Scrollbar(self.log_frame, orient="vertical", command=self.txt_log.yview)
        lxs = ttk.Scrollbar(self.log_frame, orient="horizontal", command=self.txt_log.xview)
        self.txt_log.configure(yscrollcommand=lys.set, xscrollcommand=lxs.set)
        self.txt_log.grid(row=0, column=0, sticky="nsew")
        lys.grid(row=0, column=1, sticky="ns")
        lxs.grid(row=1, column=0, sticky="ew")
        self.log_frame.rowconfigure(0, weight=1)
        self.log_frame.columnconfigure(0, weight=1)
        self.txt_log.configure(state="disabled")

        # ---- 底部
        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=6, pady=6)
        self.bottom_bar = bottom
        self.btn_csv = ttk.Button(bottom, text="导出 CSV", state="disabled",
                                  command=lambda: self.export("csv"))
        self.btn_csv.pack(side="left")
        self.btn_json = ttk.Button(bottom, text="导出 JSON", state="disabled",
                                   command=lambda: self.export("json"))
        self.btn_json.pack(side="left", padx=6)
        ttk.Button(bottom, text="复制 A/B 路径", command=self.copy_path).pack(side="left", padx=6)
        ttk.Button(bottom, text="打开所在文件夹", command=self.open_folder).pack(side="left")
        ttk.Button(bottom, text="指纹缓存位置", command=self.open_cache_dir).pack(side="left", padx=(6, 0))
        self.btn_clear_cache = ttk.Button(bottom, text="清除缓存", command=self.on_clear_cache)
        self.btn_clear_cache.pack(side="left", padx=(6, 0))
        self.var_info = tk.StringVar(value="")
        ttk.Label(bottom, textvariable=self.var_info, style="Muted.TLabel").pack(side="right")

    def _set_sash(self) -> None:
        try:
            w = self.winfo_width()
            self.main_panes.sashpos(0, 380)
            self.main_panes.sashpos(1, min(380 + 470, w - 320))
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ 小工具

    def toggle_log(self) -> None:
        self._log_visible = not self._log_visible
        if self._log_visible:
            self.log_frame.pack(fill="both", padx=6, before=self.bottom_bar)
            self.btn_log.configure(text="▾ 运行日志")
        else:
            self.log_frame.pack_forget()
            self.btn_log.configure(text="▸ 运行日志")

    def clear_log(self) -> None:
        self.txt_log.configure(state="normal")
        self.txt_log.delete("1.0", "end")
        self.txt_log.configure(state="disabled")

    def open_logfile(self) -> None:
        try:
            os.startfile(str(self.log_path))  # noqa: S606
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("打开失败", str(exc))

    def _load_last_dir(self) -> str:
        try:
            cfg = core.data_dir() / "ui.json"
            if cfg.exists():
                return json.loads(cfg.read_text(encoding="utf-8")).get("last_dir", "")
        except Exception:
            pass
        return ""

    def _save_last_dir(self, d: str) -> None:
        try:
            (core.data_dir() / "ui.json").write_text(
                json.dumps({"last_dir": d}, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def _book_text(self, bid: int) -> str:
        """按需读取整本原文（只读，最多缓存 6 本）。

        扫描期不缓存原文——1.7 万对重复的片段能吃掉几 GB 内存。
        这里每次点开一个匹配段才读一次，同一本书的后续点击直接命中缓存。

        ⚠ 必须走 `formats.extract`，不能直接 `raw.decode()`：
          证据段的 orig_a/orig_b 是**格式层提取出的纯文本**里的偏移。
          直接解码原始字节时，docx/epub/pdf 拿到的是 zip/PDF 二进制，
          html 拿到的是带标签的源码 —— 界面会显示乱码，位置也是错的。
        """
        if bid in self._text_cache:
            return self._text_cache[bid]
        b = self.result.books.get(bid) if self.result else None
        if not b:
            return ""
        try:
            text = formats.extract(Path(b.path)).text
        except formats.UnsupportedFormat as exc:
            self._append_log(f"读取原文失败：{b.path}（{exc}）", "WARNING")
            return ""
        except Exception as exc:  # noqa: BLE001 —— 单本读不出来不能拖垮界面
            self._append_log(f"读取原文失败：{b.path}"
                             f"（{type(exc).__name__}: {exc}）", "WARNING")
            return ""
        if len(self._text_cache) >= 6:
            self._text_cache.pop(next(iter(self._text_cache)))
        self._text_cache[bid] = text
        return text

    def _excerpt(self, bid: int, orig: int, pad: int = 60, take: int = 320) -> tuple[str, int]:
        """取 orig 附近的一段原文，返回 (片段, 匹配起点在片段内的下标)。"""
        text = self._book_text(bid)
        if not text:
            return "", 0
        start = max(0, orig - pad)
        return text[start : orig + take], orig - start

    def _set_text(self, widget: tk.Text, head: str, body: str, mark: int) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", head, ("head",))
        base = len(head)
        widget.insert("end", body)
        if body:
            widget.tag_add("hit", f"1.0+{base + mark}c", "end")
            widget.see(f"1.0+{base + mark}c")
        widget.configure(state="disabled")

    # ------------------------------------------------------------ 状态

    def _render_status(self) -> None:
        elapsed = time.perf_counter() - self._t0 if self._scanning and self._t0 else 0.0
        if self._scanning and self._total > 0:
            base, span = PHASE_SPAN.get(self._phase, (0, 100))
            frac = min(1.0, self._done / max(1, self._total))
            self.pb.stop()
            self.pb.configure(mode="determinate", value=min(100, base + span * frac))
            self.var_phase.set(f"{self._phase}  {self._done}/{self._total}   {elapsed:,.0f}s")
        elif self._scanning:
            self.var_phase.set(f"{self._phase}   {elapsed:,.0f}s")
        else:
            self.var_phase.set(self._phase or "空闲")
        self.var_detail.set(self._detail)

    def _tick(self) -> None:
        try:
            if self._scanning:
                self._render_status()
        except Exception:  # noqa: BLE001
            pass
        finally:
            self.after(300, self._tick)

    # ------------------------------------------------------------ 动作

    def open_result(self) -> None:
        """导入上次导出的 JSON：重新分组，不重新扫描。

        为什么要有：一个 1.7 万本的库扫一遍 40 分钟。改了判据、或者只想换个
        角度看结果，不该再等 40 分钟。导出文件里有每对书的完整指标，
        足够用当前判据重判 + 重新分组（见 loader.py 的说明）。
        """
        if self._scanning or self._deleting:
            return
        path = filedialog.askopenfilename(
            title="打开查重结果", filetypes=[("查重结果 JSON", "*.json"), ("所有文件", "*.*")])
        if not path:
            return
        self.btn_start.configure(state="disabled", text="导入中…")
        self._phase = "导入结果"
        self._detail = f"正在读取 {Path(path).name}…"
        self._render_status()
        self._append_log(f"— 导入结果：{path}")

        def run() -> None:
            try:
                from . import loader

                self._q.put(("loaded", loader.load_result(Path(path), log=self.logger)))
            except Exception:  # noqa: BLE001
                self._q.put(("error", traceback.format_exc()))

        threading.Thread(target=run, daemon=True).start()

    def _load_done(self, ld) -> None:
        self.btn_start.configure(state="normal", text="开始扫描")
        if not ld.ok:
            self._phase = "导入失败"
            self._detail = "；".join(ld.warnings) or "文件无法解析"
            self._render_status()
            messagebox.showerror("导入失败", "\n".join(ld.warnings) or "文件无法解析")
            return
        res = ld.result
        self._show_result(res)
        self._detail = "（导入）" + self._detail
        if getattr(ld, "missing", 0):
            self._detail += f"｜已剔除 {ld.missing:,} 个已删文件"
        self._render_status()
        self.var_info.set(self._detail)
        self._append_log(f"— 导入完成：{len(res.books):,} 本 / {len(res.groups):,} 组"
                         + (f"（剔除已删 {ld.missing:,} 个）" if getattr(ld, "missing", 0) else ""))
        if ld.warnings:
            messagebox.showwarning("导入提醒", "\n\n".join(ld.warnings))

    def on_clear_cache(self) -> None:
        """清除缓存：删完重复文件后回收空间，或强制下次重算。"""
        if self._scanning or self._deleting:
            return
        raw = self.var_dir.get().strip().strip('"')
        root = Path(raw) if raw else (self.result.root if self.result else None)
        if not root or not root.is_dir():
            messagebox.showwarning("先选目录", "请先在顶部选择小说目录。")
            return
        cur_mb = core.dir_size_mb(core.cache_dir_for(root))
        all_mb = deleter.cache_total_mb()
        # 判"有没有缓存"不能看体积：小书库的缓存可能只有几十 KB，
        # 用 `<= 0.05 MB` 会把它们误判成"没有缓存"。
        base = core.data_dir() / "cache"
        has_any = base.exists() and any(base.iterdir())
        if not has_any:
            messagebox.showinfo("没有缓存", "目前没有缓存数据（可能还没扫描过，或已经清过了）。")
            return
        dlg = ClearCacheDialog(self, cur_mb, all_mb, root.name)
        self.wait_window(dlg)
        if not dlg.choice:
            return

        everything = dlg.choice == "all"
        self._phase = "清除缓存"
        self._detail = "正在删除缓存文件…"
        self._render_status()
        self.btn_clear_cache.configure(state="disabled")

        def run() -> None:
            try:
                n, freed = deleter.clear_cache(
                    None if everything else root, everything=everything)
                self._q.put(("cache_cleared", n, freed, everything))
            except Exception:  # noqa: BLE001
                self._q.put(("error", traceback.format_exc()))

        threading.Thread(target=run, daemon=True).start()

    def _cache_cleared(self, n: int, freed_mb: float, everything: bool) -> None:
        self.btn_clear_cache.configure(state="normal")
        self._phase = "完成"
        self._detail = (f"已清除缓存：{n} 个书库、{freed_mb:,.0f} MB"
                        if everything else f"已清除本库缓存：{freed_mb:,.0f} MB")
        self._render_status()
        self.var_info.set(self._detail)
        self._append_log(f"— {self._detail}（下次扫描将重新计算）")
        if self.result is not None:
            self._append_log("  提示：当前结果仍在内存里，可以继续浏览/导出；"
                             "但下次扫描会全量重算。")
        messagebox.showinfo(
            "缓存已清除",
            f"{self._detail}\n\n"
            f"你的书没有受到任何影响。\n"
            f"下次扫描会重新计算（大库需要较长时间）。")

    def open_cache_dir(self) -> None:
        """打开指纹缓存目录 —— 指纹是真的存了，只是没塞进导出文件。"""
        try:
            root = Path(self.var_dir.get().strip().strip('"')) if self.var_dir.get() else Path(".")
            d = core.cache_dir_for(root) if root.exists() else core.data_dir() / "cache"
            self._append_log(f"— 指纹缓存：{d}（{core.dir_size_mb(d):,.0f} MB）")
            os.startfile(str(d))  # noqa: S606
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("打不开", str(exc))

    @staticmethod
    def _t2s_ok() -> bool:
        try:
            from .normalize import t2s_available

            return t2s_available()
        except Exception:  # noqa: BLE001
            return False

    def _on_toggle_t2s(self) -> None:
        """繁简开关：改的是归一化规则，缓存必须全部重算。"""
        if self.var_t2s.get():
            self._append_log("— 已开启「繁简视为同一本」：归一化规则变更，"
                             "下次扫描会全部重算（缓存自动失效）", "WARNING")
            self._detail = "已开启繁简转换：下次扫描将逐本重算（不能复用旧缓存）"
            self._render_status()
        else:
            self._append_log("— 已关闭「繁简视为同一本」")

    def browse(self) -> None:
        d = filedialog.askdirectory(initialdir=self.var_dir.get() or None, title="选择小说所在目录")
        if d:
            self.var_dir.set(d)

    def start(self) -> None:
        if (self._worker and self._worker.is_alive()) or self._deleting:
            return
        root = Path(self.var_dir.get().strip().strip('"'))
        if not root.is_dir():
            messagebox.showwarning("目录不存在", f"找不到这个目录：\n{root}")
            return
        self._save_last_dir(str(root))
        for t in (self.gtree, self.mtree, self.ptree, self.stree):
            t.delete(*t.get_children())
        self._group_index.clear()
        self._pair_index.clear()
        self._seg_index.clear()
        self._member_index.clear()
        self._bid_group.clear()
        self._checked.clear()
        self._text_cache.clear()
        self._ev_pending.clear()
        self._cur_pair = None
        self._set_text(self.txt_a, "", "", 0)
        self._set_text(self.txt_b, "", "", 0)
        self.btn_start.configure(state="disabled", text="扫描中…")
        self.btn_clean_dirs.configure(state="disabled")
        self.btn_clear_cache.configure(state="disabled")
        self._set_result_buttons(False)
        self.var_info.set("")
        self.var_group_info.set("")
        self._refresh_check_summary()

        self._scanning = True
        self._t0 = time.perf_counter()
        self._phase, self._done, self._total = "准备扫描", 0, 0
        self._detail = f"正在枚举文件：{root}"
        self.pb.configure(mode="indeterminate", value=0)
        self.pb.start(18)
        self._render_status()
        self._append_log(f"— 开始扫描：{root}")

        preset = self.var_preset.get()
        self._t2s_used = bool(self.var_t2s.get())
        self._worker = threading.Thread(
            target=self._run_scan, args=(root, None if preset == "自动" else preset), daemon=True)
        self._worker.start()

    def _run_scan(self, root: Path, preset: str | None) -> None:
        try:
            from . import engine

            def prog(phase: str, done: int = 0, total: int = 0, msg: str = "") -> None:
                self._q.put(("prog", phase, done, total, msg))

            res = engine.scan(root, preset=preset, log=self.logger, progress=prog,
                              t2s=self._t2s_used)
            self._q.put(("done", res))
        except Exception:  # noqa: BLE001
            self._q.put(("error", traceback.format_exc()))

    def _poll(self) -> None:
        try:
            while True:
                try:
                    item = self._q.get_nowait()
                except queue.Empty:
                    break
                try:
                    self._handle(item)
                except Exception:  # noqa: BLE001
                    self._append_log("界面更新出错：\n" + traceback.format_exc(limit=3), "ERROR")
        except Exception:  # noqa: BLE001
            pass
        finally:
            self.after(100, self._poll)

    def _handle(self, item: tuple) -> None:
        kind = item[0]
        if kind == "log":
            self._append_log(item[1], item[2])
        elif kind == "prog":
            _, phase, done, total, msg = item
            self._phase, self._done, self._total = phase, done, total
            if msg:
                self._detail = msg
            self._render_status()
        elif kind == "done":
            self._show_result(item[1])
        elif kind == "selftest":
            self._selftest_done(item[1], item[2], item[3])
        elif kind == "del_done":
            self._delete_done(item[1], item[2])
        elif kind == "cache_cleared":
            self._cache_cleared(item[1], item[2], item[3])
        elif kind == "empty_dirs":
            self._empty_dirs_done(item[1], item[2], item[3])
        elif kind == "dirs_done":
            self._dirs_done(item[1], item[2], item[3] if len(item) > 3 else 0)
        elif kind == "loaded":
            self._load_done(item[1])
        elif kind == "evidence":
            _, key, segs = item
            self._ev_pending.discard(key)
            if self.result and self._cur_pair == key:
                self._fill_segments(key, segs)
        elif kind == "error":
            # ★ 必须同时复位 _deleting：删除/清空目录线程抛异常时只回来了 error，
            #   没有 del_done/dirs_done。若 _deleting 停在 True，后续 start()、
            #   删除、导入、清缓存全部会被"提前 return"拦住，界面看起来像死了。
            self._scanning = False
            self._deleting = False
            self.pb.stop()
            self.pb.configure(mode="determinate", value=0)
            self.btn_start.configure(state="normal", text="开始扫描")
            self.btn_clean_dirs.configure(state="normal")
            self.btn_clear_cache.configure(state="normal")
            if self.result is not None:
                self._set_result_buttons(True)
            else:
                self._refresh_check_summary()
            self._phase = "出错了"
            self._detail = "详细堆栈见下方「运行日志」"
            self._render_status()
            self._append_log(item[1], "ERROR")
            messagebox.showerror("出错了", item[1][-1500:])

    # ------------------------------------------------------------ 结果

    def _set_result_buttons(self, on: bool) -> None:
        st = "normal" if on else "disabled"
        for b in (self.btn_csv, self.btn_json, self.btn_del_same,
                  self.btn_precheck, self.btn_check_group, self.btn_clear_check):
            b.configure(state=st)
        self._refresh_check_summary()

    def _show_result(self, res) -> None:
        self.result = res
        self._scanning = False
        # 按需重算证据时要与这份结果用同一套归一化规则（导入的结果也有自己的 t2s）
        self._t2s_used = bool(getattr(res, "t2s", self._t2s_used))
        self.pb.stop()
        self.btn_start.configure(state="normal", text="开始扫描")
        self.btn_clean_dirs.configure(state="normal")
        self.btn_clear_cache.configure(state="normal")
        self._set_result_buttons(True)
        self._phase = "完成"
        n_pairs = len(res.dup_pairs)
        sus = len(res.suspects)
        self._detail = (
            f"{len(res.books):,} 本 / 汉字 {res.total_chars/1e4:,.0f} 万"
            f"（含标点 {res.total_word_chars/1e4:,.0f} 万） / "
            f"{len(res.groups):,} 组 / {n_pairs:,} 对 / {res.elapsed:.0f}s"
            + (f" / 峰值 {res.peak_mb:,.0f} MB" if res.peak_mb else "")
            + (f"　⚠ {sus} 本需人工确认" if sus else ""))
        self.pb.configure(mode="determinate", value=100 if res.groups else 0)
        self._render_status()
        self.var_info.set(self._detail)
        self._append_log(f"— 扫描完成：{self._detail}")

        self._checked.clear()
        self._bid_group = {}
        for g in res.groups:
            for m in g.members:
                self._bid_group[m] = g

        self._populate_groups()
        kids = self.gtree.get_children()
        if kids:
            self.gtree.selection_set(kids[0])
            self.on_pick_group()
        if n_pairs == 0:
            messagebox.showinfo("扫描完成", "没有发现重复。")

    def _populate_groups(self) -> None:
        """按过滤词重建分组列表（只建分组行，书对用到时才建）。"""
        if not self.result:
            return
        keep_sel = self.gtree.selection()
        keep_gi = self._group_index.get(keep_sel[0]) if keep_sel else None

        kw = self.var_filter.get().strip().lower()
        self.gtree.delete(*self.gtree.get_children())
        self._group_index.clear()
        self._groups = []
        names = {bid: b.name for bid, b in self.result.books.items()}
        shown = 0
        for gi, g in enumerate(self.result.groups, 1):
            keep = names.get(g.keep_id, "")
            top = max(g.pairs, key=lambda s: s.jac) if g.pairs else None
            rel = top.rel if top else ""
            if kw:
                blob = " ".join([keep, rel] + [names.get(m, "") for m in g.members]).lower()
                if kw not in blob:
                    continue
            iid = self.gtree.insert("", "end", values=(
                gi, g.size, g.structure, rel, keep))
            self._group_index[iid] = gi - 1
            shown += 1
        self.var_counts.set(f"{shown:,}/{len(self.result.groups):,} 组")

        if keep_gi is not None:
            for iid, idx in self._group_index.items():
                if idx == keep_gi:
                    self.gtree.selection_set(iid)
                    self.gtree.see(iid)
                    break

    def on_pick_group(self, _evt=None) -> None:
        sel = self.gtree.selection()
        if not sel or not self.result:
            return
        g = self.result.groups[self._group_index[sel[0]]]
        self._populate_members(g)
        self.ptree.delete(*self.ptree.get_children())
        self.stree.delete(*self.stree.get_children())
        self._pair_index.clear()
        self._seg_index.clear()
        names = {bid: b.name for bid, b in self.result.books.items()}
        for st in sorted(g.pairs, key=lambda s: -s.jac):
            iid = self.ptree.insert("", "end", values=(
                st.rel, names.get(st.a, ""), names.get(st.b, ""),
                f"{st.jac:.3f}", f"{st.cov_a:.2f}/{st.cov_b:.2f}"))
            self._pair_index[iid] = (st.a, st.b)
        kids = self.ptree.get_children()
        if kids:
            self.ptree.selection_set(kids[0])
            self.on_pick_pair()
        else:
            self._set_text(self.txt_a, "（该组没有可显示的书对）", "", 0)
            self._set_text(self.txt_b, "", "", 0)
        self._refresh_check_summary()

    def _populate_members(self, g) -> None:
        """组内成员列表（带勾选）。★保留 = 建议留下的那本。"""
        self.mtree.delete(*self.mtree.get_children())
        self._member_index.clear()
        for m in g.members:
            b = self.result.books.get(m)
            if not b:
                continue
            checked = m in self._checked
            iid = self.mtree.insert("", "end", values=(
                "☑" if checked else "☐",
                "★" if m == g.keep_id else "",
                b.name,
                b.chapters or "-",
                f"{b.norm_chars/1e4:,.0f}万",
                b.ad_lines,
                b.enc or "-"))
            self.mtree.item(iid, tags=("checked",) if checked else
                            (("keep",) if m == g.keep_id else ()))
            self._member_index[iid] = m
        keep = self.result.books.get(g.keep_id)
        info = f"本组 {g.size} 本"
        if keep:
            info += f"｜建议保留：{keep.name}"
        self.var_group_info.set(info)
        self._draw_structure(g)

    # ------------------------------------------------------------ 结构图

    def _draw_structure(self, g) -> None:
        """合订-分卷组：画"全集在上、分卷在下"的结构图（计划 §3.6）。

        为什么要画：这种组的成员不是"互为副本"，而是"整体 ← 分卷"。
        列成一张表只会让人以为 4 个文件都是重复品；
        画出覆盖关系，才看得懂"删掉全集就少了分卷目录，删掉分卷可能少一点内容"。
        """
        from . import judge as judge_mod

        c = self.struct_canvas
        from .group import STRUCT_OMNIBUS

        if not self.result or g.structure != STRUCT_OMNIBUS or not g.superset_id:
            c.pack_forget()
            self.lbl_advice.pack_forget()
            self.var_advice.set("")
            return

        if not c.winfo_ismapped():
            c.pack(fill="x", pady=(3, 3), before=self._tree_holder)
            self.lbl_advice.pack(fill="x", pady=(0, 3), before=self._tree_holder)

        c.update_idletasks()
        W = max(360, c.winfo_width())
        H = 132
        c.delete("all")
        books = self.result.books
        sup = books.get(g.superset_id)
        if not sup:
            return

        def short(name: str, n: int = 18) -> str:
            return name if len(name) <= n else name[: n - 1] + "…"

        def sz(n: int) -> str:
            return f"{n / 2 ** 20:,.1f}MB" if n >= 2 ** 20 else f"{n / 1024:,.0f}KB"

        # 全集
        bw, bh = int(W * 0.56), 34
        x0 = (W - bw) // 2
        c.create_rectangle(x0, 8, x0 + bw, 8 + bh, fill="#e8f0fe", outline="#1a5fb4", width=2)
        c.create_text(x0 + bw // 2, 8 + bh // 2,
                      text=f"全集  {short(sup.name, 22)}   {sup.chapters} 章   {sz(sup.size)}",
                      font=("Microsoft YaHei UI", 9, "bold"), fill="#123a6b")
        c.create_text(x0 + bw // 2, 8 + bh + 10,
                      text=f"由 {len(g.volumes)} 个分卷组成，分卷合计覆盖 {g.volumes_cover:.0%}",
                      font=("Microsoft YaHei UI", 8), fill="#666666")

        # 分卷
        vols = [(books.get(v), cov) for v, cov in g.volumes]
        vols = [(b, cov) for b, cov in vols if b]
        n = max(1, len(vols))
        gap = 8
        vw = max(80, min(150, (W - gap * (n + 1)) // n))
        total = n * vw + gap * (n - 1)
        vx = (W - total) // 2
        vy = 74
        vh = 40
        for i, (b, cov) in enumerate(vols):
            bx = vx + i * (vw + gap)
            c.create_line(x0 + bw // 2, 8 + bh + 18, bx + vw // 2, vy,
                          fill="#9bb7d4", width=1)
            c.create_rectangle(bx, vy, bx + vw, vy + vh, fill="#f7f7f7", outline="#9a9a9a")
            c.create_text(bx + vw // 2, vy + 12, text=short(b.name, 12),
                          font=("Microsoft YaHei UI", 8), fill="#333333")
            c.create_text(bx + vw // 2, vy + 28, text=f"覆盖 {cov:.0%}  {sz(b.size)}",
                          font=("Microsoft YaHei UI", 7), fill="#777777")

        a, b2 = judge_mod.omnibus_advice(g, books)
        self.var_advice.set(f"二选一：① {a}\n　　　　② {b2}")

    # ------------------------------------------------------------ 勾选

    def _on_member_click(self, evt) -> str | None:
        """只有点第一列（☐）才切换勾选，避免误触。"""
        iid = self.mtree.identify_row(evt.y)
        if not iid:
            return None
        if self.mtree.identify_column(evt.x) != "#1":
            return None
        self._toggle_member(iid)
        return "break"

    def _toggle_selected_member(self) -> None:
        sel = self.mtree.selection()
        if sel:
            self._toggle_member(sel[0])

    def _toggle_member(self, iid: str) -> None:
        bid = self._member_index.get(iid)
        if bid is None:
            return
        if bid in self._checked:
            self._checked.discard(bid)
        else:
            self._checked.add(bid)
        self._sync_member_row(iid, bid)

    def _sync_member_row(self, iid: str, bid: int) -> None:
        checked = bid in self._checked
        self.mtree.set(iid, "chk", "☑" if checked else "☐")
        g = self._bid_group.get(bid)
        is_keep = bool(g and g.keep_id == bid)
        self.mtree.item(iid, tags=("checked",) if checked else (("keep",) if is_keep else ()))
        self._refresh_check_summary()

    def _refresh_check_summary(self) -> None:
        """更新「已勾选 N 本」+ 启用/禁用删除按钮。"""
        n = 0
        size = 0
        if self.result:
            for bid in self._checked:
                b = self.result.books.get(bid)
                if b:
                    n += 1
                    size += b.size
        if n:
            self.var_check.set(f"已勾选 {n:,} 本（{_mb(size)}）")
        else:
            self.var_check.set("未勾选")
        can = bool(n) and not self._deleting and not self._scanning
        self.btn_del_checked.configure(state="normal" if can else "disabled")

    def on_check_group(self) -> None:
        """勾选本组除「建议保留」外的所有成员（只勾选，不删）。"""
        sel = self.gtree.selection()
        if not sel or not self.result:
            return
        g = self.result.groups[self._group_index[sel[0]]]
        added = 0
        for m in g.members:
            if m == g.keep_id or m in self._checked:
                continue
            self._checked.add(m)
            added += 1
        for iid, bid in self._member_index.items():
            self._sync_member_row(iid, bid)
        self._append_log(f"— 已勾选本组 {added} 本（保留《"
                         f"{self.result.books[g.keep_id].name if g.keep_id in self.result.books else ''}》）")

    def on_clear_checked(self) -> None:
        self._checked.clear()
        for iid, bid in list(self._member_index.items()):
            self._sync_member_row(iid, bid)
        self._append_log("— 已清空勾选")

    def on_precheck_all(self) -> None:
        """全库预勾选：每组只留建议保留，其余全部勾上（**不删除**）。"""
        if not self.result:
            return
        items = plan.plan_all_but_keep(self.result.books, self.result.groups)
        if not items:
            messagebox.showinfo("没有可勾选的", "每组都只剩一本，没有多余副本。")
            return
        if not messagebox.askyesno(
                "全库预勾选",
                f"将勾选 {len(items):,} 本（每组只留「建议保留」那一本）。\n\n"
                "这一步**不会删除任何文件**，勾完之后你可以逐个复核、取消误勾的，\n"
                "再点「删除勾选」才会真正移入回收站。\n\n继续吗？"):
            return
        self._checked = {i.bid for i in items}
        for iid, bid in self._member_index.items():
            self._sync_member_row(iid, bid)
        self._append_log(f"— 全库预勾选 {len(items):,} 本（尚未删除，需复核后确认）")
        self.var_info.set(f"已预勾选 {len(items):,} 本 —— 请复核后点「删除勾选」")

    # ------------------------------------------------------------ 删除

    def _checked_items(self) -> list[plan.DeleteItem]:
        out: list[plan.DeleteItem] = []
        books = self.result.books if self.result else {}
        for bid in sorted(self._checked):
            b = books.get(bid)
            if not b:
                continue
            g = self._bid_group.get(bid)
            keep = books.get(g.keep_id) if g else None
            out.append(plan.DeleteItem(
                bid=bid, path=b.path, name=b.name, size=b.size,
                reason="手动勾选",
                keep_name=keep.name if keep and keep.id != bid else "",
                keep_id=keep.id if keep else 0))
        out.sort(key=lambda d: (-d.size, d.path))
        return out

    def on_delete_identical(self) -> None:
        """一键：内容 hash 完全相同的副本，每组 hash 簇留 1 本。"""
        if not self.result or self._deleting:
            return
        items = plan.plan_identical(self.result.books, self.result.groups)
        if not items:
            messagebox.showinfo(
                "没有可一键清理的",
                "没有「内容完全相同」的多余副本。\n\n"
                "其余重复（广告不同、缺章、修订版等）内容并不完全一致，\n"
                "请用组成员列表逐个勾选，或点「全库预勾选」后复核。")
            return
        keep_n = len({i.keep_id for i in items})
        self._confirm_and_delete(
            items, "一键清理：内容完全相同",
            f"这些书的**归一化全文 hash 完全一致**（一个字都不差），每个哈希簇只保留一本，"
            f"共保留 {keep_n:,} 本、删除 {len(items):,} 本。")

    def on_delete_checked(self) -> None:
        """删除手动勾选的书（回收站）。"""
        if not self.result or self._deleting:
            return
        items = self._checked_items()
        if not items:
            messagebox.showinfo("没有勾选", "先在组成员列表里点 ☐ 勾选要删除的文件。")
            return
        self._confirm_and_delete(
            items, "删除勾选的文件",
            "下面是你在组成员列表里勾选的文件。删除后可在系统回收站还原。")

    def on_clean_empty_dirs(self) -> None:
        """清理空文件夹：删完重复后，很多子文件夹会变空。

        两件事必须做对：
        1. 自底向上（子目录删掉后父目录才空）——`deleter.find_empty_dirs` 负责
        2. 二次确认 + 走回收站 + 不删扫描根目录 —— 复用现有删除流程
        """
        if self._scanning or self._deleting:
            return
        # 优先用"当前结果对应的目录"：屏幕上显示的是哪份结果，就在哪清理，
        # 避免目录框里还留着别的路径时清错地方。
        root = None
        if self.result is not None and Path(self.result.root).is_dir():
            root = Path(self.result.root)
        else:
            raw = self.var_dir.get().strip().strip('"')
            root = Path(raw) if raw else None
        if not root or not root.is_dir():
            messagebox.showwarning("先选目录", "请先在顶部选择小说目录（或先扫描一次）。")
            return
        self._phase = "查找空文件夹"
        self._detail = f"正在检查 {root} …"
        self._render_status()
        self.btn_clean_dirs.configure(state="disabled")
        self.btn_clear_cache.configure(state="disabled")

        def run() -> None:
            try:
                from . import deleter

                dirs, junk = deleter.find_empty_dirs(root)
                self._q.put(("empty_dirs", root, dirs, junk))
            except Exception:  # noqa: BLE001
                self._q.put(("error", traceback.format_exc()))

        threading.Thread(target=run, daemon=True).start()

    def _empty_dirs_done(self, root: Path, dirs: list, junk: list) -> None:
        self.btn_clean_dirs.configure(state="normal")
        if not dirs and not junk:
            self._phase = "完成"
            self._detail = "没有空文件夹"
            self._render_status()
            messagebox.showinfo("没有空文件夹", "所有文件夹里都还有文件，不用清理。")
            return

        items = [plan.DeleteItem(bid=-1, path=str(d), name=d.name, size=0,
                                 reason="空文件夹（里面已没有任何文件）") for d in dirs]
        if junk:
            items += [plan.DeleteItem(bid=-1, path=str(j), name=j.name, size=0,
                                      reason="缩略图缓存（Thumbs.db 等），一并清掉")
                      for j in junk]
        self._append_log(f"— 找到 {len(dirs)} 个空文件夹" +
                         (f"、{len(junk)} 个缩略图缓存文件" if junk else ""))
        dlg = ConfirmDelete(
            self, items, "清理空文件夹 —— 确认",
            f"位置：{root}\n"
            f"这些文件夹在删除重复文件之后已经没有内容了（共 {len(dirs)} 个："
            f"{'、'.join(d.name for d in dirs[:5])}{' 等' if len(dirs) > 5 else ''}）。"
            f"空文件夹会**直接删除**（里面没有任何文件，不需要回收站）。"
            + (f"\n另有 {len(junk)} 个系统缩略图缓存文件（Thumbs.db / desktop.ini）一并清除。"
               if junk else ""),
            unit="文件夹",
            headline=(f"即将直接删除 {len(dirs):,} 个空文件夹"
                      + (f" + {len(junk):,} 个缓存文件" if junk else "")))
        self.wait_window(dlg)
        if not dlg.approved:
            self._append_log("— 已取消清理空文件夹")
            self._phase = "完成"
            self._detail = "已取消"
            self._render_status()
            return
        self._start_clean_dirs(root, dirs, junk)

    def _start_clean_dirs(self, root: Path, dirs: list, junk: list) -> None:
        self._deleting = True
        self.btn_clean_dirs.configure(state="disabled")
        self.btn_clear_cache.configure(state="disabled")
        self._phase = "清理空文件夹"
        self._detail = f"正在删除 {len(dirs)} 个空文件夹…"
        self._render_status()

        def run() -> None:
            try:
                from . import deleter

                paths = [*junk, *dirs]
                res = deleter.to_recycle_bin(paths, root=root, log=self.logger,
                                             reason="清理空文件夹", allow_dirs=True)
                self._q.put(("dirs_done", res, len(dirs), len(junk)))
            except Exception:  # noqa: BLE001
                self._q.put(("error", traceback.format_exc()))

        threading.Thread(target=run, daemon=True).start()

    def _dirs_done(self, res, n_dirs: int, n_junk: int = 0) -> None:
        self._deleting = False
        self.btn_clean_dirs.configure(state="normal")
        self.btn_clear_cache.configure(state="normal")
        # res.deleted 里混着空目录和 Thumbs.db，计数必须按"请求的清单"分别算，
        # 不能把垃圾文件也报成"空文件夹"。
        ok = {str(p) for p in res.deleted}
        self._phase = "完成"
        # 措辞说清"直接删除"（空目录不进回收站），同时保留"清理"这个动作词
        self._detail = f"已清理 {n_dirs:,} 个空文件夹（直接删除）" + (
            f"、{n_junk:,} 个缓存文件" if n_junk else "") + (
            f"，{len(res.failed):,} 个失败" if res.failed else "")
        self._render_status()
        self.var_info.set(self._detail)
        self._append_log(f"— {self._detail}")
        if res.failed:
            lines = "\n".join(f"{p.name}：{why}" for p, why in res.failed[:10])
            messagebox.showwarning(
                "部分没删掉",
                f"成功删除 {len(ok):,} 项，失败 {len(res.failed):,} 项。\n\n{lines}")
        else:
            messagebox.showinfo(
                "清理完成",
                f"已直接删除 {n_dirs:,} 个空文件夹"
                + (f"、{n_junk:,} 个缓存文件" if n_junk else "")
                + "。\n\n空文件夹里没有任何文件，所以不进回收站；"
                  "下一个动作建议：点「清除缓存」回收空间。")

    def _confirm_and_delete(self, items: list, title: str, note: str) -> None:
        dlg = ConfirmDelete(self, items, f"{title} —— 确认", note)
        self.wait_window(dlg)
        if not dlg.approved:
            self._append_log("— 已取消删除")
            return
        self._start_delete(items, title)

    def _start_delete(self, items: list, title: str) -> None:
        self._deleting = True
        self._set_result_buttons(False)
        self._refresh_check_summary()
        self._phase = "删除中"
        self._detail = f"正在把 {len(items):,} 个文件移入回收站…"
        self._render_status()
        root = Path(self.result.root) if self.result else None

        def run() -> None:
            try:
                res = deleter.to_recycle_bin(
                    [Path(i.path) for i in items], root=root,
                    log=self.logger, reason=title)
                done_paths = {str(p) for p in res.deleted}
                removed = [i.bid for i in items if str(Path(i.path)) in done_paths]
                self._q.put(("del_done", res, removed))
            except Exception:  # noqa: BLE001
                self._q.put(("error", traceback.format_exc()))

        threading.Thread(target=run, daemon=True).start()

    def _delete_done(self, res, removed: list) -> None:
        from . import engine

        self._deleting = False
        self._set_result_buttons(True)

        removed_set = set(removed)
        if removed_set:
            engine.forget_paths(Path(self.result.root), res.deleted)
            engine.apply_deletions(self.result, removed_set)
            self._checked -= removed_set
            for bid in removed_set:
                self._text_cache.pop(bid, None)
                self._bid_group.pop(bid, None)

            self._bid_group = {}
            for g in self.result.groups:
                for m in g.members:
                    self._bid_group[m] = g

            self._populate_groups()
            kids = self.gtree.get_children()
            if kids:
                sel = self.gtree.selection()
                if not sel:
                    self.gtree.selection_set(kids[0])
                self.on_pick_group()
            else:
                self.mtree.delete(*self.mtree.get_children())
                self._member_index.clear()
                self.ptree.delete(*self.ptree.get_children())
                self.stree.delete(*self.stree.get_children())
                self._set_text(self.txt_a, "（已经没有重复组了）", "", 0)
                self._set_text(self.txt_b, "", "", 0)
                self.var_group_info.set("")

        self._phase = "完成"
        self._detail = (f"已删除 {len(res.deleted):,} 个文件（{_mb(res.freed_bytes)}）"
                        f"，剩 {len(self.result.groups):,} 组")
        self._refresh_check_summary()
        self._render_status()
        self.var_info.set(self._detail)
        self._append_log(f"— {res.summary}；剩余 {len(self.result.groups):,} 组")

        if res.failed:
            lines = "\n".join(f"{p.name}：{why}" for p, why in res.failed[:12])
            more = f"\n…共 {len(res.failed)} 个失败" if len(res.failed) > 12 else ""
            messagebox.showwarning(
                "部分文件没能删除",
                f"成功 {len(res.deleted):,} 个，失败 {len(res.failed):,} 个。\n\n"
                f"{lines}{more}\n\n失败的文件仍留在原处，可以在结果里重新处理。")
        else:
            messagebox.showinfo(
                "删除完成",
                f"{res.summary}\n\n"
                f"剩余 {len(self.result.groups):,} 个重复组。\n"
                f"如需还原，去系统回收站；删除记录：\n{res.log_path}")

    # ------------------------------------------------------------ 证据

    def on_pick_pair(self, _evt=None) -> None:
        sel = self.ptree.selection()
        if not sel or not self.result:
            return
        key = self._pair_index.get(sel[0])
        if not key:
            return
        self._cur_pair = key
        names = {bid: b.name for bid, b in self.result.books.items()}
        self.var_a.set(f"A：{names.get(key[0], '')}")
        self.var_b.set(f"B：{names.get(key[1], '')}")

        segs = self.result.segs.get(key) or self.result.segs.get((key[1], key[0])) or []
        if segs:
            self._fill_segments(key, segs)
        else:
            self._request_evidence(key)

    def _fill_segments(self, key: tuple, segs: list) -> None:
        self.stree.delete(*self.stree.get_children())
        self._seg_index.clear()
        for s in segs:
            iid = self.stree.insert("", "end", values=(s.readable, f"{s.length:,}"))
            self._seg_index[iid] = s
        kids = self.stree.get_children()
        if kids:
            self.stree.selection_set(kids[0])
            self.on_pick_seg()
        else:
            self._set_text(self.txt_a, "（该对没有可显示的匹配段）", "", 0)
            self._set_text(self.txt_b, "", "", 0)

    def _request_evidence(self, key: tuple) -> None:
        """增量扫描后，老的重复对没有预存证据 → 点开时按需重算这一对。

        为什么不全量预算：17,000 对重复的证据要重读几千本书；
        而用户一次只会看几对。按需算，两三秒，换来的是扫描快一个量级。
        """
        if key in self._ev_pending:
            return
        self._ev_pending.add(key)
        self.stree.delete(*self.stree.get_children())
        self._seg_index.clear()
        tip = "正在按需计算这一对的证据…"
        self._set_text(self.txt_a, tip, "", 0)
        self._set_text(self.txt_b, "", "", 0)

        res = self.result

        def run() -> None:
            try:
                from . import engine

                segs = engine.evidence_for(res, key[0], key[1], t2s=self._t2s_used)
                self._q.put(("evidence", key, segs))
            except Exception:  # noqa: BLE001
                self._append_log("按需证据失败：\n" + traceback.format_exc(limit=3), "ERROR")
                self._q.put(("evidence", key, []))

        threading.Thread(target=run, daemon=True).start()

    def on_pick_seg(self, _evt=None) -> None:
        sel = self.stree.selection()
        if not sel:
            return
        s = self._seg_index.get(sel[0])
        if not s:
            return
        key = self._cur_pair
        if not key:
            return
        head = f"{s.readable}\n{'-' * 56}\n"
        ea, ma = self._excerpt(key[0], s.orig_a)
        eb, mb = self._excerpt(key[1], s.orig_b)
        self._set_text(self.txt_a, head, ea, ma)
        self._set_text(self.txt_b, head, eb, mb)

    def _current_pair(self):
        sel = self.ptree.selection()
        return self._pair_index.get(sel[0]) if sel else None

    def copy_path(self) -> None:
        key = self._current_pair()
        if not key or not self.result:
            return
        text = "\n".join(self.result.books[i].path for i in key if i in self.result.books)
        self.clipboard_clear()
        self.clipboard_append(text)
        self._detail = "A/B 路径已复制到剪贴板"
        self._render_status()

    def open_folder(self) -> None:
        key = self._current_pair()
        b = self.result.books.get(key[0]) if key and self.result else None
        if b:
            try:
                os.startfile(str(Path(b.path).parent))  # noqa: S606
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror("打开失败", str(exc))

    def export(self, kind: str) -> None:
        if not self.result:
            return
        ext = ".csv" if kind == "csv" else ".json"
        path = filedialog.asksaveasfilename(
            title=f"导出 {kind.upper()}", defaultextension=ext,
            initialfile=f"重复清单{ext}", filetypes=[(f"{kind.upper()} 文件", f"*{ext}")])
        if not path:
            return
        try:
            if kind == "csv":
                n = report.write_csv(self.result, Path(path))
                self._append_log(f"— 已导出 CSV：{path}（{n} 行）")
                self._detail = f"已导出 CSV：{n} 行"
            else:
                with_c = messagebox.askyesno(
                    "导出选项",
                    "是否包含「全部候选对」（含被拒绝的）？\n\n"
                    "包含：以后可以用「打开结果…」按新判据重判 + 重新分组，不用重扫；"
                    "文件会大很多。\n"
                    "不包含：只导出判为重复的对，文件小，但只能删边不能补边。")
                report.write_json(self.result, Path(path), with_candidates=with_c)
                self._append_log(f"— 已导出 JSON：{path}"
                                 f"（{'含' if with_c else '不含'}全部候选对）")
                self._detail = "已导出 JSON" + ("（含全部候选对）" if with_c else "")
            self._render_status()
            if messagebox.askyesno("导出成功", "要打开它吗？"):
                os.startfile(path)  # noqa: S606
        except Exception:  # noqa: BLE001
            tb = traceback.format_exc()
            self._append_log(tb, "ERROR")
            messagebox.showerror("导出失败", tb[-1200:])

    # ------------------------------------------------------------ 自检

    def selftest_dialog(self) -> None:
        if (self._worker and self._worker.is_alive()) or self._deleting:
            return
        self.btn_start.configure(state="disabled")
        self._append_log("— 开始自检（11 组语料，约 2 秒）")

        def run() -> None:
            try:
                from . import selftest

                passed, total, lines = selftest.run(self.logger)
                self._q.put(("selftest", passed, total, lines))
            except Exception:  # noqa: BLE001
                self._q.put(("error", traceback.format_exc()))

        threading.Thread(target=run, daemon=True).start()

    def _selftest_done(self, passed: int, total: int, lines: list[str]) -> None:
        self.btn_start.configure(state="normal")
        self._detail = f"自检 {passed}/{total}"
        self._render_status()
        self._append_log(f"— 自检 {passed}/{total}")
        win = tk.Toplevel(self)
        win.title("自检结果")
        win.geometry("860x520")
        txt = tk.Text(win, wrap="word", font=("Microsoft YaHei UI", 9))
        txt.insert("1.0", f"自检 {passed}/{total} 通过\n\n" + "\n".join(lines))
        txt.configure(state="disabled")
        txt.pack(fill="both", expand=True)


def main() -> int:
    # 多进程 worker 里绝不能再开一个界面（PyInstaller 打包后尤其要防）
    try:
        import multiprocessing

        if multiprocessing.parent_process() is not None:
            return 0
    except Exception:  # noqa: BLE001
        pass

    try:
        app = App()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, traceback.format_exc(limit=3), "小说查重", 0x10)
        except Exception:
            pass
        return 1
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
