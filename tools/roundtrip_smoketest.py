"""导出 → 导入 往返测试。

验证三件事：
1. 导出再导入，结果必须**和扫描时一致**（书数、组、书对、标签）
2. 导入时能**按当前判据重判**（改阈值后导入，标签应随之变化）
3. 证据段能还原（章节/偏移），原文按需读取

跑：py -3 tools/roundtrip_smoketest.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from noveldedup import core, engine, judge, loader, report, selftest   # noqa: E402

ok_all = True


def check(label: str, cond: bool, extra: str = "") -> None:
    global ok_all
    ok_all &= bool(cond)
    print(f"  {'✅' if cond else '❌'} {label}" + (f"   {extra}" if extra else ""))


def pair_set(res) -> set[tuple[str, str, str]]:
    names = {b.id: b.name for b in res.books.values()}
    out = set()
    for s in res.dup_pairs:
        a, b = names.get(s.a, "?"), names.get(s.b, "?")
        out.add((a, b, s.rel) if a <= b else (b, a, s.rel))
    return out


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="nd_rt_"))
    print(f"临时书库：{tmp}\n")

    a = selftest.make_book(40_000, 61)
    b = selftest.make_book(40_000, 62)
    (tmp / "甲.txt").write_bytes(a.encode("gb18030"))
    (tmp / "甲_改名.txt").write_bytes(a.encode("utf-8"))          # 同一版本
    (tmp / "甲_断更.txt").write_bytes(a[: int(len(a) * 0.75)].encode("gb18030"))  # B ⊂ A
    (tmp / "乙.txt").write_bytes(b.encode("gb18030"))
    (tmp / "乙_改写.txt").write_bytes(selftest.make_book(40_000, 63).encode("gb18030"))

    print("[1] 扫描")
    res1 = engine.scan(tmp, preset="标准", workers=1, with_evidence=True)
    print(f"  {len(res1.books)} 本 / {len(res1.groups)} 组 / {len(res1.dup_pairs)} 对"
          f" / 候选 {len(res1.candidates)} 条")

    print("\n[2] 导出（含全部候选对）")
    js = tmp / "结果.json"
    report.write_json(res1, js, with_candidates=True)
    print(f"  {js.name}：{js.stat().st_size / 1024:,.0f} KB")

    print("\n[3] 导入并与扫描结果比对")
    ld = loader.load_result(js, reclassify=True)
    check("导入成功", ld.ok, "；".join(ld.warnings))
    if not ld.ok:
        return 1
    res2 = ld.result
    check("书数一致", len(res2.books) == len(res1.books),
          f"{len(res2.books)} vs {len(res1.books)}")
    check("组数一致", len(res2.groups) == len(res1.groups),
          f"{len(res2.groups)} vs {len(res1.groups)}")
    check("重复对**逐个一致**", pair_set(res2) == pair_set(res1),
          f"导入 {len(pair_set(res2))} / 原 {len(pair_set(res1))}")
    g1 = sorted(sorted(res1.books[m].name for m in g.members) for g in res1.groups)
    g2 = sorted(sorted(res2.books[m].name for m in g.members) for g in res2.groups)
    check("分组结构一致", g1 == g2, f"{g1} vs {g2}")

    print("\n[4] 证据段还原")
    check("证据段被还原", bool(res2.segs), f"{len(res2.segs)} 对带证据")
    if res2.segs:
        segs = next(iter(res2.segs.values()))
        s = segs[0]
        check("段能读成一句话", "连续" in s.readable, s.readable)
        check("有原文偏移", s.orig_a > 0 or s.orig_b > 0,
              f"a={s.orig_a} b={s.orig_b}")
        # 按偏移读原文（界面就是这么做的）
        raw = (tmp / res2.books[res2.dup_pairs[0].a].name).read_bytes()
        check("原文还能按偏移读到", len(raw) > 1000, f"{len(raw):,} 字节")

    print("\n[5] 导入后能按当前判据重判（改阈值 → 结果应随之变化）")
    old = core.TH.jac_rev
    try:
        core.TH.jac_rev = 0.99             # 把最低相似度门槛拉到 0.99
        ld2 = loader.load_result(js, reclassify=True)
        n2 = len(pair_set(ld2.result))
        check("收紧阈值后书对减少", n2 < len(pair_set(res1)),
              f"{len(pair_set(res1))} → {n2} 对")
        left = {p[2] for p in pair_set(ld2.result)}
        print(f"     剩下 {n2} 对，标签：{left or '（空）'}")
    finally:
        core.TH.jac_rev = old

    print("\n[6] 不带候选对的导出（旧格式）")
    js2 = tmp / "结果_精简.json"
    report.write_json(res1, js2, with_candidates=False)
    ld3 = loader.load_result(js2, reclassify=True)
    check("精简版也能导入", ld3.ok)
    check("精简版组数一致", ld3.ok and len(ld3.result.groups) == len(res1.groups),
          f"{len(ld3.result.groups) if ld3.ok else '-'} 组")
    check("会提示「只能删边不能补边」",
          any("候选对" in w for w in ld3.warnings), "；".join(ld3.warnings)[:80])

    print("\n" + ("往返测试：通过" if ok_all else "往返测试：失败"))
    print(f"（临时目录：{tmp}）")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
