"""合订-分卷结构测试：识别、覆盖率、二选一建议、界面结构图。

场景：一本书被拆成 上/中/下 三卷，另有一个三卷合订的全集文件。
期望：
- 4 个文件归成一组，标 `合订-分卷`
- 认出全集 = 那个大文件，3 个分卷各自给出对全集的覆盖率
- 建议保留 = 全集，理由写明"合订本，由 3 个分卷合成"
- 界面能画出结构图（Canvas 上有图形元素）并给出二选一建议

跑：py -3 tools/omnibus_smoketest.py
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from noveldedup import engine, judge, selftest   # noqa: E402

ok_all = True


def check(label: str, cond: bool, extra: str = "") -> None:
    global ok_all
    ok_all &= bool(cond)
    print(f"  {'✅' if cond else '❌'} {label}" + (f"   {extra}" if extra else ""))


def build_corpus(tmp: Path) -> tuple[int, int]:
    """造 上/中/下 三卷 + 一个全集合订本，返回全集的章节数与字数。"""
    vols = [selftest.make_book(18_000, s) for s in (21, 22, 23)]
    for i, v in enumerate(vols, 1):
        (tmp / f"《测试长篇》第{i}卷.txt").write_bytes(v.encode("gb18030"))
    full = "\n".join(vols)
    (tmp / "《测试长篇》全集.txt").write_bytes(full.encode("utf-8"))
    return full.count("第"), len(full)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="nd_omni_"))
    chapters, chars = build_corpus(tmp)
    print(f"临时书库：{tmp}（3 个分卷 + 1 个全集，全集 {chars:,} 字）\n")

    print("[1] 扫描与结构识别")
    res = engine.scan(tmp, preset="标准", workers=1, with_evidence=False)
    check("4 个文件成 1 组", len(res.groups) == 1, f"{len(res.groups)} 组")
    g = res.groups[0] if res.groups else None
    check("结构标签 = 合订-分卷", bool(g) and g.structure == "合订-分卷",
          getattr(g, "structure", ""))
    check("组内 4 本", bool(g) and g.size == 4, f"{getattr(g, 'size', 0)} 本")

    if g:
        sup = res.books.get(g.superset_id)
        check("认出全集", bool(sup), sup.name if sup else "无")
        check("全集是那个大文件", bool(sup) and "全集" in sup.name)
        check("识别出 3 个分卷", len(g.volumes) == 3, f"{len(g.volumes)} 个")
        print(f"     分卷覆盖：{[(res.books[v].name, round(c, 3)) for v, c in g.volumes]}")
        check("分卷合计覆盖 ≥95%", g.volumes_cover >= 0.95, f"{g.volumes_cover:.1%}")

        print("\n[2] 建议保留与二选一")
        check("建议保留 = 全集", g.keep_id == g.superset_id,
              res.books[g.keep_id].name if g.keep_id in res.books else "")
        check("理由写明合订本", "合订" in g.keep_reason, g.keep_reason)
        a, b = judge.omnibus_advice(g, res.books)
        check("给了「保留全集」方案", "保留全集" in a and "省" in a)
        check("给了「保留分卷」方案", "保留 3 个分卷" in b and "覆盖" in b)
        print(f"     ① {a}")
        print(f"     ② {b}")

    print("\n[3] 界面结构图")
    import noveldedup.gui as G

    G.messagebox.showinfo = lambda *a, **k: None
    G.messagebox.showwarning = lambda *a, **k: None
    G.messagebox.showerror = lambda *a, **k: print("  [错误]", a[1] if len(a) > 1 else "")
    G.messagebox.askyesno = lambda *a, **k: False

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

    kids = app.gtree.get_children()
    check("分组列表有 1 行", len(kids) == 1, f"{len(kids)} 行")
    if kids:
        app.gtree.selection_set(kids[0])
        app.on_pick_group()
        app.update()
        app.update_idletasks()
        c = app.struct_canvas
        check("结构图画布已显示", bool(c.winfo_ismapped()))
        items = c.find_all()
        check("画布上有图形元素", len(items) >= 10, f"{len(items)} 个元素")
        texts = [c.itemcget(i, "text") for i in items if c.type(i) == "text"]
        check("画了「全集」", any("全集" in t for t in texts), str(texts[:3]))
        check("画面里有分卷名", any("第1卷" in t for t in texts), str(texts))
        advice = app.var_advice.get()
        check("显示了二选一建议", "保留全集" in advice and "分卷" in advice, advice[:80])

    app.destroy()
    print("\n" + ("合订-分卷结构测试：通过" if ok_all else "合订-分卷结构测试：失败"))
    print(f"（临时目录：{tmp}）")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
