"""报告导出：CSV（Excel 友好）/ JSON / 控制台摘要。"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from . import core

CSV_HEAD = [
    "组号", "结构", "建议保留", "关系", "文件A", "文件B",
    "Jaccard", "A含于B", "B含于A", "字符覆盖A", "字符覆盖B",
    "共享指纹", "链长", "汉字A", "字符A(含标点)", "汉字B", "字符B(含标点)",
    "章节A", "章节B", "证据段数", "证据示例",
]


def _rows(result):
    for gi, g in enumerate(result.groups, 1):
        keep = result.books.get(g.keep_id)
        for st in sorted(g.pairs, key=lambda s: -s.jac):
            a = result.books.get(st.a)
            b = result.books.get(st.b)
            if not a or not b:
                continue
            segs = result.segs.get((st.a, st.b), [])
            sample = segs[0].readable if segs else ""
            yield gi, g, keep, st, a, b, segs, sample


def write_csv(result, path: Path) -> int:
    """UTF-8-BOM，Excel 直接双击不乱码。返回写入行数。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(CSV_HEAD)
        for gi, g, keep, st, a, b, segs, sample in _rows(result):
            w.writerow([
                gi, g.structure, (keep.name if keep else ""), st.rel,
                a.path, b.path,
                f"{st.jac:.4f}", f"{st.cont_ab:.4f}", f"{st.cont_ba:.4f}",
                f"{st.cov_a:.4f}", f"{st.cov_b:.4f}",
                st.shared, st.chain, a.norm_chars, a.word_chars, b.norm_chars, b.word_chars,
                a.chapters, b.chapters, len(segs), sample,
            ])
            n += 1
    return n


def to_dict(result, with_candidates: bool = False) -> dict:
    groups = []
    for gi, g in enumerate(result.groups, 1):
        keep = result.books.get(g.keep_id)
        members = [result.books[m] for m in g.members if m in result.books]
        pairs = []
        for st in g.pairs:
            a = result.books.get(st.a)
            b = result.books.get(st.b)
            if not a or not b:
                continue
            segs = result.segs.get((st.a, st.b), [])
            pairs.append({
                "a": a.path, "b": b.path, "relation": st.rel,
                # 不四舍五入：4 位小数会让 0.54996→0.55 跨过阈值，
                # 导入后重判的结果与扫描时不一致（往返测试抓到的）
                "jaccard": st.jac,
                "containment_ab": st.cont_ab,
                "containment_ba": st.cont_ba,
                "coverage_a": st.cov_a,
                "coverage_b": st.cov_b,
                "shared_fingerprints": st.shared,
                "chain": st.chain,
                "cjk_a": a.norm_chars, "chars_a": a.word_chars,
                "cjk_b": b.norm_chars, "chars_b": b.word_chars,
                "evidence": [
                    {"span": s.readable,
                     "a_chapter": s.chapter_a, "a_title": s.title_a,
                     "b_chapter": s.chapter_b, "b_title": s.title_b,
                     "chars": s.length, "a_offset": s.orig_a, "b_offset": s.orig_b}
                    for s in segs
                ],
            })
        groups.append({
            "group": gi, "structure": g.structure,
            "keep": keep.path if keep else "",
            "keep_reason": g.keep_reason,
            "members": [{"path": m.path, "chapters": m.chapters,
                         "cjk_chars": m.norm_chars, "chars": m.word_chars, "enc": m.enc,
                         "size": m.size, "fmt": getattr(m, "fmt", "txt"),
                         "ad_lines": m.ad_lines} for m in members],
            "pairs": pairs,
            "skipped_merges": [{"a": result.books.get(x).path if result.books.get(x) else x,
                                "b": result.books.get(y).path if result.books.get(y) else y,
                                "relation": z} for x, y, z in g.skipped],
        })
    out = {
        "tool": "NovelDedup",
        "format_version": 2,
        # 三个版本号：导入时用来判断"这份数据还能不能直接复用"
        "judge_ver": core.JUDGE_VER,
        "feat_ver": core.feat_ver(result.w) if result.w else "",
        # 归一化版本必须跟着繁简开关走（n3 / n3s），写死 NORM_VER 会让元数据说谎
        "norm_ver": core.norm_ver(bool(getattr(result, "t2s", False))),
        "t2s": bool(getattr(result, "t2s", False)),
        "root": str(result.root),
        "preset": result.preset,
        "window": result.w,
        "guarantee_chars": result.w + 12 - 1,
        "elapsed_sec": round(result.elapsed, 2),
        "peak_memory_mb": round(result.peak_mb, 1),
        "books": len(result.books),
        "total_cjk_chars": result.total_chars,
        "total_chars": result.total_word_chars,
        "total_raw_chars": result.total_raw_chars,
        "duplicate_pairs": len(result.dup_pairs),
        # 全部书（不只是重复组里的）。导入时要靠它重建完整清单：
        # 被拒候选涉及的书往往不在任何组里，没有这份清单就还原不出来。
        "books_all": [
            {"path": b.path, "chapters": b.chapters,
             "cjk_chars": b.norm_chars, "chars": b.word_chars,
             "size": b.size, "enc": b.enc, "fmt": getattr(b, "fmt", "txt"),
             "ad_lines": b.ad_lines,
             # l1 = 归一化全文 hash。必须带上：导入后「⚡ 一键清理内容完全相同」
             # 完全靠它找"一字不差"的副本，缺了就只能静默地什么都不做。
             "l1": b.l1, "file_hash": b.file_hash,
             "note": b.note if not b.ok else ""}
            for b in result.books.values()
        ],
        "groups": groups,
        "suspects": [{"path": b.path, "note": b.note} for b in result.suspects],
        "candidates_complete": bool(getattr(result, "candidates_complete", False)),
        "candidate_count": len(getattr(result, "candidates", ())),
    }
    if with_candidates and getattr(result, "candidates", None):
        # 只有调判据时才需要：把**判过的所有候选对**（含被拒的）也写进去，
        # 这样导入后改阈值重判 = 等价于重扫，不用再等几十分钟。
        out["candidates"] = [
            {"a": result.books[s.a].path if s.a in result.books else "",
             "b": result.books[s.b].path if s.b in result.books else "",
             "shared": s.shared, "raw_shared": s.raw_shared,
             "jaccard": s.jac,
             "containment_ab": s.cont_ab,
             "containment_ba": s.cont_ba,
             "chain": s.chain,
             "coverage_a": s.cov_a, "coverage_b": s.cov_b}
            for s in result.candidates
            if s.a in result.books and s.b in result.books
        ]
    return out


def write_json(result, path: Path, with_candidates: bool = False) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(to_dict(result, with_candidates), ensure_ascii=False, indent=2),
                    encoding="utf-8")


def summary_text(result, top: int = 20) -> str:
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append(f"扫描完成：{result.root}")
    lines.append(f"档位 {result.preset}（w={result.w}）｜保证：连续 {result.w + 11} 字相同必检出")
    lines.append(
        f"书籍 {len(result.books)} 本"
        f"｜汉字 {result.total_chars/1e4:,.0f} 万"
        f"（含标点 {result.total_word_chars/1e4:,.0f} 万，起点口径）"
        f"｜耗时 {result.elapsed:.1f}s"
        + (f"｜峰值内存 {result.peak_mb:,.0f} MB" if result.peak_mb else ""))
    lines.append(f"重复组 {len(result.groups)} 个｜重复书对 {len(result.dup_pairs)} 对")
    lines.append("=" * 78)

    for gi, g in enumerate(result.groups[:top], 1):
        keep = result.books.get(g.keep_id)
        lines.append("")
        lines.append(f"【组 {gi}】{g.structure}　{len(g.members)} 本")
        lines.append(f"  建议保留：{keep.name if keep else '?'}")
        if g.keep_reason:
            lines.append(f"  理由：{g.keep_reason}")
        for st in sorted(g.pairs, key=lambda s: -s.jac)[:6]:
            a = result.books.get(st.a)
            b = result.books.get(st.b)
            if not a or not b:
                continue
            lines.append(f"    - [{st.rel}] {a.name}  ↔  {b.name}")
            lines.append(
                f"      Jaccard {st.jac:.3f}｜覆盖 {st.cov_a:.2f}/{st.cov_b:.2f}"
                f"｜共享 {st.shared:,}｜链长 {st.chain:,}"
            )
            segs = result.segs.get((st.a, st.b), [])
            if segs:
                lines.append(f"      证据：{segs[0].readable}")
                # head_a/head_b 只在调用方显式传了原文时才填（扫描期为省内存不填），
                # 空的时候不要打印空行
                if segs[0].head_a:
                    lines.append(f"        A：{segs[0].head_a[:50]}")
                if segs[0].head_b:
                    lines.append(f"        B：{segs[0].head_b[:50]}")
    if len(result.groups) > top:
        lines.append(f"\n…… 还有 {len(result.groups) - top} 组，见 CSV/JSON")

    sus = result.suspects
    if sus:
        lines.append("")
        lines.append(f"⚠ {len(sus)} 本需要人工确认（编码可疑/解析失败）：")
        for b in sus[:10]:
            lines.append(f"    {b.name}：{b.note}")
    return "\n".join(lines)
