"""生成 11 组已知答案的测试语料（计划 §M0）。

用法：
    py -3 tools/make_testdata.py [目标目录]
默认写到 ./testdata（会先清空该目录）。

生成后会写一个「答案.txt」，写明每个文件期望被判成什么关系，
方便人工核对——**自检的真值必须是人能看懂的**，不能只有程序知道。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from noveldedup import selftest   # noqa: E402


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path("testdata")
    if root.exists():
        print(f"清空 {root} …")
        shutil.rmtree(root)
    root.mkdir(parents=True)

    expect = selftest.build_corpus(root)
    print(f"已生成 {len(expect)} 个文件到 {root}\n")

    lines = ["# 测试语料答案表", "",
             "每个文件与「基准文件」的关系（基准 = g01_甲版.txt）：", ""]
    width = max(len(n) for n in expect)
    for name, want in sorted(expect.items()):
        lines.append(f"  {name:<{width}}  →  {want or '（基准文件）'}")
    lines += ["", "判定标准：",
              "  同一版本 / 疑似修订版 / 疑似改字版  → 应判为重复",
              "  A ⊂ B / B ⊂ A                    → 应判为重复（一方缺章）",
              "  章节顺序不同                       → 应判为重复（顺序被打乱）",
              "  不判重 / 空                        → 不应进入重复组"]
    text = "\n".join(lines)
    (root / "答案.txt").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
