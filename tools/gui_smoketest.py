"""界面冒烟测试：建窗 → 扫描 → 选组 → 选书对 → 点匹配段 → 读左右原文。

用法（必须用带 tkinter 的解释器）：
    py -3 tools/gui_smoketest.py [目录]
不给目录时自动生成一套**合成语料**（11 组已知答案），不依赖任何本地书库。

⚠ 必须在**桌面会话**里跑。无交互桌面的环境（服务、远程会话）里 tkinter 可能挂起。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from noveldedup.gui import App  # noqa: E402


def main(argv: list[str]) -> int:
    import tempfile

    from noveldedup import selftest

    if len(argv) > 1:
        root = Path(argv[1]).resolve()
    else:
        root = Path(tempfile.mkdtemp(prefix="nd_gui_"))
        selftest.build_corpus(root)
        print(f"（未指定目录，已生成合成语料到 {root}）")
    root = root.resolve()
    print(f"目录：{root}")
    if not root.is_dir():
        print("目录不存在")
        return 2

    import noveldedup.gui as G

    G.messagebox.showinfo = lambda *a, **k: None
    G.messagebox.showwarning = lambda *a, **k: None
    G.messagebox.showerror = lambda *a, **k: print("  [对话框]", a[1] if len(a) > 1 else a)
    G.messagebox.askyesno = lambda *a, **k: False

    app = App()
    app.var_dir.set(str(root))
    app.var_preset.set("自动")
    app.update()

    t0 = time.perf_counter()
    app.start()
    deadline = t0 + 600
    while time.perf_counter() < deadline:
        app.update()
        st = app.var_phase.get()
        if st.startswith("完成") or st.startswith("出错了"):
            break
        time.sleep(0.05)
    print(f"  扫描 {time.perf_counter() - t0:.1f}s｜{app.var_phase.get()}")
    print(f"  状态：{app.var_detail.get()}")

    ok = True
    groups = app.gtree.get_children()
    print(f"  {'✅' if groups else '❌'} 分组列表 {len(groups)} 行（{app.var_counts.get()}）")
    ok &= bool(groups)

    if groups:
        app.gtree.selection_set(groups[0])
        app.on_pick_group()
        app.update()
        members = app.mtree.get_children()
        print(f"  {'✅' if members else '❌'} 组成员 {len(members)} 行")
        for iid in members[:4]:
            print("     ", " | ".join(str(x) for x in app.mtree.item(iid, "values")))
        ok &= bool(members)

        pairs = app.ptree.get_children()
        print(f"  {'✅' if pairs else '❌'} 组内书对 {len(pairs)} 行")
        for iid in pairs[:3]:
            print("     ", " | ".join(str(x) for x in app.ptree.item(iid, "values")))
        ok &= bool(pairs)

        if pairs:
            app.ptree.selection_set(pairs[0])
            app.on_pick_pair()
            # 增量扫描后老的重复对没有预存证据，界面会**按需**在后台算；
            # 这里等它算完（最多 90 秒），顺便验证这条异步路径。
            t_ev = time.perf_counter()
            while time.perf_counter() - t_ev < 90:
                app.update()
                if app.stree.get_children():
                    break
                time.sleep(0.05)
            segs = app.stree.get_children()
            dt = time.perf_counter() - t_ev
            print(f"  {'✅' if segs else '❌'} 匹配段 {len(segs)} 条"
                  f"（{'按需计算' if dt > 0.3 else '直接命中'}，{dt:.1f}s）")
            ok &= bool(segs)

            if segs:
                app.stree.selection_set(segs[0])
                app.on_pick_seg()
                app.update()
                a = app.txt_a.get("1.0", "end").strip()
                b = app.txt_b.get("1.0", "end").strip()
                print(f"  {'✅' if a and b else '❌'} 左右原文 A={len(a)} 字 B={len(b)} 字")
                print(f"      标题：{app.var_a.get()} / {app.var_b.get()}")
                print(f"      A：{a.splitlines()[-1][:50] if a else ''}")
                ok &= bool(a) and bool(b)

        # 过滤框
        app.var_filter.set("不存在的关键词xyz")
        app._populate_groups()
        app.update()
        filtered = app.gtree.get_children()
        print(f"  {'✅' if not filtered else '❌'} 过滤生效（无匹配时应为 0 行，实为 {len(filtered)}）")
        ok &= not filtered
        app.var_filter.set("")
        app._populate_groups()

    log_lines = app.txt_log.get("1.0", "end").strip().splitlines()
    print(f"  {'✅' if log_lines else '❌'} 运行日志 {len(log_lines)} 行")
    ok &= bool(log_lines)

    app.destroy()
    print("\n界面冒烟测试：" + ("通过" if ok else "失败"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
