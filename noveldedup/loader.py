"""从导出的 JSON 复原扫描结果 —— 不重新扫描就能重新分组、重判、浏览、删除。

为什么需要它
------------
真实书库扫一遍 40 分钟。改了判据、或者只想换个角度看结果，不应该再等 40 分钟。
导出的 JSON 里已经带了**每对书的完整指标**（jac / 包含度 / 覆盖率 / 链长 / 共享数），
足够离线重跑 `judge.classify()` 和 `group.build()`。

数据分别在哪儿（用户最常问的问题）
----------------------------------
| 数据 | 存放在哪 | 能随导出文件走吗 |
| --- | --- | --- |
| 书籍元信息（字数/章节/编码/大小） | JSON 的 `books_all` / `members` | ✅ |
| 关系指标（jac/包含度/覆盖率/链长） | JSON 的 `pairs` + `candidates` | ✅ |
| **指纹数组**（每本一个 uint32 排序表） | 本地缓存 `%LOCALAPPDATA%\\NovelDedup\\cache\\<哈希>\\fp\\*.bin` | ❌ 没写进导出 |
| 原文 | 用户自己的书库 | ✅（文件还在就能读，用于证据并排） |

指纹**是存了的**，只是存在本机缓存目录里（体积约原文字数的 1.7%，
19.4 亿字对应约 150 MB），不塞进导出文件——否则每次导出都要多几百 MB。
换机器要复用缓存，把那个 cache 目录一起拷过去即可（路径要一致）。

导入能做什么 / 不能做什么
-------------------------
能：重新分组（用当前规则）、按当前判据重判（前提：导出时带了 `candidates`）、
    浏览、筛选、重新导出、删除到回收站。
不能：凭空补出"从没判过的候选对"——那需要指纹，只能重扫。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import core, group as group_mod, judge, segments
from .cache import Book
from .engine import ScanResult


@dataclass
class Loaded:
    result: ScanResult | None = None
    warnings: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    missing: int = 0        # 导出之后已经不存在的文件数

    @property
    def ok(self) -> bool:
        return self.result is not None


def _book_from(d: dict, bid: int) -> Book:
    return Book(
        id=bid, path=d.get("path", ""),
        size=int(d.get("size") or 0), mtime=0.0,
        enc=d.get("enc") or "", fmt=d.get("fmt") or "txt",
        raw_chars=0,
        norm_chars=int(d.get("cjk_chars") or d.get("norm_chars") or 0),
        word_chars=int(d.get("chars") or d.get("word_chars") or 0),
        cjk=0.0, common=0.0,
        chapters=int(d.get("chapters") or 0),
        ad_lines=int(d.get("ad_lines") or 0),
        fps=0,
        # 归一化全文 hash / 文件 hash：导入后「一键清理内容完全相同」要用
        # （老导出文件没有这两个字段，此时一键清理会退化为"没有可清理的"，
        #   由 load_result 给出明确提示，而不是静默）
        l1=d.get("l1") or "", file_hash=d.get("file_hash") or "",
        status="ok" if not d.get("note") else "suspect",
        note=d.get("note") or "")


def _seg_from(e: dict) -> segments.Segment:
    a = int(e.get("a_offset") or 0)
    b = int(e.get("b_offset") or 0)
    s = segments.Segment(pos_a=a, pos_b=b, length=int(e.get("chars") or 0))
    s.chapter_a = int(e.get("a_chapter") or 0)
    s.chapter_b = int(e.get("b_chapter") or 0)
    s.chapter_a_end = s.chapter_a
    s.chapter_b_end = s.chapter_b
    s.title_a = e.get("a_title") or ""
    s.title_b = e.get("b_title") or ""
    s.orig_a, s.orig_b = a, b          # 界面按这个偏移去读原文
    return s


def _pair_from(d: dict, id_of: dict[str, int]) -> judge.PairStat | None:
    a = id_of.get(d.get("a") or "")
    b = id_of.get(d.get("b") or "")
    if not a or not b or a == b:
        return None
    return judge.PairStat(
        a=a, b=b,
        shared=int(d.get("shared") or d.get("shared_fingerprints") or 0),
        raw_shared=int(d.get("raw_shared") or d.get("shared_fingerprints") or 0),
        jac=float(d.get("jaccard") or d.get("jac") or 0),
        cont_ab=min(1.0, float(d.get("containment_ab") or 0)),
        cont_ba=min(1.0, float(d.get("containment_ba") or 0)),
        chain=int(d.get("chain") or 0),
        cov_a=float(d.get("coverage_a") or 0),
        cov_b=float(d.get("coverage_b") or 0),
        rel=d.get("relation") or "")


def load_result(path: Path, reclassify: bool = True, log=None,
                skip_missing: bool = True) -> Loaded:
    """读导出文件 → ScanResult。

    skip_missing=True（默认）：**把磁盘上已经不存在的文件剔掉**。

    为什么必须这么做（漫画查重项目里踩过的同一个坑）：
    导出文件记录的是"扫描那一刻的书库"。用户删完重复文件之后再导入，
    那些已删的书仍然在 JSON 里 —— 不剔除就会看到一堆"已经不存在的书",
    更糟的是它们还能参与分组、被再次勾选删除（虽然删除时会失败）。
    视频/漫画查重的做法都是从缓存里移除已删记录，这里同理：以磁盘为准。
    """
    out = Loaded()
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        out.warnings.append(f"读不了这个文件：{exc}")
        return out

    if data.get("tool") != "NovelDedup":
        out.warnings.append("这不是本工具导出的结果文件")
        return out

    meta = {k: data.get(k) for k in
            ("judge_ver", "feat_ver", "norm_ver", "t2s", "preset", "window", "root",
             "candidates_complete", "candidate_count", "format_version")}
    out.meta = meta

    # ---- 书
    books: dict[int, Book] = {}
    id_of: dict[str, int] = {}
    book_rows = data.get("books_all") or []
    if not book_rows:                      # 旧导出没有 books_all → 用组成员兜底
        for g in data.get("groups", []):
            book_rows.extend(g.get("members", []))
        if book_rows:
            out.warnings.append("这份文件没有完整的书籍清单，只能还原重复组里的书")
    # ---- 先按"文件还在不在"过滤
    if skip_missing:
        present, gone = [], []
        for d in book_rows:
            p = d.get("path") or ""
            if not p:
                continue
            (present if Path(p).exists() else gone).append(d)
        if present:
            out.missing = len(gone)
            book_rows = present
            if gone:
                out.warnings.append(
                    f"{len(gone):,} 个文件已不在磁盘上（可能你已删除或移动），"
                    f"已从结果中剔除。")
        elif gone:
            # 全军覆没：多半是在另一台机器/另一个盘打开的结果。
            # 这时不能"什么都不显示"，退化成只读查看，并说清楚。
            out.warnings.append(
                f"这份结果里的 {len(gone):,} 个文件都不在本机，"
                f"只能查看指标，不能删除。")
            book_rows = gone

    for d in book_rows:
        p = d.get("path") or ""
        if not p or p in id_of:
            continue
        bid = len(id_of) + 1
        id_of[p] = bid
        books[bid] = _book_from(d, bid)

    # 老导出文件（v1.0.4 之前）没有 l1：一键清理会静默失效，必须说清楚
    if books and not any(b.l1 or b.file_hash for b in books.values()):
        out.warnings.append(
            "这份导出没带「归一化全文 hash」（旧版本导出的），"
            "导入后无法使用「⚡ 一键清理内容完全相同」；其他功能不受影响。")

    # ---- 候选 / 重复对
    raw_candidates = data.get("candidates") or []
    raw_pairs = [p for g in data.get("groups", []) for p in g.get("pairs", [])]

    source = "重复对"
    rows = raw_pairs
    if raw_candidates:
        rows = raw_candidates
        source = "全部候选对"
    if not rows:
        out.warnings.append("文件里没有任何书对数据")
        return out

    stats: list[judge.PairStat] = []
    dropped = 0
    for d in rows:
        st = _pair_from(d, id_of)
        if st is None:
            continue
        if reclassify:
            st.rel = judge.classify(st, 0, 0, int(meta.get("window") or core.FINE_W))
        if st.rel == judge.NO_REL:
            dropped += 1
            continue
        stats.append(st)

    # ---- 分组（用当前规则重算，而不是照抄文件里的组）
    groups = group_mod.build(stats, books)

    # ---- 证据段（从文件里的 evidence 还原；原文按需读）
    segs: dict[tuple[int, int], list[segments.Segment]] = {}
    for g in data.get("groups", []):
        for p in g.get("pairs", []):
            st = _pair_from(p, id_of)
            if st is None:
                continue
            ev = p.get("evidence") or []
            if not ev:
                continue
            key = (st.a, st.b)
            if key not in segs:
                segs[key] = [_seg_from(e) for e in ev]

    res = ScanResult(
        root=Path(meta.get("root") or "."),
        preset=meta.get("preset") or "",
        w=int(meta.get("window") or core.FINE_W),
        books=books,
        stats=stats,
        groups=groups,
        segs=segs,
    )
    # 繁简是否参与归一化：按需重算证据时必须沿用，否则偏移会错
    res.t2s = bool(meta.get("t2s")) or meta.get("norm_ver") == "n3s"
    res.candidates = [s for s in stats] if raw_candidates else []
    res.candidates_complete = bool(meta.get("candidates_complete"))

    out.result = res

    # ---- 版本提示（说清楚"这份数据能不能直接信"）
    if meta.get("judge_ver") and meta["judge_ver"] != core.JUDGE_VER:
        out.warnings.append(
            f"这份数据是用旧判据 {meta['judge_ver']} 生成的（当前 {core.JUDGE_VER}）。"
            f"已按当前判据重新分组，但**只有旧判据留下的边**——"
            f"新判据不会补出新边，要完整结果请重扫。")
    if not raw_candidates:
        out.warnings.append(
            f"文件里没有候选对（只有 {len(raw_pairs):,} 条判为重复的边）。"
            f"重新分组只能删边、不能补边。想离线改判据，导出时勾选「包含全部候选对」。")
    if log:
        for w in out.warnings:
            log.warning("%s", w)
    if log:
        log.info("导入完成：%d 本 / %d 组 / %d 对（来源：%s，判据 %s%s）",
                 len(books), len(groups), len(stats), source,
                 meta.get("judge_ver") or "未知",
                 f"，剔除已删 {out.missing:,}" if out.missing else "")
    return out
