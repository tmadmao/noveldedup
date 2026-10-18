"""编码探测 + 归一化 + 位置映射 + 章节索引。

归一化是判重的根基。它要把"同一本书的不同副本"之间的所有**非内容**差异消掉：
编码、空白、全角半角、标点、段首缩进、换行、站点广告行。

实现要点（都有实测依据，见 docs/真实语料验证-2026-10-18.md）
--------------------------------------------------------------
1. 编码探测：严格 UTF-8 优先，退到 GB18030，再用**常用字覆盖率**校验。
   ⚠ 不能用 CJK 占比做主要判据——实测把 UTF-8 错解成 GB18030 时，
   CJK 占比只从 81.17% 掉到 81.02%（完全无感），而常用字覆盖率从 78% 崩到 2.2%。
2. 归一化用**单张 translate 表**一次 C 级扫描完成（比 NFKC + 逐字符循环快 2.8 倍）。
   ⚠ 全角折叠只能保留字母数字；全角标点必须映射成 None，否则标点会漏进输出。
3. 按行归一化并记录行映射，用于把归一化位置回溯到原文（证据展示）。
4. 广告行是**逐条**剔的，同时记下"删在哪、删了多少"：整篇 `sub("")` 会让后面所有
   偏移少掉被删的长度、误差累加（`orig_offset()` 回指的位置会越走越偏）。
"""

from __future__ import annotations

import re
from array import array
from collections import Counter
from bisect import bisect_right
from dataclasses import dataclass, field
from itertools import accumulate
from pathlib import Path

from .core import AD_MAXLEN, TH

# ---------------------------------------------------------------- 常用字表
# 现代汉语高频字（按频次大致排序）。只用前 300 个做编码自检。
_COMMON = (
    "的一是不了在人有我他这个们中来上大为和国地到以说时要就出会可也你对生能而子那得于着"
    "下自之年过发后作里用道行所然家种事成方多经么去法学如都同现当没动面起看定天分还进好"
    "小部其些主样理心她本前开但因只从想实日军者意无力它与长把机十民第公此已工使情明性知"
    "全三又关点正业外将两高间由问很最重并物手应战向头文体政美相见被利什二等产或新己制身"
    "果加西斯月话合回特代内信表化老给世位次度门任常先海通教儿原东声提立及比员解水名真论"
    "处走义各入几口认条平系气题活尔更别打女变四神总何电数安少报才结反受目太量再感建务做"
    "接必场件计管期市直德资命山金指克许统区保至队形社便空决治展马科司五基眼书非则听白却"
    "界达光放强即像难且权思王象完设式色路记南品住告类求据程北边死张该交规万取拉格望觉术"
    "领共确传师观清今切院让识候带导争运笑飞风步改收根干造言联持组每济车亲极林服快办议往"
    "元英士证近失转夫令准布始怎呢存未远叫台单影具罗字爱击流备兵连调深商算质团集百需价花"
    "党华城石级整府离况亚请技际约示复病息究线似官火断精满支视消越器容照须九增研写称企八"
)
COMMON_SET = frozenset(_COMMON)

# ---------------------------------------------------------------- 广告行

AD_HINT = re.compile(
    r"(www\.|https?:|本书由|首发|转载|手机阅读|txt下载|请记住本站|最新章节|无弹窗|免费阅读|"
    r"更新最快|加入书签|投推荐票|求收藏|求月票|笔趣阁|顶点小说)",
    re.I,
)

# 整篇一次性剔除广告行：`^.*关键词.*$` 在 C 层扫描，比逐行 Python 调 regex 快得多
# （实测逐行判定 10 万行要 1.5~2 秒，这一步是归一化的主要开销之一）。
# 回调只在真正命中的行上执行，用于二次确认行长。
AD_LINE_RE = re.compile(r"^.*(?:www\.|https?:|本书由|首发|转载|手机阅读|txt下载|请记住本站|"
                        r"最新章节|无弹窗|免费阅读|更新最快|加入书签|投推荐票|求收藏|求月票|"
                        r"笔趣阁|顶点小说).*$", re.I | re.M)

# ---------------------------------------------------------------- 归一化表


def _build_table() -> dict[int, str | None]:
    tbl: dict[int, str | None] = {}
    for o in range(0x10000):
        if 0xFF01 <= o <= 0xFF5E:            # 全角 ASCII
            t = o - 0xFEE0
            if 0x30 <= t <= 0x39 or 0x61 <= t <= 0x7A:
                tbl[o] = chr(t)
            elif 0x41 <= t <= 0x5A:
                tbl[o] = chr(t + 32)
            else:
                tbl[o] = None                # 全角标点 → 删
            continue
        if (0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF
                or 0x30 <= o <= 0x39 or 0x61 <= o <= 0x7A):
            continue                          # 原样保留
        if 0x41 <= o <= 0x5A:
            tbl[o] = chr(o + 32)
            continue
        tbl[o] = None                         # 空白/标点/零宽/其余 → 删
    return tbl


TABLE = _build_table()

# ---------------------------------------------------------------- 章节

_CH_NUM = r"[0-9零〇一二三四五六七八九十百千万两]+"
CHAP_RE = re.compile(rf"^[ \t\u3000]*(第\s*{_CH_NUM}\s*[章节回卷][^\n]{{0,40}})$", re.M)


# ---------------------------------------------------------------- 编码

def _score_common(text: str) -> float:
    """常用字覆盖率：CJK 字符里有多少落在前 300 高频字中。

    这是**编码错判的唯一可靠判据**（实测：错解时 CJK 占比仍是 81%，
    但常用字覆盖率从 78% 崩到 2.2%）。用 Counter 一次 C 层扫描，别写逐字符循环。
    """
    counts = Counter(text)
    cjk = 0
    hit = 0
    for ch, n in counts.items():
        if 0x4E00 <= ord(ch) <= 0x9FFF:
            cjk += n
            if ch in COMMON_SET:
                hit += n
    return hit / cjk if cjk else 0.0


def detect_encoding(raw: bytes) -> tuple[str, str]:
    """返回 (编码, 说明)。顺序：BOM → 严格 UTF-8 → GB18030 → 兜底。"""
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig", "BOM"
    if raw.startswith(b"\xff\xfe"):
        return "utf-16-le", "BOM"
    if raw.startswith(b"\xfe\xff"):
        return "utf-16-be", "BOM"
    try:
        raw[: min(len(raw), 1 << 20)].decode("utf-8")
        return "utf-8", "严格 UTF-8"
    except UnicodeDecodeError:
        pass
    # GB18030 几乎能解任何字节流 → 必须用常用字覆盖率校验
    sample = raw[: min(len(raw), 1 << 20)]
    try:
        s = sample.decode("gb18030", errors="replace")
    except Exception:
        s = ""
    if _score_common(s) >= TH.common_min or not s:
        return "gb18030", "GB18030 + 常用字校验"
    best = ("gb18030", _score_common(s))
    for enc in ("big5", "utf-16-le", "latin-1"):
        try:
            s2 = sample.decode(enc, errors="replace")
        except Exception:
            continue
        sc = _score_common(s2)
        if sc > best[1]:
            best = (enc, sc)
    return best[0], f"兜底（常用字 {best[1]:.1%}）"


def read_text(path: Path) -> tuple[str, str, int]:
    """返回 (文本, 编码, 替换字符数)。"""
    raw = path.read_bytes()
    enc, _note = detect_encoding(raw)
    text = raw.decode(enc, errors="replace")
    return text, enc, text.count("\ufffd")


# ---------------------------------------------------------------- 繁简

_CONV: dict[str, object] = {}


def t2s_available() -> bool:
    """opencc 是否可用（可选依赖，Apache-2.0）。"""
    return t2s_converter() is not None


def t2s_converter():
    """返回 text→text 的繁转简函数；没装 opencc 返回 None（只算一次）。"""
    if "c" in _CONV:
        return _CONV["c"]
    conv = None
    try:
        from opencc import OpenCC

        conv = OpenCC("t2s").convert
    except Exception:  # noqa: BLE001 —— 没装/装坏了都当"不可用"
        conv = None
    _CONV["c"] = conv
    return conv


# ---------------------------------------------------------------- 归一化结果


@dataclass
class NormDoc:
    """归一化后的文档 + 回溯信息。"""

    text: str = ""
    # 每行：(原文起始偏移, 原文长度, 归一化起始位置, 归一化长度)
    lines: list[tuple[int, int, int, int]] = field(default_factory=list)
    # 章节：(原文偏移, 标题)
    chapters: list[tuple[int, str]] = field(default_factory=list)
    raw_chars: int = 0
    cjk_ratio: float = 0.0
    common_ratio: float = 0.0
    enc: str = ""
    decode_bad: int = 0
    ad_lines: int = 0
    # 被删掉的广告行：(在"删广告之后"的文本里的起点, 长度)。
    # 没有它，`orig_offset()` 只能回指到**删广告后**的坐标 —— 每删一行，
    # 后面所有位置就少 N 个字符，误差按"该点之前被删广告的总长度"累加
    # （百万字的书按常见广告密度能漂上万字，高亮会落到别的段落）。
    # 见 set_ad_removed() / _to_orig()。
    ad_removed: list[tuple[int, int]] = field(default_factory=list, repr=False)

    _norm_starts: array = field(default_factory=lambda: array("q"), repr=False)
    _ad_starts: list[int] = field(default_factory=list, repr=False)
    _ad_cum: list[int] = field(default_factory=list, repr=False)

    def set_ad_removed(self, removed: list[tuple[int, int]]) -> "NormDoc":
        """登记被删广告行的位置，供 orig_offset() 把坐标换算回原始文本。"""
        self.ad_removed = list(removed)
        self._ad_starts = [s for s, _len in self.ad_removed]
        self._ad_cum = list(accumulate(length for _s, length in self.ad_removed))
        return self

    def finish(self) -> "NormDoc":
        self._norm_starts = array("q", (ln[2] for ln in self.lines))
        return self

    def _to_orig(self, pos: int) -> int:
        """「删广告后」的偏移 → **原始**（解码后、未删广告）的偏移。"""
        if not self._ad_starts:
            return pos
        i = bisect_right(self._ad_starts, pos)      # 起点 <= pos 的删除块数量
        return pos + (self._ad_cum[i - 1] if i else 0)

    # -- 位置回溯
    def orig_offset(self, norm_pos: int) -> int:
        """归一化位置 → 原文偏移（按行对齐，误差 < 一行；已补偿被删掉的广告行）。"""
        if not self.lines:
            return 0
        i = bisect_right(self._norm_starts, norm_pos) - 1
        i = max(0, min(i, len(self.lines) - 1))
        o_start, _o_len, n_start, _n_len = self.lines[i]
        return self._to_orig(o_start + max(0, norm_pos - n_start))

    def chapter_of(self, orig_offset: int) -> tuple[int, str]:
        """原文偏移 → (第几章序号, 标题)。"""
        if not self.chapters:
            return 0, ""
        lo, hi = 0, len(self.chapters) - 1
        res = 0
        while lo <= hi:
            mid = (lo + hi) // 2
            if self.chapters[mid][0] <= orig_offset:
                res = mid
                lo = mid + 1
            else:
                hi = mid - 1
        return res + 1, self.chapters[res][1]

    def line_at(self, norm_pos: int) -> int:
        if not self.lines:
            return 0
        return max(0, bisect_right(self._norm_starts, norm_pos) - 1)


def normalize(text: str, *, drop_ads: bool = True, src_enc: str = "", bad: int = 0,
              t2s: bool = False) -> NormDoc:
    """解码后的文本 → 归一化文档。

    流程：剔除广告行 → 逐行 translate（纯逐字符映射，逐行与整篇等价）
    → 顺手记录行映射，用于把归一化位置回溯到原文。

    ⚠ 广告行**不能**简单 `sub("")` 掉了事：那样后面所有偏移都会少掉被删的长度，
      误差一路累加，最后"第 3 章第 2 段"会指到别的地方。
      所以这里逐条处理并记下「删掉的位置 + 长度」，由 `orig_offset()` 换算回原文。
    """
    if t2s:
        conv = t2s_converter()
        if conv is not None:
            text = conv(text)          # 繁→简要在切行和章节正则之前做
    doc = NormDoc(raw_chars=len(text), enc=src_enc, decode_bad=bad)
    ad = 0
    removed: list[tuple[int, int]] = []
    if drop_ads:
        # finditer 仍是 C 层扫描，只有真正命中的行才走 Python
        pieces: list[str] = []
        last = 0
        filtered = 0
        for m in AD_LINE_RE.finditer(text):
            s = m.group(0)
            if len(s.strip()) >= AD_MAXLEN:
                continue
            seg = text[last:m.start()]
            pieces.append(seg)
            filtered += len(seg)
            removed.append((filtered, len(s)))
            ad += 1
            last = m.end()
        if removed:
            pieces.append(text[last:])
            text = "".join(pieces)

    doc.set_ad_removed(removed)
    parts: list[str] = []
    lines = doc.lines
    norm_len = 0
    pos = 0
    for raw_line in text.splitlines(keepends=True):
        o_start = pos
        pos += len(raw_line)
        n = raw_line.translate(TABLE)
        if not n:
            continue
        lines.append((o_start, len(raw_line), norm_len, len(n)))
        parts.append(n)
        norm_len += len(n)

    doc.text = "".join(parts)
    doc.ad_lines = ad

    # 两个自检指标口径不同，别混：
    #   cjk_ratio    —— 在**原文**上算，判断"这文件到底是不是中文书"（英文/二进制会被拦）
    #   common_ratio —— 在**归一化后**的汉字上算，判断"解码出来的像不像中文"（编码错判的唯一可靠信号）
    raw_counts = Counter(text)
    raw_cjk = 0
    for ch, n in raw_counts.items():
        if 0x4E00 <= ord(ch) <= 0x9FFF:
            raw_cjk += n
    doc.cjk_ratio = raw_cjk / len(text) if text else 0.0

    counts = Counter(doc.text)
    cjk = 0
    common = 0
    for ch, n in counts.items():
        o = ord(ch)
        if 0x4E00 <= o <= 0x9FFF:
            cjk += n
            if ch in COMMON_SET:
                common += n
    doc.common_ratio = common / cjk if cjk else 0.0
    # 章节位置要和 orig_offset() 同一套坐标（原始文本），否则界面按偏移取原文会错位
    doc.chapters = [(doc._to_orig(m.start()), m.group(1).strip())
                    for m in CHAP_RE.finditer(text)]
    return doc.finish()


def selfcheck(doc: NormDoc) -> str:
    """返回告警字符串（空 = 正常）。

    常用字覆盖率 < 40% 说明"解出来的不像中文" —— 最可能的失败模式是编码错判，
    后果是整本静默失效（指纹全废、判不出重复，但程序不报错）。
    """
    if not doc.text:
        return "空文本"
    if doc.common_ratio < TH.common_min:
        return (
            f"疑似解码失败：常用字覆盖率仅 {doc.common_ratio:.1%}"
            f"（正常 >60%），编码={doc.enc}，替换字符 {doc.decode_bad}"
        )
    if doc.cjk_ratio < TH.cjk_min:
        return f"汉字占比过低 {doc.cjk_ratio:.1%}（编码={doc.enc}）"
    if doc.decode_bad > len(doc.text) * 0.02:
        return f"替换字符过多 {doc.decode_bad}"
    return ""
