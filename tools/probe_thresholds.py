"""判据阈值标定辅助（计划 §3.4.3 ★ 需真实书库 + 人工真值）。

用法：
    py -3 tools/probe_thresholds.py D:\\小说 --out 阈值观测.tsv

把所有"被判为重复"的书对的指标导成 TSV，供人工核对：
  shared / chain / jac / cont_ab / cont_ba / cov_a / cov_b / 关系标签

为什么不做自动标定：真值必须人给。工具能做的是把"边界上的样本"摊开，
让你一眼看出哪条阈值切错了。经验做法：
  1. 先按 jac 升序看最低的一批 —— 这些是"勉强判重"的，最容易是假阳性
  2. 再按 cov 看 0.95~0.99 之间的一批 —— 广告/错字多的样本落在这里
  3. 挑 30 对确认无关的、30 对确认真重复的，反过来验阈值
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from noveldedup import core, engine   # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    root = Path(argv[1])
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else Path("阈值观测.tsv")
    if not root.is_dir():
        print(f"目录不存在：{root}")
        return 2

    log = core.setup_logging(False, logfile=core.data_dir() / "run.log")
    print(f"扫描 {root} …（用缓存，不会重算已算过的书）")
    res = engine.scan(root, workers=None, log=log, with_evidence=False)
    names = {b.id: b.name for b in res.books.values()}

    rows = sorted(res.dup_pairs, key=lambda s: s.jac)
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        f.write("A\tB\tshared\tchain\tchain/shared\tjac\tcont_ab\tcont_ba\t"
                "cov_a\tcov_b\tseg_a\tseg_b\trel\n")
        for s in rows:
            f.write("\t".join([
                names.get(s.a, "?"), names.get(s.b, "?"),
                str(s.shared), str(s.chain),
                f"{s.chain / max(1, s.shared):.3f}", f"{s.jac:.4f}",
                f"{s.cont_ab:.4f}", f"{s.cont_ba:.4f}",
                f"{s.cov_a:.4f}", f"{s.cov_b:.4f}",
                str(s.seg_a), str(s.seg_b), s.rel]) + "\n")

    print(f"\n判为重复 {len(rows):,} 对，已导出：{out}")
    if rows:
        jacs = [s.jac for s in rows]
        print(f"Jaccard 分布：最低 {jacs[0]:.3f}  中位 {jacs[len(jacs) // 2]:.3f}  "
              f"最高 {jacs[-1]:.3f}")
        print("\n最可疑的 10 对（Jaccard 最低，最可能是假阳性）：")
        for s in rows[:10]:
            print(f"  {s.jac:.3f}  {names.get(s.a, '?')[:28]}  ↔  {names.get(s.b, '?')[:28]}"
                  f"   [{s.rel}]")
    print("\n当前阈值：")
    for k in ("min_shared", "chain_floor", "in_order", "contain_hi", "contain_lo",
              "same_ver", "cov_same", "cov_same_jac", "jac_same", "jac_rev", "max_df"):
        print(f"  {k} = {getattr(core.TH, k)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
