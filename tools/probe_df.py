"""标定 max_df（套话截断阈值，计划 §M1 ★ 需真实书库）。

用法（先跑过至少一次扫描，缓存里要有指纹）：
    py -3 tools/probe_df.py D:\\小说

做法：读缓存里的每本指纹，统计"每个指纹出现在多少本书里"的分布。
某个指纹出现在很多本书里 = 共享套话（"他点了点头说道"这类），
超过 max_df 就整段丢弃；所以 max_df 要卡在"正常重复"与"套路话"之间。

判读：
  · 绝大多数指纹 df=1（只有一本书有）→ 正常
  · df 在 3~20 之间的指纹很少 → 说明书库来源分散，max_df 可以放心取小
  · df 在 50 以上还有一大堆 → 要么书库里有大量同系列/同站点书，要么 max_df 太小
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from noveldedup import core   # noqa: E402
from noveldedup.cache import Cache   # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    root = Path(argv[1])
    if not root.is_dir():
        print(f"目录不存在：{root}")
        return 2

    cache = Cache(core.cache_dir_for(root))
    try:
        books = cache.all_books()
        if not books:
            print("缓存是空的，请先跑一次 --scan")
            return 2
        print(f"{root}\n缓存里 {len(books):,} 本书，正在统计指纹的文档频率 …\n")

        df: Counter = Counter()
        for b in books:
            arr = cache.read_fp(b.id)
            for h in set(arr):
                df[h] += 1
    finally:
        cache.close()

    if not df:
        print("没有指纹数据，请先跑一次 --scan")
        return 2

    total = len(df)
    buckets = [(1, 1), (2, 2), (3, 5), (6, 10), (11, 50), (51, 100), (101, 10 ** 9)]
    print(f"不同指纹总数 {total:,}\n")
    print(f"{'文档频率':<14}{'指纹数':>14}{'占比':>9}")
    for lo, hi in buckets:
        n = sum(1 for h, c in df.items() if lo <= c <= hi)
        label = f"{lo}" if lo == hi else (f"{lo}+" if hi >= 10 ** 9 else f"{lo}-{hi}")
        print(f"  df={label:<11}{n:>14,}{n / total:>9.2%}")

    print("\n当前阈值：max_df =", core.TH.max_df)
    for cand in (20, 50, 100, 200):
        dropped = sum(1 for c in df.values() if c > cand)
        print(f"  max_df={cand:<4} 会丢弃 {dropped:>10,} 个指纹（{dropped / total:.3%}）")
    print("\n建议：选一个「丢弃比例 < 1%、且能盖住 df 长尾」的值；换库要重新标定。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
