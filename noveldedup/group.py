"""分组：并查集 + 组代表互验（**按边类型区别对待**）+ 结构标签。

为什么不能照搬漫画/视频项目的组代表互验
----------------------------------------
在视频/漫画里，"组代表互验"是为了防止「同系列各卷共享封面/版权页」被连环并组：
A≈B、B≈C 但 A≉C 时不该合成一组。

但小说场景有一条**合法**的传递链：
    上卷.txt、中卷.txt、全集.txt
上卷和中卷**本来就不相似**（包含度≈0），它们只通过"都被全集包含"产生关联。
如果严格要求两两互验，这三个文件会被拆成 3 个"重复组"——而它们明明是同一部书。

所以规则是：
    resemblance 边（相似）  → 合并**要求**组代表互验
    containment 边（子集）  → 合并**不要求**，因为"整体-部分"天然不可传递为相似
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .core import TH
from .judge import (A_SUB_B, B_SUB_A, PairStat, pick_keep)

STRUCT_SINGLE = "单链重复"
STRUCT_OMNIBUS = "合订-分卷"
STRUCT_MIXED = "混合"


class DSU:
    def __init__(self) -> None:
        self.parent: dict[int, int] = {}
        self.count: dict[int, int] = {}

    def find(self, x: int) -> int:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def size(self, x: int) -> int:
        return self.count.get(self.find(x), 1)

    def union(self, a: int, b: int) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        self.parent[rb] = ra
        self.count[ra] = self.size(ra) + self.size(rb)
        self.count.pop(rb, None)
        return True

    def groups(self) -> dict[int, list[int]]:
        out: dict[int, list[int]] = {}
        for x in list(self.parent):
            out.setdefault(self.find(x), []).append(x)
        return out


@dataclass
class Group:
    members: list[int] = field(default_factory=list)
    pairs: list[PairStat] = field(default_factory=list)
    keep_id: int = 0
    keep_reason: str = ""
    structure: str = STRUCT_SINGLE
    skipped: list[tuple[int, int, str]] = field(default_factory=list)
    # 合订-分卷专用：全集 id、分卷 [(id, 该分卷覆盖全集的%)]、合计覆盖率
    superset_id: int = 0
    volumes: list[tuple[int, float]] = field(default_factory=list)
    volumes_cover: float = 0.0

    @property
    def size(self) -> int:
        return len(self.members)

    @property
    def rel(self) -> str:
        return self.pairs[0].rel if self.pairs else ""


def build(stats: list[PairStat], books: dict[int, object]) -> list[Group]:
    """stats 是全部判为重复的书对；books 是 id → Book。"""
    if not stats:
        return []

    edge_set = {frozenset((s.a, s.b)): s for s in stats}
    has_edge = lambda x, y: frozenset((x, y)) in edge_set  # noqa: E731

    dsu = DSU()
    reps: dict[int, int] = {}

    def rep_of(x: int) -> int:
        r = dsu.find(x)
        if r not in reps:
            reps[r] = x
        return reps[r]

    def merge(a: int, b: int, verify: bool) -> bool:
        ra, rb = dsu.find(a), dsu.find(b)
        if ra == rb:
            return True
        # ★ 组大小上限：正常一部作品不会有 60 个"副本"。
        #   超过这个数说明是「合集/文集文件把一堆无关的书连起来了」，
        #   真实案例：一个 2400 万字的合集把 3,216 本毫无关系的书连成一组。
        if dsu.size(ra) + dsu.size(rb) > TH.max_group:
            return False
        pa, pb = rep_of(a), rep_of(b)
        if verify:
            # 组代表互验：代表之间、或代表与对方成员之间必须有一条直接证据
            if not (has_edge(pa, pb) or has_edge(pa, b) or has_edge(pb, a)):
                return False
        dsu.union(a, b)
        root = dsu.find(a)
        reps[root] = pa if _keep_order(pa, books) <= _keep_order(pb, books) else pb
        return True

    skipped: list[tuple[int, int, str]] = []
    # 1) 先并 containment 边（不需要互验）
    for s in stats:
        if s.is_contain:
            merge(s.a, s.b, verify=False)
    # 2) 再并 resemblance 边（需要互验）
    for s in stats:
        if not s.is_contain:
            if not merge(s.a, s.b, verify=True):
                skipped.append((s.a, s.b, s.rel))

    # 3) 组装
    raw = dsu.groups()
    out: list[Group] = []
    for _root, members in raw.items():
        if len(members) < 2:
            continue
        members = sorted(members)
        ms = set(members)
        gpairs = [s for s in stats if s.a in ms and s.b in ms]
        g = Group(members=members, pairs=gpairs, skipped=[])
        blist = [books[m] for m in members if m in books]
        g.structure, g.superset_id, g.volumes, g.volumes_cover = _structure(g, stats)
        g.keep_id, g.keep_reason = pick_keep(blist)
        if g.structure == STRUCT_OMNIBUS and g.superset_id:
            # 合订组：内容最全的一定是全集，建议保留它（另一条路是保分卷，见 judge.omnibus_advice）
            g.keep_id = g.superset_id
            g.keep_reason = (f"合订本：由 {len(g.volumes)} 个分卷合成，"
                             f"分卷合计覆盖 {g.volumes_cover:.0%}")
        out.append(g)

    for a, b, rel in skipped:
        for g in out:
            if a in g.members:
                g.skipped.append((a, b, rel))
    out.sort(key=lambda g: (-g.size, -min((p.jac for p in g.pairs), default=0)))
    return out


def _keep_order(book_id: int, books: dict) -> int:
    """越小越优先当组代表（用完整度排序，稳定）。"""
    b = books.get(book_id)
    if not b:
        return 1 << 30
    return (-b.chapters, -b.norm_chars, b.path)


def _structure(g: Group, stats: list[PairStat]) -> tuple[str, int, list[tuple[int, float]], float]:
    """识别「1 个超集 + N 个子集」的结构，并算出分卷对全集的覆盖。

    覆盖率的算法（近似，够用）：
      某分卷对全集的覆盖率 = 该分卷的共享指纹数 / 全集指纹数
      合计 = 各分卷覆盖率之和（上限 1.0）
    严格算"子集指纹并集覆盖超集"需要把每本书的指纹集合都摊开算，
    在 上万本的书库上代价太高；而分卷之间内容不重叠，
    求和就是并集的下界近似——只要 ≥95% 就足以判定"这就是同一部书"。
    """
    if g.size < 2:
        return STRUCT_SINGLE, 0, [], 0.0

    supersets: dict[int, dict[int, float]] = {}
    for s in g.pairs:
        if s.rel == A_SUB_B:            # A ⊂ B → B 是超集，A 覆盖 B 的比例是 cont_ba
            supersets.setdefault(s.b, {})[s.a] = s.cont_ba
        elif s.rel == B_SUB_A:          # B ⊂ A → A 是超集，B 覆盖 A 的比例是 cont_ab
            supersets.setdefault(s.a, {})[s.b] = s.cont_ab

    others = set(g.members)
    for sup, covs in supersets.items():
        if set(covs) | {sup} != others:
            continue
        cover = min(1.0, sum(covs.values()))
        vols = sorted(covs.items(), key=lambda kv: kv[0])
        return STRUCT_OMNIBUS, sup, vols, cover

    return (STRUCT_MIXED if supersets else STRUCT_SINGLE), 0, [], 0.0
