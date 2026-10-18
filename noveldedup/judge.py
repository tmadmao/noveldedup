"""判据：一对一匹配 → LIS 链 → 字符覆盖率 → 关系分类 → 建议保留。

三个指标各管一件事，别混用
--------------------------
Jaccard        |F(A)∩F(B)| / |F(A)∪F(B)|   —— 整体相似度，但对"插入广告"会掉
c(A,B)         包含度（Broder 1997）        —— 判"一方是另一方的子集"（缺章/合订）
cov(A)         匹配段在**字符**上的覆盖率    —— 判"只是插入了内容/换了排版"

为什么不能只用 Jaccard（实测）
------------------------------
同一本书每 1000 字插一条广告：Jaccard 掉到 0.95，cov 仍是 0.99。
如果只按 Jaccard 打标签，用户会看到"疑似修订版"——明明一个字都没改。

为什么用 LIS 而不是命中数（漫画项目 v1.1 实测）
-----------------------------------------------
"共享 N 句套话" → 匹配点位置乱跳 → LIS 很短；
"沿文本顺序连续几百字都对得上" → 匹配点成斜线 → LIS 很长。
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field

from .core import K, TH

# 关系标签（顺序即判定顺序）
NO_REL = "无关系"
SAME_INSERT = "同一版本（仅插入广告/换排版）"
SAME_MINOR = "同一版本（有细微字符差异）"
SAME_EXACT = "同一版本"
A_SUB_B = "A ⊂ B（A 缺章）"
B_SUB_A = "B ⊂ A（B 缺章）"
REORDERED = "章节顺序不同"
REVISED = "疑似修订版"
TYPO = "疑似改字版"


@dataclass
class PairStat:
    a: int
    b: int
    shared: int = 0        # **一对一**共享数（用于 jac / 包含度 / 链长比例）
    raw_shared: int = 0    # 全量匹配数（会被高频片段放大，只用于覆盖率）
    jac: float = 0.0
    cont_ab: float = 0.0
    cont_ba: float = 0.0
    chain: int = 0
    cov_a: float = 0.0
    cov_b: float = 0.0
    seg_a: int = 0
    seg_b: int = 0
    rel: str = NO_REL
    pairs: list[tuple[int, int]] = field(default_factory=list)

    @property
    def is_dup(self) -> bool:
        return self.rel != NO_REL

    @property
    def is_contain(self) -> bool:
        return self.rel in (A_SUB_B, B_SUB_A)


def matching(pa, ha, pb, hb) -> tuple[list[tuple[int, int]], int, list[tuple[int, int]]]:
    """返回 (一对一匹配, 共享数, 全量匹配)。

    - **全量匹配**用于算字符覆盖率：一个 B 位置被多个 A 位置命中是正常的
      （高频短句），不参与会让覆盖率凭空掉下来。
    - **一对一匹配**只用于 LIS 链长：抑制 burstiness 放大（Jégou 2009 的 MMR），
      否则一段高频套话就能把"链长"刷上去。
    """
    where: dict[int, int] = {}
    for p, x in zip(pb, hb):
        where.setdefault(x, p)  # 保留最靠前的位置
    raw: list[tuple[int, int]] = []
    one2one: list[tuple[int, int]] = []
    seen_b: set[int] = set()
    for p, x in zip(pa, ha):
        q = where.get(x)
        if q is None:
            continue
        raw.append((p, q))
        if q not in seen_b:
            seen_b.add(q)
            one2one.append((p, q))
    return one2one, len(raw), raw


def lis_len(pairs: list[tuple[int, int]]) -> int:
    """按 A 位置排序后，对 B 位置求最长严格递增子序列。"""
    if not pairs:
        return 0
    seq = [q for _p, q in sorted(pairs)]
    tails: list[int] = []
    for v in seq:
        i = bisect_left(tails, v)
        if i == len(tails):
            tails.append(v)
        else:
            tails[i] = v
    return len(tails)


def coverage(pairs: list[tuple[int, int]], n_doc: int, w: int = 40, k: int = K) -> tuple[float, int]:
    """匹配段对**字符**的覆盖率 + 段数。

    ⚠ 段内允许的间隙必须跟着指纹间距走：winnowing 平均间距 ≈ (w+1)/2，
      所以 gap_max 取 3 倍间距、最短段取 t = w+k-1。
      写死 60 字在 w=200（间距 98）时会把每一对都切成独立段，覆盖率算成 0。
    """
    if not pairs:
        return 0.0, 0
    gap_max = 3 * (w + 1) // 2
    min_seg = w + k - 1
    ps = sorted(p for p, _q in pairs)
    total = 0
    nseg = 0
    start = prev = ps[0]
    for x in ps[1:]:
        if x - prev > gap_max:
            if prev - start >= min_seg:
                total += prev - start
                nseg += 1
            start = x
        prev = x
    if prev - start >= min_seg:
        total += prev - start
        nseg += 1
    return total / max(1, n_doc), nseg


def classify(st: PairStat, n_a: int, n_b: int, w: int = 40) -> str:
    """按顺序判关系。

    ⚠ **相似度底线必须在最前面**（v1.0.1 修的致命 bug）：
      "章节顺序不同"这条判据原来排在 Jaccard 检查之前，只要
      「共享几百个指纹 + 顺序散乱」就返回重复，**根本不看整体相似度**。
      真实书库里因此出现：
          4,250 字的短篇 ↔ 352 万字的长篇，jac = 0.0012（0.12%）
          被判成"章节顺序不同" → 一个合集文件把 3,216 本无关的书连成一组。

      顺序乱不影响 Jaccard（它是集合运算，与位置无关），
      所以"内容基本相同只是顺序乱"必然 jac 很高 —— 反过来，
      jac 很低就绝不可能是"顺序不同"，只能是"根本不相关"。
    """
    if st.shared < TH.min_shared or st.chain < TH.chain_floor:
        return NO_REL
    # 连"疑似改字版"的门槛都够不上 → 无论顺序如何都不是同一本书
    if st.jac < TH.jac_rev:
        return NO_REL

    ca, cb = st.cont_ab, st.cont_ba
    if st.chain < TH.in_order * st.shared:
        return REORDERED
    # 完全一模一样（一字不差）要单独判：否则会被下面的覆盖率判据归到
    # "仅插入广告/换排版"，语义就错了（导出再导入也会因此换标签）。
    if st.jac >= 0.9999 and min(ca, cb) >= 0.9999:
        return SAME_EXACT
    if st.cov_a >= TH.cov_same and st.cov_b >= TH.cov_same and st.jac >= TH.jac_same:
        return SAME_INSERT if st.jac >= TH.cov_same_jac else SAME_MINOR
    if cb >= TH.contain_hi and ca <= TH.contain_lo:
        return B_SUB_A
    if ca >= TH.contain_hi and cb <= TH.contain_lo:
        return A_SUB_B
    if st.jac >= TH.jac_same and min(ca, cb) >= TH.same_ver:
        return SAME_EXACT
    if st.jac >= TH.jac_same:
        return REVISED
    if st.jac >= TH.jac_rev:
        return TYPO
    return NO_REL


def judge_pair(a_id: int, b_id: int, fa, fb, n_a: int, n_b: int, w: int) -> PairStat:
    """fa/fb = (pos_array, hash_array)。返回 PairStat（含关系标签）。"""
    one2one, raw_shared, raw = matching(fa[0], fa[1], fb[0], fb[1])
    uniq = len(one2one)
    st = PairStat(a=a_id, b=b_id, shared=uniq, raw_shared=raw_shared, pairs=raw)
    na, nb = len(fa[1]), len(fb[1])
    if uniq == 0 or na == 0 or nb == 0:
        return st
    # jac 与包含度用一对一计数：全量匹配会被高频片段放大（同一个 B 位置被几十个
    # A 指纹命中），把"短篇 vs 长篇"的包含度算成 >1，反过来制造假阳性。
    st.jac = uniq / (na + nb - uniq)
    st.cont_ab = min(1.0, uniq / na)
    st.cont_ba = min(1.0, uniq / nb)
    st.chain = lis_len(one2one)          # 链长用一对一，抑制 burstiness
    st.cov_a, st.seg_a = coverage(raw, n_a, w)                       # 覆盖率用全量
    st.cov_b, st.seg_b = coverage([(q, p) for p, q in raw], n_b, w)
    st.rel = classify(st, n_a, n_b, w)
    return st


# ------------------------------------------------------------------ 建议保留


def quality_key(b, st: StarStat | None = None) -> tuple:
    """越大越该保留。逐级比较，最后一档用路径做稳定 tie-break。

    维度按文本场景重排（视频项目是时长/分辨率/码率，这里是完整性/格式/编码/污染）：
      1. 章节数（完整版优先；断更版往往章节少）
      2. 归一化字数（同章节数时内容更全的优先）
      3. 格式质量（epub/docx 有目录结构，优于裸 txt）
      4. 广告行少（干净版优先）
      5. UTF-8 优于 GBK（编码规范性）
      6. 文件名"干净"（带「校对版」「全本」的通常是整理过的）
      7. 路径（保证结果可复现，永不漂移）
    """
    # 格式质量：有目录结构的 epub/docx 比裸 txt 更值得留（计划 §3.7 第 2 条）
    fmt_rank = {"epub": 3, "docx": 3, "html": 2, "txt": 1, "pdf": 0}.get(
        (getattr(b, "fmt", "") or "txt").lower(), 1)
    enc_score = 1 if (b.enc or "").lower().startswith("utf-8") else 0
    name = b.name
    clean = 0
    for kw in ("校对", "精校", "全本", "完结", "番外"):
        if kw in name:
            clean += 1
    return (
        -b.chapters,          # 章节多 → 排前面（取负因为要降序）
        -b.norm_chars,
        -fmt_rank,            # epub/docx 优于 txt
        b.ad_lines,
        -enc_score,
        -clean,
        len(name),
        b.path,
    )


def omnibus_advice(group, books: dict) -> tuple[str, str]:
    """合订-分卷组的**二选一**建议（计划 §3.7）。

    这种组不该"选一个丢一个"，两条路都合理，取决于用户想要什么：
    省空间 → 留全集；方便读 → 留分卷。所以把代价算清楚给用户自己定。
    """
    sup = books.get(group.superset_id)
    if not sup:
        return "", ""
    vols = [(books.get(v), c) for v, c in group.volumes]
    vols = [(b, c) for b, c in vols if b]
    sup_mb = sup.size / 2 ** 20
    vol_mb = sum(b.size for b, _ in vols) / 2 ** 20

    keep_all = (f"保留全集「{sup.name}」（{sup_mb:,.1f} MB，{sup.chapters} 章）："
                f"删掉 {len(vols)} 个分卷，省 {vol_mb:,.1f} MB。"
                f"代价：没有分卷目录，想单看某一卷要去书里找。")
    keep_vols = (f"保留 {len(vols)} 个分卷（合计 {vol_mb:,.1f} MB，"
                 f"覆盖全集 {group.volumes_cover:.0%}）："
                 f"删除全集，省 {sup_mb:,.1f} MB。"
                 f"代价：若覆盖不足 100%，可能少一点点内容。")
    return keep_all, keep_vols


@dataclass
class StarStat:
    book_id: int
    chapters: int
    norm_chars: int
    ad_lines: int
    enc: str
    name: str


def pick_keep(books: list) -> tuple[int, str]:
    """返回 (建议保留的 book_id, 判决理由)。books 是 cache.Book 列表。"""
    if not books:
        return 0, ""
    if len(books) == 1:
        return books[0].id, "组内唯一"
    best = min(books, key=quality_key)
    reasons: list[str] = []
    ch = max(b.chapters for b in books)
    if ch > 0 and best.chapters == ch:
        reasons.append(f"章节最全（{ch} 章）")
    mx = max(b.norm_chars for b in books)
    if best.norm_chars == mx and len({b.norm_chars for b in books}) > 1:
        reasons.append(f"内容最多（{mx:,} 字）")
    mn_ad = min(b.ad_lines for b in books)
    if best.ad_lines == mn_ad and mn_ad < max(b.ad_lines for b in books):
        reasons.append(f"广告最少（{mn_ad} 行）")
    if (best.enc or "").lower().startswith("utf-8") and any(
        not (b.enc or "").lower().startswith("utf-8") for b in books
    ):
        reasons.append("编码为 UTF-8")
    if not reasons:
        reasons.append("各维度持平，取路径排序最前者（保证结果可复现）")
    return best.id, "；".join(reasons)
