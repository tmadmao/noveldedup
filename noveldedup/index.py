"""候选生成：按 hash 高 8 位分 256 桶，桶内排序归并。

为什么不用倒排表 / 不用 O(n²)
-----------------------------
1 万本 × 90 万字 = 90 亿字 → 1.19 亿条指纹。两两比较是 5000 万对，不可能。
倒排表（SQLite 一亿行）也撑不住。

分桶归并的好处：**内存与书库规模无关**。任一时刻只处理一个桶，
桶的大小 = 总指纹数 / 256，只取决于总字数，不取决于有多少本书。

MAX_DF 截断
-----------
某个指纹若出现在 > 50 本书里，判定为"套路话"（"他点了点头说道"这类），
整段丢弃。这是 burstiness 抑制的第一招，也顺手把候选对数量压下来。
"""

from __future__ import annotations

from array import array
from collections import defaultdict

from .core import TH

N_BUCKETS = 256  # 按 hash 高 8 位分桶


def candidates(
    fp_arrays: dict[int, array],
    fps: dict[int, int],
    max_df: int = TH.max_df,
    min_shared: int = TH.min_shared,
    min_ratio: float = TH.min_shared_ratio,
    min_sym: float = TH.min_shared_sym,
    focus: set[int] | None = None,
) -> list[tuple[int, int, int]]:
    """返回 [(book_a, book_b, 共享指纹数)]，已按门槛过滤，按共享数降序。

    fp_arrays: book_id → uint32 排序数组
    fps:       book_id → 该本指纹总数（算相对门槛用）
    focus:     只保留「至少一端在这个集合里」的书对（增量扫描用）
    """
    # 1) 分桶：桶内只存 (hash, book_id)
    hashes: list[array] = [array("I") for _ in range(N_BUCKETS)]
    owners: list[array] = [array("I") for _ in range(N_BUCKETS)]
    for bid, arr in fp_arrays.items():
        for h in arr:
            b = h >> 24
            hashes[b].append(h)
            owners[b].append(bid)

    # 2) 逐桶排序 + 同 hash 归组
    pair_counts: dict[tuple[int, int], int] = defaultdict(int)
    for bi in range(N_BUCKETS):
        hs = hashes[bi]
        if not hs:
            continue
        recs = sorted(zip(hs, owners[bi]))
        hashes[bi] = array("I")  # 尽早释放
        owners[bi] = array("I")

        i = 0
        n = len(recs)
        while i < n:
            h = recs[i][0]
            j = i
            books_here: list[int] = []
            while j < n and recs[j][0] == h:
                b = recs[j][1]
                if not books_here or books_here[-1] != b:
                    books_here.append(b)
                j += 1
            k = len(books_here)
            if 2 <= k <= max_df:
                for x in range(k):
                    bx = books_here[x]
                    if focus is not None and bx not in focus:
                        # 老-老 对上一轮已经配过，不会变；只需要含新书的对
                        if not any(by in focus for by in books_here[x + 1:]):
                            continue
                    for y in range(x + 1, k):
                        by = books_here[y]
                        if focus is not None and bx not in focus and by not in focus:
                            continue
                        pair_counts[(bx, by)] += 1
            i = j

    # 3) 门槛过滤
    out: list[tuple[int, int, int]] = []
    for (a, b), shared in pair_counts.items():
        if shared < min_shared:
            continue
        smaller = min(fps.get(a, 0), fps.get(b, 0))
        if smaller and shared / smaller < min_ratio:
            continue
        # 对称门槛：短篇被长篇包含时 shared/min 接近 1，但 shared/max 极小
        larger = max(fps.get(a, 0), fps.get(b, 0))
        if larger and shared / larger < min_sym:
            continue
        out.append((a, b, shared))
    out.sort(key=lambda t: -t[2])
    return out
