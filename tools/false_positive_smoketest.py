"""假阳性回归测试 —— 这几类误判在真实书库里出现过，必须永远不再发生。

真实案例（用户 上万 本书库）：
    4,250 字的短篇 ↔ 352 万字的长篇，jac = 0.0012
    被判成"章节顺序不同" → 一个合集文件把 3,216 本无关的书连成一组

根因：`classify()` 里"章节顺序不同"排在 Jaccard 检查之前，
只要共享几百个指纹 + 顺序散乱就返回重复，**完全不看整体相似度**。

本测试覆盖 6 个场景：
  ① 长篇里截出一段短篇           → 必须判"无关系"
  ② 两篇不同的短篇共享 3 千字模板  → 必须判"无关系"
  ③ 短篇 vs 万倍长的长篇          → 必须判"无关系"
  ④ 真断更版（75%）               → 必须判"B ⊂ A"
  ⑤ 改名版（同一本）              → 必须判"同一版本"
  ⑥ 文集（8 个短篇拼起来）+ 8 个短篇 → 不许形成大组，文集本身判无关

跑：py -3 tools/false_positive_smoketest.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from noveldedup import engine, group as group_mod, judge, selftest   # noqa: E402

ok_all = True


def check(label: str, cond: bool, extra: str = "") -> None:
    global ok_all
    ok_all &= bool(cond)
    print(f"  {'✅' if cond else '❌'} {label}" + (f"   {extra}" if extra else ""))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="nd_fp_"))
    print(f"临时书库：{tmp}\n")

    long = selftest.make_book(300_000, 31)      # 长篇 ~30 万字
    # ① 从长篇中段截一段连续的 5 千字
    mid = len(long) // 2
    snippet = long[mid: mid + 5_000]
    (tmp / "长篇.txt").write_bytes(long.encode("gb18030"))
    (tmp / "长篇_片段.txt").write_bytes(snippet.encode("gb18030"))

    # ② 两篇不同的短篇，中间共享 3 千字模板
    a = selftest.make_book(10_000, 41)
    b = selftest.make_book(10_000, 42)
    tpl = selftest.make_book(3_000, 43)
    (tmp / "短篇甲.txt").write_bytes((a[:3_500] + tpl + a[3_500:]).encode("gb18030"))
    (tmp / "短篇乙.txt").write_bytes((b[:3_500] + tpl + b[3_500:]).encode("gb18030"))
    (tmp / "模板.txt").write_bytes(tpl.encode("gb18030"))

    # ③ 1 万字短篇 vs 30 万字长篇（都是各自独立内容）
    (tmp / "无关短篇.txt").write_bytes(selftest.make_book(10_000, 44).encode("gb18030"))

    # ④ 真断更版：长篇的前 75%
    (tmp / "长篇_断更版.txt").write_bytes(long[: int(len(long) * 0.75)].encode("gb18030"))

    # ⑤ 改名版
    (tmp / "长篇_改名版.txt").write_bytes(long.encode("utf-8"))

    # ⑥ 文集：8 个短篇拼起来 + 8 个独立文件
    vols = [selftest.make_book(8_000, 500 + i) for i in range(8)]
    (tmp / "短篇文集.txt").write_bytes("\n".join(vols).encode("gb18030"))
    for i, v in enumerate(vols):
        (tmp / f"文集第{i + 1}篇.txt").write_bytes(v.encode("gb18030"))

    print("[1] 扫描")
    res = engine.scan(tmp, preset="标准", workers=1, with_evidence=False)
    names = {b.id: b.name for b in res.books.values()}

    def rel_of(x: str, y: str) -> str:
        ix = next((i for i, n in names.items() if n == x), None)
        iy = next((i for i, n in names.items() if n == y), None)
        for s in res.dup_pairs:
            if {s.a, s.b} == {ix, iy}:
                return s.rel
        return judge.NO_REL

    def metrics(x: str, y: str) -> str:
        ix = next((i for i, n in names.items() if n == x), None)
        iy = next((i for i, n in names.items() if n == y), None)
        for s in res.dup_pairs:
            if {s.a, s.b} == {ix, iy}:
                return f"jac={s.jac:.4f} c={s.cont_ab:.3f}/{s.cont_ba:.3f}"
        return "未进候选或未判重"

    print(f"  {len(res.books)} 本，{len(res.groups)} 组，{len(res.dup_pairs)} 对\n")

    print("[2] 必须判「无关系」的（真实书库里的误判来源）")
    for x, y, why in (
        ("长篇.txt", "长篇_片段.txt", "长篇 vs 5 千字片段"),
        ("短篇甲.txt", "短篇乙.txt", "两篇不同短篇共享 3 千字模板"),
        ("长篇.txt", "无关短篇.txt", "30 万字 vs 1 万字，无关"),
        ("短篇文集.txt", "文集第1篇.txt", "文集 vs 其中一篇"),
        ("短篇文集.txt", "短篇甲.txt", "文集 vs 无关短篇"),
    ):
        r = rel_of(x, y)
        check(f"{why} → {r}", r == judge.NO_REL, metrics(x, y))

    print("\n[3] 必须判重复的（不能因为修复而漏判）")
    r = rel_of("长篇.txt", "长篇_断更版.txt")
    check(f"75% 断更版 → {r}", r == judge.B_SUB_A, metrics("长篇.txt", "长篇_断更版.txt"))
    r = rel_of("长篇.txt", "长篇_改名版.txt")
    check(f"改名版（同内容不同编码）→ {r}", r.startswith("同一版本"),
          metrics("长篇.txt", "长篇_改名版.txt"))

    print("\n[4] 组规模必须合理")
    sizes = sorted((g.size for g in res.groups), reverse=True)
    check("没有超过 60 本的组", all(s <= 60 for s in sizes), f"最大 {sizes[0] if sizes else 0} 本")
    big = [g for g in res.groups if any("文集" in names.get(m, "") for m in g.members)]
    check("文集没有把一堆短篇连成组", not big,
          f"{len(big)} 组含文集成员" if big else "")
    print(f"  组大小：{sizes}")

    if big:
        for g in big:
            print(f"    异常组：{[names[m] for m in g.members][:10]}")

    print("\n" + ("假阳性回归测试：通过" if ok_all else "假阳性回归测试：失败"))
    print(f"（临时目录：{tmp}）")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
