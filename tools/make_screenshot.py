"""生成 README 用的界面截图（真实界面，不是拼图）。

做法：用合成语料建一个小书库 → 启动真实 tkinter 界面 → 扫描 → 选中分组/书对/证据段
→ 用 Win32 `PrintWindow` 把窗口内容抓下来存 PNG（PW_RENDERFULLCONTENT，窗口被遮住也能抓）。

用法：
    py -3 tools/make_screenshot.py                 # 两张：主界面 + 合订-分卷结构图
    py -3 tools/make_screenshot.py --out docs/ui-main.png --group first

依赖：Pillow（仅截图用，运行本工具本身不需要）。
"""

from __future__ import annotations

import argparse
import ctypes
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from noveldedup import selftest                      # noqa: E402
from noveldedup.gui import App                       # noqa: E402

W, H = 1920, 1120


# ---------------------------------------------------------------- 语料

def build_demo_library(root: Path) -> None:
    """6 本书：3 个同源副本 + 1 组「单卷 vs 合订」。文件名像真实书库。"""
    a = selftest.make_book(22_000, 11)

    def w(name: str, text: str, enc: str = "utf-8") -> None:
        (root / name).write_bytes(text.encode(enc, errors="replace"))

    w("斗破苍穹.txt", a)
    # 排版版：段首缩进 + CRLF + GBK（判成"同一版本"）
    styled = "\r\n".join("　　" + ln.strip() for ln in a.split("\n") if ln.strip())
    w("斗破苍穹（精校版）.txt", styled, "gb18030")
    # 广告版：每 3000 字插一条站点广告（判成"仅插入广告/换排版"）
    parts = []
    for i in range(0, len(a), 3000):
        parts.append(a[i : i + 3000] + "\n看正版就来青柠书屋 www.qingning.com\n")
    w("斗破苍穹（某某书屋）.txt", "".join(parts))
    # 缺章版：只有前 72%（判成"B ⊂ A（B 缺章）"，证据段是部分匹配）
    w("斗破苍穹（断更到第9章）.txt", a[: int(len(a) * 0.72)])

    # 合订-分卷：上卷 + 中卷 = 全集
    v1, v2 = selftest.make_book(20_000, 22), selftest.make_book(20_000, 33)
    w("凡人修仙传_上卷.txt", v1)
    w("凡人修仙传_中卷.txt", v2)
    w("凡人修仙传_全集.txt", v1 + v2)


# ---------------------------------------------------------------- 抓图

def capture(window, path: Path) -> None:
    from PIL import Image

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    user32.SetProcessDPIAware()

    hwnd = int(window.winfo_id())
    top = user32.GetParent(hwnd) or hwnd          # 真正的顶层窗口（带标题栏）
    rect = wintypes.RECT()
    user32.GetWindowRect(top, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top

    hdc = user32.GetWindowDC(top)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    try:
        # 2 = PW_RENDERFULLCONTENT：窗口被遮挡/最小化时也能拿到内容
        if not user32.PrintWindow(top, mem, 2):
            raise RuntimeError("PrintWindow 失败")

        class BMIH(ctypes.Structure):
            _fields_ = [
                ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD),
            ]

        bi = BMIH()
        bi.biSize = ctypes.sizeof(BMIH)
        bi.biWidth, bi.biHeight = w, -h          # 负高度 = 自上而下
        bi.biPlanes, bi.biBitCount = 1, 32
        bi.biCompression = 0                     # BI_RGB

        buf = ctypes.create_string_buffer(w * h * 4)
        if not gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0):
            raise RuntimeError("GetDIBits 失败")
        img = Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)
    finally:
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(top, hdc)

    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG", optimize=True)
    print(f"  已保存 {path}（{w}×{h}，{path.stat().st_size / 1024:.0f} KB）")


# ---------------------------------------------------------------- 主流程

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/ui-main.png")
    ap.add_argument("--group", choices=("first", "omnibus"), default="first")
    args = ap.parse_args(argv[1:])

    root = Path(tempfile.mkdtemp(prefix="nd_shot_"))
    build_demo_library(root)
    print(f"语料：{root}（6 本）")

    app = App()
    app.geometry(f"{W}x{H}+60+40")
    app.var_dir.set(str(root))
    app.var_preset.set("标准")
    app.update()

    t0 = time.perf_counter()
    app.start()
    while time.perf_counter() - t0 < 180:
        app.update()
        if app.var_phase.get().startswith(("完成", "出错了")):
            break
        time.sleep(0.05)
    print(f"扫描：{app.var_phase.get()}｜{app.var_detail.get()}")

    kids = app.gtree.get_children()
    print(f"分组：{len(kids)} 行")
    if not kids:
        app.destroy()
        return 1

    # 选目标组
    target = kids[0]
    if args.group == "omnibus":
        from noveldedup.group import STRUCT_OMNIBUS

        for iid, idx in app._group_index.items():
            if app.result.groups[idx].structure == STRUCT_OMNIBUS:
                target = iid
                break
    app.gtree.selection_set(target)
    app.on_pick_group()
    app.update()
    # 三栏宽度（截图用：左栏够放"建议保留"，右栏左右各 ~485）
    app.main_panes.sashpos(0, 430)
    app.main_panes.sashpos(1, 1070)
    # 中栏竖向三段（成员 / 书对 / 匹配段）：成员区留够，合订结构图才不挤
    mid = app.main_panes.nametowidget(app.main_panes.panes()[1])
    mh = mid.winfo_height()
    mid.sashpos(0, int(mh * 0.46))
    mid.sashpos(1, int(mh * 0.74))
    app.update()
    time.sleep(0.2)
    app.update()

    # 优先选"缺章/子集"那一对：证据段是部分匹配，比"整本相同"更能说明问题
    pairs = app.ptree.get_children()
    pick = pairs[0] if pairs else None
    for iid in pairs:
        vals = app.ptree.item(iid, "values")
        if any("⊂" in str(v) for v in vals):
            pick = iid
            break
    if pick:
        app.ptree.selection_set(pick)
        app.on_pick_pair()
        app.update()
        segs = app.stree.get_children()
        if segs:
            app.stree.selection_set(segs[0])
            app.on_pick_seg()
            app.update()
    time.sleep(0.3)
    app.update_idletasks()
    app.update()

    panes = [app.main_panes.nametowidget(pid) for pid in app.main_panes.panes()]
    print(f"  窗口 {app.winfo_width()}x{app.winfo_height()}"
          f" sash={app.main_panes.sashpos(0)}/{app.main_panes.sashpos(1)}"
          f" 栏宽={[p.winfo_width() for p in panes]}")
    capture(app, Path(args.out))
    app.destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
