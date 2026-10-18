"""格式提取质量抽检（计划 §M3）。

用法：
    py -3 tools/probe_formats.py D:\\小说 [--limit 200]

对每个支持的文件报告：识别出的格式、编码、提取字数、汉字占比、提示。
用来在真实书库上回答"到底有多少文件提不出正文"——这比拍脑袋猜重要。
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from noveldedup import formats   # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    root = Path(argv[1])
    limit = 2000
    if "--limit" in argv:
        limit = int(argv[argv.index("--limit") + 1])
    if not root.is_dir():
        print(f"目录不存在：{root}")
        return 2

    files = [p for p in sorted(root.rglob("*"))
             if p.is_file() and p.suffix.lower() in formats.supported_exts()]
    print(f"{root}\n共 {len(files):,} 个支持的文件\n")
    print(f"{'格式':<6}{'文件数':>8}{'提不出正文':>12}{'平均字数':>12}")
    by_fmt: dict[str, list[int]] = {}
    bad: list[tuple[str, str]] = []
    for p in files[:limit]:
        try:
            item = formats.extract(p)
        except formats.UnsupportedFormat as exc:
            by_fmt.setdefault(p.suffix.lower() or "?", []).append(0)
            bad.append((p.name, str(exc)))
            continue
        n = len(item.text.strip())
        by_fmt.setdefault(item.fmt, []).append(n)
        if n == 0:
            bad.append((p.name, item.note or "空正文"))

    for fmt, sizes in sorted(by_fmt.items()):
        zero = sum(1 for s in sizes if s == 0)
        avg = sum(sizes) / max(1, len(sizes))
        print(f"{fmt:<6}{len(sizes):>8,}{zero:>12,}{avg:>12,.0f}")

    if bad:
        print(f"\n提不出正文的 {len(bad)} 个（前 15 个）：")
        for name, why in bad[:15]:
            print(f"  {name}：{why}")
    print(f"\npypdf 可用：{formats.pdf_available()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
