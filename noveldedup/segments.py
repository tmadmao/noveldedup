"""证据段：把匹配点并成"连续对得上的文本段"，用于界面并排显示。

这是本项目相对漫画/视频查重的核心差异：不只给一个相似度小数，
而是能指出"从第 3 章第 2 段起，连续 4,820 字与对方一致"。

两个坐标要分清
--------------
pos_a / pos_b   归一化文本里的位置（指纹匹配发生在这一层）
orig / chapter  原文里的偏移与章节（给人看的）
两者通过 NormDoc.orig_offset() / chapter_of() 换算，误差 < 一行。
"""

from __future__ import annotations

from dataclasses import dataclass

from .core import K


@dataclass
class Segment:
    pos_a: int
    pos_b: int
    length: int          # 归一化字符数
    chapter_a: int = 0
    chapter_b: int = 0
    chapter_a_end: int = 0
    chapter_b_end: int = 0
    title_a: str = ""
    title_b: str = ""
    head_a: str = ""     # 原文开头若干字（CSV 用）
    head_b: str = ""
    orig_a: int = 0      # 原文偏移（界面定位用）
    orig_b: int = 0
    excerpt_a: str = ""  # 带前置上下文的片段（界面并排显示用）
    excerpt_b: str = ""
    mark_a: int = 0      # excerpt 里"匹配开始"的下标
    mark_b: int = 0

    @property
    def readable(self) -> str:
        def span(ch: int, ch_end: int, title: str) -> str:
            head = f"第{ch}章" if ch else "开头"
            if ch_end and ch_end > ch:
                head += f"→第{ch_end}章"
            t = f"《{title}》" if title else ""
            return head + t

        return (f"A {span(self.chapter_a, self.chapter_a_end, self.title_a)} ↔ "
                f"B {span(self.chapter_b, self.chapter_b_end, self.title_b)}，"
                f"连续 {self.length:,} 字")


def build(pairs: list[tuple[int, int]], w: int = 40, k: int = K, limit: int = 40) -> list[Segment]:
    """把匹配点并成匹配段，按长度降序返回前 limit 段。

    段的切分标准与 judge.coverage 一致（gap_max = 3 倍指纹间距），
    这样"段覆盖的字数"和"段列表"讲的是同一件事，不会出现矛盾数字。
    """
    if not pairs:
        return []
    gap_max = 3 * (w + 1) // 2
    min_seg = w + k - 1
    ps = sorted(pairs)

    segs: list[Segment] = []
    start_a, start_b = ps[0]
    prev_a, prev_b = ps[0]
    for pa, pb in ps[1:]:
        if pa - prev_a > gap_max or pb - prev_b > gap_max:
            length = prev_a - start_a
            if length >= min_seg:
                segs.append(Segment(start_a, start_b, length))
            start_a, start_b = pa, pb
        prev_a, prev_b = pa, pb
    length = prev_a - start_a
    if length >= min_seg:
        segs.append(Segment(start_a, start_b, length))

    segs.sort(key=lambda s: -s.length)
    return segs[:limit]


def render(segs: list[Segment], doc_a, doc_b, text_a: str = "", text_b: str = "",
           head: int = 60) -> list[Segment]:
    """填充章节号、标题、原文偏移。

    ⚠ 扫描期**不缓存原文片段**。1.7 万对重复 × 每对几十段 × 每段几百字，
      几 GB 内存就这样没了（真实书库上踩到过）。
      片段改为界面按需读取（见 gui.App._excerpt），扫描期只存整数偏移。
    """
    for s in segs:
        oa = doc_a.orig_offset(s.pos_a)
        ob = doc_b.orig_offset(s.pos_b)
        s.chapter_a, s.title_a = doc_a.chapter_of(oa)
        s.chapter_b, s.title_b = doc_b.chapter_of(ob)
        s.chapter_a_end, _ = doc_a.chapter_of(doc_a.orig_offset(s.pos_a + s.length))
        s.chapter_b_end, _ = doc_b.chapter_of(doc_b.orig_offset(s.pos_b + s.length))
        s.orig_a, s.orig_b = oa, ob
        if text_a and text_b:      # 只有显式传入原文时才截片段（CLI 一般不传）
            pad, take = 80, 400
            pa, pb = max(0, oa - pad), max(0, ob - pad)
            s.excerpt_a = text_a[pa : oa + take]
            s.excerpt_b = text_b[pb : ob + take]
            s.mark_a, s.mark_b = oa - pa, ob - pb
        s.head_a = " ".join(text_a[oa : oa + head].split())
        s.head_b = " ".join(text_b[ob : ob + head].split())
    return segs
