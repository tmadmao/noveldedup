"""扫描引擎：扫描 → 指纹 → 候选 → 精算 → 分组。

两遍结构（与开发计划 §3.3 一致）
--------------------------------
Pass 1（全部书）：解码 → 归一化 → 粗指纹（只存 32 位哈希，不存位置）→ 落缓存
候选生成：按 hash 高 8 位分 256 桶归并，内存与书库规模无关
Pass 2（仅候选对涉及的书）：重读 → 细指纹（w=40，带位置）→ 精确指标 + 证据段

为什么 Pass 2 要重读：位置只在精算和证据里用得上，全库存位置会让索引翻倍。
候选对通常只有几十~几百对，重读的成本远小于全程存位置。

多进程
------
纯 Python 的 CPU 密集循环（滚动哈希）用线程没有加速（GIL），
所以用 ProcessPoolExecutor。worker 只做计算、不碰数据库，主进程负责落盘。
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from array import array
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from . import core, formats, group as group_mod, index, judge, segments
from .cache import Book, Cache
from .fingerprint import fingerprints, fingerprints_no_pos
from .normalize import NormDoc, normalize, selfcheck

# 支持的全部扩展名（计划 M3：txt / epub / html / docx / pdf）
TEXT_EXT = set(formats.supported_exts())
_WS = re.compile(r"\s")


# ------------------------------------------------------------------ 结果


@dataclass
class ScanResult:
    root: Path = Path(".")
    preset: str = ""
    w: int = 0
    books: dict[int, Book] = field(default_factory=dict)
    stats: list[judge.PairStat] = field(default_factory=list)
    groups: list[group_mod.Group] = field(default_factory=list)
    segs: dict[tuple[int, int], list[segments.Segment]] = field(default_factory=dict)
    # 本轮**判过的全部候选对**（含被拒的）。导出后可以离线改判据重判，不用重扫。
    candidates: list[judge.PairStat] = field(default_factory=list)
    candidates_complete: bool = True
    # 本轮实际生效的预处理参数：按需重算证据时必须沿用，否则位置对不上
    t2s: bool = False
    drop_ads: bool = True
    elapsed: float = 0.0
    peak_mb: float = 0.0
    log_lines: list[str] = field(default_factory=list)

    @property
    def dup_pairs(self) -> list[judge.PairStat]:
        return [s for s in self.stats if s.is_dup]

    @property
    def total_chars(self) -> int:
        """归一化字数（汉字+数字+字母）——算法口径。"""
        return sum(b.norm_chars for b in self.books.values())

    @property
    def total_word_chars(self) -> int:
        """含标点的字符数（起点/阅文口径），用于和平台字数对照。"""
        return sum(b.word_chars for b in self.books.values())

    @property
    def total_raw_chars(self) -> int:
        return sum(b.raw_chars for b in self.books.values())

    @property
    def suspects(self) -> list[Book]:
        return [b for b in self.books.values() if not b.ok]

    def book(self, bid: int) -> Book:
        return self.books[bid]


# ------------------------------------------------------------------ worker


def _hash_bytes(raw: bytes) -> tuple[str, str]:
    full = hashlib.blake2b(raw, digest_size=16).hexdigest()
    n = 64 * 1024
    ht = hashlib.blake2b(raw[:n] + raw[-n:], digest_size=16).hexdigest()
    return full, ht


def _work_scan(args: tuple) -> dict:
    """Pass 1：单本 → 元信息 + 粗指纹（不含位置）。"""
    path, k, w, drop_ads, t2s = args
    p = Path(path)
    out: dict = {"path": path}
    try:
        st = p.stat()
        raw = p.read_bytes()
        full_hash, ht_hash = _hash_bytes(raw)
        item = formats.extract(p, raw)      # ← 格式层：txt/epub/html/docx/pdf 统一入口
        del raw
        text = item.text
        enc, bad = item.enc, item.bad
        if not text.strip():
            raise ValueError(item.note or "提取不到文本（可能是图片版）")
        doc = normalize(text, drop_ads=drop_ads, src_enc=enc, bad=bad, t2s=t2s)
        warn = selfcheck(doc)
        # 短文本标注（计划 §7.1）：字数太少时判据不稳，必须让用户知道
        if len(doc.text) < core.SHORT_CHARS:
            short = f"短文本：仅 {len(doc.text):,} 字，判定可能不稳"
            warn = f"{warn}；{short}" if warn else short
        # 起点/阅文的"总字数"= 非空白字符数（汉字+标点+数字），与判重用的归一化字数不是一回事
        word_chars = len(text) - len(_WS.findall(text))
        del text
        if not doc.text:
            raise ValueError("归一化后为空（可能不是中文文本）")
        hashes = fingerprints_no_pos(doc.text, k, w)
        l1 = hashlib.blake2b(doc.text.encode("utf-8", "surrogatepass"), digest_size=16).hexdigest()
        out.update(
            size=st.st_size, mtime=st.st_mtime, enc=enc, fmt=item.fmt,
            raw_chars=doc.raw_chars, norm_chars=len(doc.text), word_chars=word_chars,
            cjk=doc.cjk_ratio, common=doc.common_ratio,
            chapters=len(doc.chapters), ad_lines=doc.ad_lines,
            fps=len(hashes), l1=l1, file_hash=full_hash, head_tail=ht_hash,
            status="suspect" if warn else "ok", note=warn,
            hashes=hashes.tobytes(),
        )
    except formats.UnsupportedFormat as e:
        out.update(status="skip", note=str(e), size=0, mtime=0,
                   enc="", fmt=p.suffix.lower().lstrip("."), raw_chars=0, norm_chars=0,
                   cjk=0.0, common=0.0, chapters=0, ad_lines=0, fps=0, l1="",
                   file_hash="", head_tail="", hashes=b"")
    except Exception as e:  # noqa: BLE001 —— 单本失败不能拖垮整次扫描
        out.update(status="error", note=f"{type(e).__name__}: {e}", size=0, mtime=0,
                   enc="", fmt=p.suffix.lower().lstrip("."), raw_chars=0, norm_chars=0,
                   cjk=0.0, common=0.0, chapters=0, ad_lines=0, fps=0, l1="",
                   file_hash="", head_tail="", hashes=b"")
    return out


def _work_fine(args: tuple) -> dict:
    """Pass 2：单本 → 细指纹（带位置）+ 行映射（供证据回溯）。

    注意：**不返回原文**。原文有十几 MB，几百本书一起回传会把内存打爆。
    证据渲染时按需重读那几个文件（只发生在真有重复的组上）。
    """
    path, k, w, drop_ads, t2s = args
    p = Path(path)
    raw = p.read_bytes()
    item = formats.extract(p, raw)          # 与 Pass1 完全同一套格式逻辑
    del raw
    doc = normalize(item.text, drop_ads=drop_ads, src_enc=item.enc, bad=item.bad, t2s=t2s)
    pos, hs = fingerprints(doc.text, k, w)
    lines = array("q")
    for ln in doc.lines:
        lines.extend(ln)
    return {
        "path": path,
        "pos": pos.tobytes(),
        "hash": hs.tobytes(),
        "norm_chars": len(doc.text),
        "lines": lines.tobytes(),
        "chapters": doc.chapters,
        # 被删广告行的位置：证据回溯要用它把坐标换算回原文（否则越长漂得越多）
        "ad_removed": doc.ad_removed,
    }


# ------------------------------------------------------------------ 辅助


def collect_files(root: Path, min_bytes: int = 1024) -> list[Path]:
    out: list[Path] = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in TEXT_EXT:
            try:
                if p.stat().st_size >= min_bytes:
                    out.append(p)
            except OSError:
                continue
    return out


def _light_doc(d: dict) -> NormDoc:
    """用 Pass2 结果重建一个只能做位置回溯的轻量 NormDoc。"""
    doc = NormDoc(text="", chapters=d["chapters"], raw_chars=0, enc="")
    flat = array("q")
    flat.frombytes(d["lines"])
    doc.lines = [tuple(flat[i : i + 4]) for i in range(0, len(flat), 4)]  # type: ignore
    # 必须还原"删了哪些广告行"，否则 orig_offset() 会把位置算回删广告后的坐标
    doc.set_ad_removed(d.get("ad_removed") or [])
    return doc.finish()


# ------------------------------------------------------------------ 主流程


def scan(
    root: Path,
    preset: str | None = None,
    workers: int | None = None,
    drop_ads: bool = True,
    min_bytes: int = 1024,
    log=None,
    budget_mb: int = core.INDEX_BUDGET_MB,
    with_evidence: bool = True,
    progress=None,
    full: bool = False,
    t2s: bool = False,
) -> ScanResult:
    """progress(阶段, 已完成, 总数) —— 供界面显示进度；异常不影响扫描。"""

    def _p(phase: str, done: int = 0, total: int = 0, msg: str = "") -> None:
        if progress:
            try:
                progress(phase, done, total, msg)
            except TypeError:
                try:
                    progress(phase, done, total)
                except Exception:  # noqa: BLE001
                    pass
            except Exception:  # noqa: BLE001 —— 界面出错不能拖垮扫描
                pass

    t_start = time.perf_counter()
    log = log or core.setup_logging()
    root = root.resolve()
    workers = workers or min(8, os.cpu_count() or 2)

    ctx = ScanResult(root=root)
    _p("枚举文件", 0, 0, f"正在遍历：{root}")
    files = collect_files(root, min_bytes)
    _p("枚举文件", len(files), max(1, len(files)), f"找到 {len(files)} 个 txt")
    log.info("文件枚举完成：%d 个（目录 %s）", len(files), root)
    if not files:
        log.warning("没有找到 txt 文件：%s", root)
        return ctx

    cache_dir = core.cache_dir_for(root)
    log.info("指纹缓存：%s（%.0f MB）", cache_dir, core.dir_size_mb(cache_dir))
    cache = Cache(cache_dir)
    try:
        # ---- 选档
        if preset and preset in core.PRESETS:
            w = core.PRESETS[preset]
        else:
            known = sum(b.norm_chars for b in cache.all_books())
            preset, w = core.pick_preset(known, budget_mb)
        feat = core.feat_ver(w)
        if t2s and not normalize_t2s_ok():
            log.warning("繁简转换需要 opencc（pip install opencc-python-reimplemented）"
                        "，本次按「不转换」处理")
            t2s = False
        nv = core.norm_ver(t2s)
        ctx.preset, ctx.w = preset, w
        ctx.t2s, ctx.drop_ads = t2s, drop_ads
        log.info("档位=%s (w=%d，保证连续 %d 字相同必检出)  进程=%d  文件=%d",
                 preset, w, core.t_of(w), workers, len(files))

        # ---- Pass 1
        _p("检查缓存", 0, max(1, len(files)), "比对缓存记录")
        todo: list[str] = []
        reused = 0
        for p in files:
            st = p.stat()
            if cache.fresh(str(p), st.st_size, st.st_mtime, feat, nv):
                reused += 1
            else:
                todo.append(str(p))
        _p("检查缓存", len(files), max(1, len(files)), f"命中缓存 {reused} 本")
        log.info("缓存命中 %d 本，需计算 %d 本", reused, len(todo))

        if todo:
            t0 = time.perf_counter()
            _p("指纹计算", 0, len(todo), f"开始计算 {len(todo)} 本")
            tasks = [(p, core.K, w, drop_ads, t2s) for p in todo]
            done = 0
            if workers > 1 and len(tasks) > 1:
                with ProcessPoolExecutor(max_workers=workers) as ex:
                    futs = [ex.submit(_work_scan, t) for t in tasks]
                    for fut in as_completed(futs):
                        r = fut.result()
                        _store(cache, r, feat, nv)
                        done += 1
                        _p("指纹计算", done, len(tasks), Path(r["path"]).name)
                        if done % 20 == 0 or done == len(tasks):
                            log.info("  Pass1 %d/%d  %.1fs", done, len(tasks),
                                     time.perf_counter() - t0)
            else:
                for t in tasks:
                    r = _work_scan(t)
                    _store(cache, r, feat, nv)
                    done += 1
                    _p("指纹计算", done, len(tasks), Path(r["path"]).name)
            log.info("Pass1 完成，用时 %.1fs", time.perf_counter() - t0)
        _p("指纹计算", len(todo) or 1, len(todo) or 1,
           "指纹计算完成（全部命中缓存）" if not todo else "指纹计算完成")

        # ---- 收集
        # 只保留本次真正枚举到的文件。缓存里可能残留"已经删掉/移走"的书，
        # 不过滤的话删完文件再扫还会把它们当成重复（用户会以为删除没生效）。
        now_paths = {str(p) for p in files}
        cached = cache.all_books(feat, nv)
        books = {b.id: b for b in cached if b.path in now_paths}
        stale = len(cached) - len(books)
        if stale:
            log.info("缓存里 %d 本已不在磁盘上，忽略", stale)
        ctx.books = books
        if not books:
            return ctx
        total = sum(b.norm_chars for b in books.values())
        log.info("书库合计 %.2f 亿字，索引预计 %.0f MB",
                 total / 1e8, core.est_index_mb(total, w))
        if core.est_index_mb(total, w) > budget_mb and not preset:
            log.warning("索引超出预算 %d MB，可手动换更快的档", budget_mb)

        # ---- L1 快速通道（归一化全文 hash 完全相同 → 零误判判重）
        #
        # L1 只花一次 dict 分组，**每轮都重算、不进关系表**：
        # 关系表里只存"指纹算出来的边"，L1 边不存在过期问题。
        l1_stats: list[judge.PairStat] = []
        by_l1: dict[str, list[int]] = {}
        for b in books.values():
            if b.ok and b.l1:
                by_l1.setdefault(b.l1, []).append(b.id)
        l1_dup: set[tuple[int, int]] = set()
        for ids in by_l1.values():
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    x, y = ids[i], ids[j]
                    shared = min(books[x].fps, books[y].fps)
                    l1_st = judge.PairStat(
                        a=x, b=y, shared=shared, jac=1.0, cont_ab=1.0, cont_ba=1.0,
                        chain=max(1, shared), cov_a=1.0, cov_b=1.0, rel=judge.SAME_EXACT)
                    l1_stats.append(l1_st)
                    ctx.candidates.append(l1_st)
                    l1_dup.add((x, y))
        if l1_stats:
            log.info("L1 归一化全文 hash 直接判重：%d 对", len(l1_stats))

        # ---- 增量判定（计划 §3.3）
        #
        # 三档行为：
        #   ① 没有完整的关系表（首次 / 上次中断 / 换了档位）→ 全量
        #   ② 有关系表 + 有新增或变更 → 只对"含变化书"的书对做候选与精算
        #   ③ 有关系表 + 一个文件都没变 → 完全不碰指纹，秒开
        #
        # ⚠ 关系表分两步提交：开跑时标"未完成"，全部算完才标"完成"。
        #   中途被杀的话下次会退回全量——宁可多算，也不能静默漏掉重复。
        todo_set = set(todo)
        todo_ids = {b.id for b in books.values() if b.path in todo_set}
        if todo_ids:
            cache.drop_edges(todo_ids, feat, nv, core.JUDGE_VER)

        hist: list[judge.PairStat] = []
        have_hist = (not full) and bool(reused) and cache.edges_complete(feat, nv, core.JUDGE_VER)
        if full:
            log.info("强制全量扫描（--full）：忽略历史关系表")
        if have_hist:
            raw_hist = cache.load_edges(feat, nv, core.JUDGE_VER)
            ended = {x for e in raw_hist for x in (e.a, e.b)} - set(books)
            if ended:                       # 书被删掉了，它的边一并作废
                cache.drop_edges(ended, feat, nv, core.JUDGE_VER)
            hist = [e for e in raw_hist if e.a in books and e.b in books]
            ctx.candidates_complete = False     # 增量只判了新对，导出时要说清楚
            log.info("增量扫描：新/变更 %d 本，复用历史关系 %d 条", len(todo_ids), len(hist))
            _p("增量", 0, 1, f"新书 {len(todo_ids)} 本 / 历史关系 {len(hist)} 条")
        else:
            if reused:
                log.warning("上次关系表不完整（中断或换档）→ 本次全量重算")
            cache.mark_edges(feat, nv, False, core.JUDGE_VER)

        stats: list[judge.PairStat] = list(l1_stats)
        computed: list[judge.PairStat] = []     # 本轮算出的边，跑完才落盘

        # ---- 候选生成（只在"有变化"或"没有历史"时才需要）
        need_judge = bool(todo_ids) or not have_hist
        cands: list[tuple[int, int, int]] = []
        if need_judge:
            t0 = time.perf_counter()
            fp_arrays: dict[int, array] = {}
            for bid in books:
                arr = cache.read_fp(bid)
                if arr:
                    fp_arrays[bid] = arr
            focus = todo_ids if have_hist else None
            cands = index.candidates(fp_arrays, {b: len(a) for b, a in fp_arrays.items()},
                                     focus=focus)
            del fp_arrays
            cands = [(a, b, sh) for a, b, sh in cands
                     if (a, b) not in l1_dup and (b, a) not in l1_dup]
            log.info("候选对 %d 个（用时 %.1fs，%s）", len(cands),
                     time.perf_counter() - t0,
                     f"只看含变化书的 {len(todo_ids)} 本" if focus else "全量")
            _p("候选比对", len(cands), len(cands), f"{len(cands)} 对候选")
        else:
            log.info("全部命中缓存：跳过候选生成与精算，直接复用关系表")
            _p("候选比对", 1, 1, "复用历史关系表")

        # ---- Pass 2：按"连通分量"分批，逐批释放
        #
        # 为什么按分量而不是一次性全算：候选书可能有上万本，
        # 每本的细指纹数组是 几十万条 × 16 字节；全部同时留在内存里会吃掉几个 GB。
        # 一个连通分量 = 一个重复簇（同一部书的各种版本），通常几本到几十本，
        # 算完即可释放，内存上限由"最大的那个簇"决定，而不是整库。
        if cands:
            t0 = time.perf_counter()
            comp_of: dict[int, int] = {}

            def _find(x: int) -> int:
                comp_of.setdefault(x, x)
                while comp_of[x] != x:
                    comp_of[x] = comp_of[comp_of[x]]
                    x = comp_of[x]
                return x

            for a, b, _s in cands:
                ra, rb = _find(a), _find(b)
                if ra != rb:
                    comp_of[rb] = ra

            by_comp: dict[int, list[tuple[int, int, int]]] = {}
            for c in cands:
                by_comp.setdefault(_find(c[0]), []).append(c)
            log.info("候选对分成 %d 个重复簇，最大的 %d 对",
                     len(by_comp), max((len(v) for v in by_comp.values()), default=0))

            done_books = 0
            total_books = len({x for a, b, _ in cands for x in (a, b)})
            # ★ 一个进程池用到底：Windows 上每建一次池要 1~2 秒，
            #   按簇建池在 1768 个簇的书库上会白等一小时。
            pool = ProcessPoolExecutor(max_workers=workers) if (workers > 1 and total_books > 2) else None
            try:
                for ci, (_root, cpairs) in enumerate(by_comp.items(), 1):
                    involved = sorted({x for a, b, _ in cpairs for x in (a, b)})
                    tasks = [(books[bid].path, core.K, core.FINE_W, drop_ads, t2s)
                             for bid in involved]
                    fine: dict[int, dict] = {}
                    if pool is not None and len(tasks) > 1:
                        futs = [pool.submit(_work_fine, t) for t in tasks]
                        for fut, bid in zip(futs, involved):
                            fine[bid] = fut.result()
                    else:
                        for t, bid in zip(tasks, involved):
                            fine[bid] = _work_fine(t)
                    done_books += len(involved)
                    _p("精算证据", done_books, max(1, total_books),
                       f"{len(by_comp)} 个重复簇，正在算第 {ci} 簇（{len(involved)} 本）")

                    docs: dict[int, object] = {}
                    for a, b, _s in cpairs:
                        if a not in fine or b not in fine:
                            continue
                        fa = (array("q", fine[a]["pos"]), array("Q", fine[a]["hash"]))
                        fb = (array("q", fine[b]["pos"]), array("Q", fine[b]["hash"]))
                        st = judge.judge_pair(a, b, fa, fb, fine[a]["norm_chars"],
                                              fine[b]["norm_chars"], core.FINE_W)
                        ctx.candidates.append(st)       # 被拒的也留：导出后能离线重判
                        if not st.is_dup:
                            st.pairs = []
                            continue
                        stats.append(st)
                        computed.append(st)
                        if with_evidence:
                            segs = segments.build(st.pairs, core.FINE_W)
                            if segs:
                                for bid in (a, b):
                                    if bid not in docs:
                                        docs[bid] = _light_doc(fine[bid])
                                segments.render(segs, docs[a], docs[b])
                                ctx.segs[(a, b)] = segs
                        # ★ 关键：匹配点列表是最大的单项内存（近全同的两本书能有几十万对），
                        #   证据段算完就没用了。不清掉的话 1.7 万对重复能吃掉 10 GB（真实踩过）。
                        st.pairs = []
                    del fine, docs   # ★ 每簇算完立刻释放
            finally:
                if pool is not None:
                    pool.shutdown(wait=True)
            log.info("Pass2 完成 %d 本，用时 %.1fs", done_books, time.perf_counter() - t0)

        # ---- 合并历史边（新算的优先，同一对不重复）
        seen = {frozenset((x.a, x.b)) for x in stats}
        merged = 0
        for e in hist:
            k = frozenset((e.a, e.b))
            if k in seen:
                continue
            seen.add(k)
            stats.append(e)
            merged += 1
        if merged:
            log.info("并入历史关系 %d 条（未重新计算）", merged)

        # ---- 关系表落盘：全部算完才标记"完成"
        if computed:
            cache.save_edges(computed, feat, nv, core.JUDGE_VER)
        cache.mark_edges(feat, nv, True, core.JUDGE_VER)

        # ---- 分组
        stats.sort(key=lambda s: -s.jac)
        ctx.stats = stats
        ctx.groups = group_mod.build(stats, books)
        ctx.elapsed = time.perf_counter() - t_start
        ctx.peak_mb = core.peak_memory_mb()
        log.info("完成：%d 对重复，%d 组，用时 %.1fs，主进程峰值内存 %.0f MB",
                 len(ctx.dup_pairs), len(ctx.groups), ctx.elapsed, ctx.peak_mb)
        _p("完成", len(ctx.groups), len(ctx.groups))
        return ctx
    finally:
        cache.close()


def _store(cache: Cache, r: dict, feat: str, nv: str) -> None:
    b = Book(
        path=r["path"], size=r["size"], mtime=r["mtime"], enc=r["enc"],
        fmt=r.get("fmt", "txt"),
        raw_chars=r["raw_chars"], norm_chars=r["norm_chars"],
        word_chars=r.get("word_chars", 0),
        cjk=r["cjk"], common=r["common"], chapters=r["chapters"],
        ad_lines=r["ad_lines"], fps=r["fps"], feat_ver=feat,
        norm_ver=nv, l1=r["l1"], file_hash=r["file_hash"],
        status=r["status"], note=r["note"],
    )
    bid = cache.upsert(b)
    if r.get("hashes"):
        cache.write_fp(bid, array("Q", r["hashes"]))


# ------------------------------------------------------------------ 繁简


def normalize_t2s_ok() -> bool:
    from .normalize import t2s_available

    return t2s_available()


# ------------------------------------------------------------------ 按需证据


def evidence_for(ctx: ScanResult, a: int, b: int, drop_ads: bool | None = None,
                 k: int = core.K, w: int = core.FINE_W, t2s: bool | None = None) -> list:
    """为某一对**按需**生成证据段。

    为什么需要：增量扫描时，老-老 对的关系直接来自关系表（没有位置信息），
    自然也没有证据段。等用户真的点开看时才重算这一对——
    两个文件重算约几秒，比把 17,000 对的证据全算出来划算得多。

    ⚠ 预处理参数（drop_ads / t2s）默认**沿用当次扫描的设置**：
      用不同的规则重算，偏移就不是同一个坐标系了，证据会指到别的位置。
    """
    ba, bb = ctx.books.get(a), ctx.books.get(b)
    if not ba or not bb:
        return []
    if drop_ads is None:
        drop_ads = ctx.drop_ads
    if t2s is None:
        t2s = ctx.t2s
    key = (a, b)
    if key in ctx.segs:
        return ctx.segs[key]
    # L1（归一化全文 hash 相同）对不走 Pass2，本来没有位置级证据。
    # 但从元信息就能直接给一条"整本相同"，不需要重读两个文件——
    # 真实书库里绝大多数重复正好是这种"一字不差"的副本。
    if ba.l1 and ba.l1 == bb.l1:
        whole = min(ba.norm_chars, bb.norm_chars)
        if whole > 0:
            seg = segments.Segment(pos_a=0, pos_b=0, length=whole)
            ctx.segs[key] = [seg]
            return [seg]

    fa = _work_fine((ba.path, k, w, drop_ads, t2s))
    fb = _work_fine((bb.path, k, w, drop_ads, t2s))
    pa = (array("q", fa["pos"]), array("Q", fa["hash"]))
    pb = (array("q", fb["pos"]), array("Q", fb["hash"]))
    st = judge.judge_pair(a, b, pa, pb, fa["norm_chars"], fb["norm_chars"], w)
    segs = segments.build(st.pairs, w) if st.pairs else []
    if segs:
        segments.render(segs, _light_doc(fa), _light_doc(fb))
    ctx.segs[key] = segs
    return segs


# ------------------------------------------------------------------ 删除后


def forget_paths(root: Path, paths) -> int:
    """把已删除的文件从缓存里摘掉（否则下次扫描仍会命中它们的指纹文件）。"""
    from pathlib import Path as _P

    cache = Cache(core.cache_dir_for(_P(root)))
    try:
        n = 0
        for p in paths:
            if cache.get(str(p)):
                cache.drop(str(p))
                n += 1
        return n
    finally:
        cache.close()


def apply_deletions(ctx: ScanResult, removed_ids: set[int]) -> None:
    """从扫描结果里剔除已删除的书并重建分组 —— **不重新扫描**。

    删完之后界面要立刻正确：被删的书不能还挂在组里，
    组内只剩 1 本时整组消失（那已经不叫重复了）。
    """
    if not removed_ids:
        return
    for bid in removed_ids:
        ctx.books.pop(bid, None)
    ctx.stats = [s for s in ctx.stats if s.a in ctx.books and s.b in ctx.books]
    ctx.segs = {k: v for k, v in ctx.segs.items()
                if k[0] in ctx.books and k[1] in ctx.books}
    ctx.groups = group_mod.build(ctx.stats, ctx.books)
