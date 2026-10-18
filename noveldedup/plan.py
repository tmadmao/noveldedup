"""删除规划：**哪些书该删、留哪一本**。纯计算，不碰任何文件。

为什么单独一个模块
------------------
"选谁删"这件事必须能脱离界面单独测试（也方便日后给 CLI 用）。
这里只产出 `DeleteItem` 列表，真正的文件操作在 `deleter.py`。

两档删除，边界很清楚
--------------------
1. `plan_identical`  —— **归一化内容 hash 完全相同**（`Book.l1` 一致）。
   一个字都不差，留哪本只影响整洁度，不影响内容 → **可以一键，零人工判断**。
   注意：l1 是在"删掉广告行、去掉排版差异"之后的全文 hash，
   所以「同一本书一个带广告一个不带」也会落进这一档，正是我们要的。
2. `plan_all_but_keep` —— 全库除"建议保留"外全部预勾选。
   **只勾选、不删除**，用于把大量近似副本先摆到用户面前逐个复核。

绝不做的事
----------
不按"关系标签"自动删。`疑似修订版`、`缺章`、`合订-分卷` 里谁是"正版"是内容判断，
程序猜不出来；交给用户勾选。
"""

from __future__ import annotations

from dataclasses import dataclass

from .judge import quality_key


@dataclass
class DeleteItem:
    bid: int
    path: str
    name: str
    size: int
    reason: str
    keep_name: str = ""
    keep_id: int = 0


def _pick_for_keep(cands: list, group_keep_id: int):
    """一个 hash 簇里留哪本：优先组里已选的"建议保留"，否则按质量排序。"""
    for b in cands:
        if b.id == group_keep_id:
            return b
    return min(cands, key=quality_key)


def plan_identical(books: dict, groups: list) -> list[DeleteItem]:
    """内容 hash 完全相同的多余副本，**每个 hash 簇留 1 本**。

    books: {book_id: cache.Book}；groups: [group.Group]
    返回要删的条目（不含保留项）。
    """
    out: list[DeleteItem] = []
    seen_keep: set[int] = set()

    for g in groups:
        buckets: dict[str, list] = {}
        for m in g.members:
            b = books.get(m)
            # 用 usable 而不是 ok：短文本/编码可疑（suspect）只是"判据可能不稳"，
            # 一字不差的副本照样该清理。用 ok 会让整组静默失效（实测两个完全相同的
            # 1,763 字文件，一键清理返回 0 条）。
            if not b or not b.usable:
                continue
            key = b.l1 or (f"file:{b.file_hash}" if b.file_hash else "")
            if not key:
                continue
            buckets.setdefault(key, []).append(b)

        for _key, cands in buckets.items():
            if len(cands) < 2:
                continue
            keep = _pick_for_keep(cands, g.keep_id)
            seen_keep.add(keep.id)
            for b in cands:
                if b.id == keep.id:
                    continue
                out.append(DeleteItem(
                    bid=b.id, path=b.path, name=b.name, size=b.size,
                    reason="内容完全相同（归一化全文 hash 一致）",
                    keep_name=keep.name, keep_id=keep.id))

    # 防御：万一某本既被选中保留、又被别的簇列进删除（跨组重复），去掉删除项
    out = [d for d in out if d.bid not in seen_keep]
    out.sort(key=lambda d: (-d.size, d.path))
    return out


def plan_all_but_keep(books: dict, groups: list) -> list[DeleteItem]:
    """全库预勾选：每组除"建议保留"外全部列出来。

    ⚠ 只是**勾选建议**，必须由用户逐条复核后再确认删除。
    """
    out: list[DeleteItem] = []
    for g in groups:
        keep = books.get(g.keep_id)
        if not keep:
            continue
        for m in g.members:
            if m == g.keep_id:
                continue
            b = books.get(m)
            if not b or not b.usable:
                continue
            top = max(g.pairs, key=lambda s: s.jac) if g.pairs else None
            out.append(DeleteItem(
                bid=b.id, path=b.path, name=b.name, size=b.size,
                reason=f"与本组《{keep.name}》重复" + (f"（{top.rel}）" if top else ""),
                keep_name=keep.name, keep_id=keep.id))
    out.sort(key=lambda d: (d.keep_name, -d.size, d.path))
    return out
