"""编码探测成功率抽检（计划 §M1）。

用法：
    py -3 tools/probe_encoding.py D:\\小说 [--limit 500]

对每个文本文件报告：探测到的编码、常用字覆盖率、是否会被判为"疑似解码失败"。
这是唯一能发现"整本静默失效"的手段——CJK 占比看不出编码错判，常用字覆盖率能。
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from noveldedup import core         # noqa: E402
from noveldedup.normalize import normalize, read_text   # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    root = Path(argv[1])
    limit = 500
    if "--limit" in argv:
        limit = int(argv[argv.index("--limit") + 1])
    if not root.is_dir():
        print(f"目录不存在：{root}")
        return 2

    files = [p for p in sorted(root.rglob("*.txt")) if p.is_file()][:limit]
    print(f"{root}\n抽检 {len(files)} 个 txt\n")

    encs = Counter()
    suspects: list[tuple[str, str, float]] = []
    ratios: list[float] = []
    for p in files:
        text, enc, bad = read_text(p)
        doc = normalize(text, src_enc=enc, bad=bad)
        encs[enc] += 1
        ratios.append(doc.common_ratio)
        if doc.common_ratio < core.TH.common_min:
            suspects.append((p.name, enc, doc.common_ratio))

    print("编码分布：")
    for enc, n in encs.most_common():
        print(f"  {enc:<12}{n:>6} 个（{n / max(1, len(files)):.1%}）")

    if ratios:
        ratios.sort()
        print(f"\n常用字覆盖率：最低 {ratios[0]:.1%}  中位 {ratios[len(ratios) // 2]:.1%}  "
              f"最高 {ratios[-1]:.1%}")
        print(f"阈值 {core.TH.common_min:.0%} 以下（会被标为疑似解码失败）：{len(suspects)} 个")
        for name, enc, r in suspects[:15]:
            print(f"  {name}（{enc}）{r:.1%}")

    print("\n提示：如果「疑似解码失败」很多，说明书库里混了非中文文件或罕见编码，"
          "不是探测算法坏了。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
