"""界面删除流程测试：勾选 → 确认框 → 后台删除 → 结果刷新。

用临时书库，**绝不碰真实书库**。确认框自动点"确认"，其余全部走真实代码路径
（包括线程、队列、缓存清理、分组重建）。

用法（必须用带 tkinter 的解释器）：
    py -3 tools/gui_delete_smoketest.py
"""

from __future__ import annotations

import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import noveldedup.gui as G          # noqa: E402
from noveldedup import core, selftest     # noqa: E402


class AutoConfirm(tk.Toplevel):
    """替身确认框：真 Toplevel + 自动批准，用来跑通 wait_window 那条路径。"""

    def __init__(self, master, items, title, note, unit="文件", headline=""):
        super().__init__(master)
        self.approved = True
        self.seen = len(items)
        self.withdraw()
        self.after(60, self.destroy)


def main() -> int:
    G.messagebox.showinfo = lambda *a, **k: None
    G.messagebox.showwarning = lambda *a, **k: print("  [警告]", a[1] if len(a) > 1 else "")
    G.messagebox.showerror = lambda *a, **k: print("  [错误]", a[1] if len(a) > 1 else "")
    G.messagebox.askyesno = lambda *a, **k: True
    G.ConfirmDelete = AutoConfirm

    tmp = Path(tempfile.mkdtemp(prefix="nd_guid_"))
    base = selftest.make_book(40_000, 7)
    for n in ("《书》一.txt", "《书》二.txt", "《书》三.txt"):
        (tmp / n).write_bytes(base.encode("gb18030"))
    (tmp / "《别的书》.txt").write_bytes(selftest.make_book(40_000, 42).encode("utf-8"))
    print(f"临时书库：{tmp}（4 本，其中 3 本内容相同）")

    app = G.App()
    app.var_dir.set(str(tmp))
    app.var_preset.set("标准")
    app.update()

    t0 = time.perf_counter()
    app.start()
    while time.perf_counter() - t0 < 180:
        app.update()
        if app.var_phase.get().startswith(("完成", "出错了")):
            break
        time.sleep(0.05)

    ok = True
    print(f"  扫描 {time.perf_counter() - t0:.1f}s｜{app.var_detail.get()}")
    groups = app.gtree.get_children()
    ok &= bool(groups)
    print(f"  {'✅' if groups else '❌'} 分组 {len(groups)} 行")

    if groups:
        app.gtree.selection_set(groups[0])
        app.on_pick_group()
        app.update()
        members = app.mtree.get_children()
        ok &= len(members) == 3
        print(f"  {'✅' if len(members) == 3 else '❌'} 组成员 {len(members)} 行（应为 3）")
        for iid in members:
            print("     ", " | ".join(str(x) for x in app.mtree.item(iid, "values")))

        # 勾选逻辑（点第一列）
        app._toggle_member(members[0])
        ok &= len(app._checked) == 1
        print(f"  {'✅' if len(app._checked) == 1 else '❌'} 点勾选后已勾选 {len(app._checked)} 本"
              f"｜{app.var_check.get()}")
        app.on_clear_checked()
        ok &= not app._checked
        print(f"  {'✅' if not app._checked else '❌'} 清空勾选")

        # 一键清理（自动确认）
        print("  → 点「一键清理内容完全相同」")
        app.on_delete_identical()
        deadline = time.perf_counter() + 120
        while time.perf_counter() < deadline:
            app.update()
            if not app._deleting:
                break
            time.sleep(0.05)
        app.update()

        left = sorted(p.name for p in tmp.iterdir())
        print(f"  目录剩：{left}")
        ok &= len(left) == 2
        print(f"  {'✅' if len(left) == 2 else '❌'} 删后目录只剩 2 本")
        print(f"  状态：{app.var_detail.get()}")
        ok &= "已删除" in app.var_detail.get()

        # ---- 清理空文件夹（自动确认）
        print("  → 造两个空子文件夹，点「清理空文件夹」")
        (tmp / "空目录A" / "空目录B").mkdir(parents=True)
        (tmp / "有货" ).mkdir(exist_ok=True)
        (tmp / "有货" / "留着.txt").write_text("别删我", encoding="utf-8")
        app.on_clean_empty_dirs()
        deadline = time.perf_counter() + 120
        while time.perf_counter() < deadline:
            app.update()
            if not app._deleting and "空文件夹" not in app.var_phase.get():
                break
            time.sleep(0.05)
        app.update()
        ok &= not (tmp / "空目录A").exists()
        print(f"  {'✅' if not (tmp / '空目录A').exists() else '❌'} 级联空文件夹已清理")
        ok &= (tmp / "有货" / "留着.txt").exists()
        print(f"  {'✅' if (tmp / '有货' / '留着.txt').exists() else '❌'} 有文件的目录保留")
        print(f"  状态：{app.var_detail.get()}")
        ok &= "清理" in app.var_detail.get()

        # 结果里不该再有已删的书
        names = {b.name for b in app.result.books.values()}
        ok &= len(names) == 2
        print(f"  {'✅' if len(names) == 2 else '❌'} 结果集剩 {len(names)} 本：{sorted(names)}")
        print(f"  {'✅' if not app.result.groups else '❌'} 剩余 {len(app.result.groups)} 组")

    # ---- 清除缓存（替身对话框，自动选"只清本库"）
    print("  → 点「清除缓存」")
    cache_dir = core.cache_dir_for(tmp)
    ok &= cache_dir.exists()
    print(f"  {'✅' if cache_dir.exists() else '❌'} 清之前缓存存在")

    class AutoCache(G.ClearCacheDialog):
        def __init__(self, master, cur_mb, all_mb, cur_name):
            super().__init__(master, cur_mb, all_mb, cur_name)
            self.withdraw()
            self.after(60, lambda: self._pick("cur"))

    G.ClearCacheDialog = AutoCache
    app.on_clear_cache()
    deadline = time.perf_counter() + 120
    while time.perf_counter() < deadline:
        app.update()
        if not cache_dir.exists() and "已清除" in app.var_detail.get():
            break
        time.sleep(0.05)
    app.update()
    ok &= not cache_dir.exists()
    print(f"  {'✅' if not cache_dir.exists() else '❌'} 缓存已清除")
    ok &= "已清除" in app.var_detail.get()
    print(f"  {'✅' if '已清除' in app.var_detail.get() else '❌'} 状态提示正确")
    ok &= len(list(tmp.glob("*.txt"))) == 2
    print(f"  {'✅' if len(list(tmp.glob('*.txt'))) == 2 else '❌'} 书没被误删")
    print(f"  状态：{app.var_detail.get()}")

    log_lines = app.txt_log.get("1.0", "end").strip().splitlines()
    print(f"  日志 {len(log_lines)} 行，末行：{log_lines[-1] if log_lines else ''}")

    app.destroy()
    print("\n界面删除流程测试：" + ("通过" if ok else "失败"))
    print(f"（临时目录：{tmp}）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
