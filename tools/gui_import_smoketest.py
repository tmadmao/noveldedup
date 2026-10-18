"""界面导入流程测试：打开结果文件 → 重新分组 → 照常浏览。

用法（必须用带 tkinter 的解释器）：
    py -3 tools/gui_import_smoketest.py
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import noveldedup.gui as G          # noqa: E402
from noveldedup import report, selftest   # noqa: E402

ok_all = True


def check(label: str, cond: bool, extra: str = "") -> None:
    global ok_all
    ok_all &= bool(cond)
    print(f"  {'✅' if cond else '❌'} {label}" + (f"   {extra}" if extra else ""))


def main() -> int:
    G.messagebox.showinfo = lambda *a, **k: None
    G.messagebox.showwarning = lambda *a, **k: None
    G.messagebox.showerror = lambda *a, **k: print("  [错误]", a[1] if len(a) > 1 else "")
    G.messagebox.askyesno = lambda *a, **k: True

    tmp = Path(tempfile.mkdtemp(prefix="nd_gimp_"))
    a = selftest.make_book(40_000, 71)
    b = selftest.make_book(40_000, 72)
    (tmp / "P.txt").write_bytes(a.encode("gb18030"))
    (tmp / "P_改名.txt").write_bytes(a.encode("utf-8"))
    (tmp / "Q.txt").write_bytes(b.encode("gb18030"))
    (tmp / "无关.txt").write_bytes(selftest.make_book(40_000, 73).encode("utf-8"))
    (tmp / "子目录").mkdir()
    (tmp / "子目录" / "P_副本.txt").write_bytes(a.encode("gb18030"))
    (tmp / "子目录" / "Q_副本.txt").write_bytes(b.encode("gb18030"))
    print(f"临时书库：{tmp}（6 本，2 对重复；子目录里 2 本）")

    app = G.App()
    app.var_dir.set(str(tmp))
    app.var_preset.set("标准")
    app.update()
    app.start()
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 180:
        app.update()
        if app.var_phase.get().startswith(("完成", "出错了")):
            break
        time.sleep(0.05)
    app.update()
    scan_groups = len(app.gtree.get_children())
    scan_pairs = len(app.result.dup_pairs)
    print(f"  扫描：{scan_groups} 组 / {scan_pairs} 对")

    # 导出（含全部候选对）
    js = tmp / "结果.json"
    report.write_json(app.result, js, with_candidates=True)
    print(f"  导出：{js.name}（{js.stat().st_size / 1024:,.0f} KB）")

    # 清空后再导入（模拟"重新打开上次结果"）
    for t in (app.gtree, app.mtree, app.ptree, app.stree):
        t.delete(*t.get_children())
    app.result = None
    app.update()

    G.filedialog.askopenfilename = lambda *a_, **k_: str(js)
    app.open_result()
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 120:
        app.update()
        if app.result is not None and app.var_phase.get().startswith("完成"):
            break
        time.sleep(0.05)
    app.update()

    check("导入后拿到结果", app.result is not None)
    if app.result is None:
        app.destroy()
        return 1
    import_groups = len(app.gtree.get_children())
    import_pairs = len(app.result.dup_pairs)
    check("组数与扫描时一致", import_groups == scan_groups,
          f"扫描 {scan_groups} / 导入 {import_groups}")
    check("书对数与扫描时一致", import_pairs == scan_pairs,
          f"扫描 {scan_pairs} / 导入 {import_pairs}")
    check("状态栏标明是导入的", app._detail.startswith("（导入）"), app._detail[:40])

    # 组成员与书对能正常显示
    kids = app.gtree.get_children()
    if kids:
        app.gtree.selection_set(kids[0])
        app.on_pick_group()
        app.update()
        check("组成员能显示", bool(app.mtree.get_children()),
              f"{len(app.mtree.get_children())} 行")
        check("组内书对能显示", bool(app.ptree.get_children()),
              f"{len(app.ptree.get_children())} 行")
        # 证据：导入后是文件里还原的段，点开应能读原文
        pairs = app.ptree.get_children()
        if pairs:
            app.ptree.selection_set(pairs[0])
            app.on_pick_pair()
            t_ev = time.perf_counter()
            while time.perf_counter() - t_ev < 30:
                app.update()
                if app.stree.get_children():
                    break
                time.sleep(0.05)
            check("匹配段可用", bool(app.stree.get_children()),
                  f"{len(app.stree.get_children())} 段")
            segs = app.stree.get_children()
            if segs:
                app.stree.selection_set(segs[0])
                app.on_pick_seg()
                app.update()
                a_txt = app.txt_a.get("1.0", "end").strip()
                check("左右原文能按偏移读出", len(a_txt) > 20, f"A {len(a_txt)} 字")

    # 缓存位置按钮不崩
    G.os.startfile = lambda *_a, **_k: None
    try:
        app.open_cache_dir()
        check("「指纹缓存位置」按钮可用", True)
    except Exception as exc:  # noqa: BLE001
        check("「指纹缓存位置」按钮可用", False, str(exc))

    # ---- 删掉子目录里的两个副本，重新导入：不该再显示；然后清理空目录
    print("\n  → 删掉子目录里的两个副本，再导入一次")
    for p in (tmp / "子目录").glob("*.txt"):
        p.unlink()
    app.result = None
    for t in (app.gtree, app.mtree, app.ptree, app.stree):
        t.delete(*t.get_children())
    app.update()

    G.filedialog.askopenfilename = lambda *a_, **k_: str(js)
    app.open_result()
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 120:
        app.update()
        if app.result is not None and app.var_phase.get().startswith("完成"):
            break
        time.sleep(0.05)
    app.update()

    names2 = {b.name for b in app.result.books.values()} if app.result else set()
    print(f"     导入后：{sorted(names2)}")
    check("已删文件不再显示", not ({"P_副本.txt", "Q_副本.txt"} & names2),
          str(sorted(names2)))
    check("状态栏写明剔除数量", "已剔除" in app._detail, app._detail[-30:])

    # 清理空文件夹（替身确认框自动批准）
    class AutoConfirm(G.ConfirmDelete):
        def __init__(self, master, items, title, note, unit="文件", headline=""):
            super().__init__(master, items, title, note, unit, headline)
            self.withdraw()
            # 直接置 approved 再销毁：`_confirm()` 会检查"我已确认"那个勾选框，
            # 不勾就直接 return，弹窗会一直挂着（踩过）
            self.approved = True
            self.after(60, self.destroy)

    G.ConfirmDelete = AutoConfirm
    app.on_clean_empty_dirs()
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 120:
        app.update()
        if not (tmp / "子目录").exists() and "清理" in app._detail:
            break
        time.sleep(0.05)
    app.update()
    check("空子目录已清理", not (tmp / "子目录").exists(),
          "还在" if (tmp / "子目录").exists() else "")
    check("书没被误删", (tmp / "P.txt").exists() and (tmp / "Q.txt").exists())
    print(f"     状态：{app._detail}")

    app.destroy()
    print("\n界面导入流程测试：" + ("通过" if ok_all else "失败"))
    print(f"（临时目录：{tmp}）")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
