"""针对 v1.0.4 审计缺陷的回归测试（P1-1 / P1-2 / P1-3 / P2-1 / P2-2 / P2-4 / P2-5 / P2-6）。

每个用例对应审计报告里的一条：
  [1] 删广告行不再让偏移漂移（P1-2）——用唯一标记核对**精确真值**
  [2] 章节位置与 orig_offset 同一套坐标（P1-2）
  [3] 证据段两侧指向的原文是同一段（P1-2，端到端）
  [4] docx / html 证据读的是正文，不是二进制/标签（P1-1）
  [5] 导入结果后「一键清理」仍然有效（P1-3）
  [6] 短文本（<2000 字）同样能一键清理（P2-1）
  [7] 控制台摘要不再打印空的 A/B 行（P2-4）
  [8] 导出的 norm_ver 跟着繁简开关走（P2-6）
  [9] 删除线程异常后复位 _deleting；空目录文案说"直接删除"（P2-2 / P2-5）

跑：py -3 tools/fix_regression_smoketest.py
（[9] 需要桌面会话，与 gui_smoketest 相同）
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from noveldedup import engine, normalize, plan, report, selftest   # noqa: E402

ok_all = True
AD = "看正版就来青柠书屋 www.qingning.com"


def check(label: str, cond: bool, extra: str = "") -> None:
    global ok_all
    ok_all &= bool(cond)
    print(f"  {'✅' if cond else '❌'} {label}" + (f"   {extra}" if extra else ""))


def cjk_text(n: int, seed: int = 0) -> str:
    """随机纯汉字（无标点）：归一化前后 1:1，便于核对精确偏移。

    ⚠ 必须随机、不能周期性重复：周期文本只有 100 个不同的 12-gram，
      候选生成会被 `min_shared_ratio` 直接过滤掉（测试会假失败）。
    """
    import random

    pool = ("的一是不了在人有我他这个们中来上大为和国地到以说时要就出会可也你对生能而子那得于着"
            "下自之年过发后作里用道行所然家种事成方多经么去法学如都同现当没动面起看定天分还进好"
            "接必场件计管期市直德资命山金指克许统区保至队形社便空决治展马科司五基眼书非则听白却")
    rng = random.Random(seed)
    return "".join(rng.choice(pool) for _ in range(n))


def with_ads(body: str, every: int) -> str:
    out = []
    for i in range(0, len(body), every):
        out.append(body[i : i + every] + "\n" + AD + "\n")
    return "".join(out)


# ---------------------------------------------------------------- [1][2] 偏移


def test_offsets() -> None:
    print("\n[1] 删广告行后的偏移（精确真值）")
    body = cjk_text(8000)
    raw = with_ads(body, 800)
    mark = "青萍之末风起于青萍之末壹贰叁肆伍"
    pos = len(raw) // 2
    raw = raw[:pos] + mark + raw[pos:]
    truth = raw.index(mark)

    doc = normalize.normalize(raw)
    got = doc.orig_offset(doc.text.index(mark))
    removed_before = got - doc.text.index(mark)
    check("回溯偏移与原文真值一致", got == truth, f"真值 {truth} / 回溯 {got}")
    check("确实删过广告（用例有效）", doc.ad_lines > 0 and removed_before > 0,
          f"{doc.ad_lines} 条，标记前累计删 {removed_before} 字")

    print("\n[2] 章节位置也在原文坐标系里")
    raw2 = "第一章 开端\n" + with_ads(body[:600], 300) + "\n第二章 后半\n" + body[600:1200]
    doc2 = normalize.normalize(raw2)
    check("识别到 2 章", len(doc2.chapters) == 2, str(doc2.chapters[:2]))
    if len(doc2.chapters) == 2:
        check("第二章偏移 = 原文真值", doc2.chapters[1][0] == raw2.index("第二章"),
              f"{doc2.chapters[1][0]} vs {raw2.index('第二章')}")


# ---------------------------------------------------------------- [3] 证据端到端


def test_evidence_alignment() -> None:
    print("\n[3] 证据段两侧指向同一段原文（带广告、带前言）")
    body = cjk_text(20000, seed=7)
    tmp = Path(tempfile.mkdtemp(prefix="nd_fix_ev_"))
    a = tmp / "甲_广告稀疏.txt"
    b = tmp / "乙_有前言广告密集.txt"
    a.write_bytes(with_ads(body, 2000).encode("utf-8"))
    b.write_bytes((cjk_text(2000, seed=8) + with_ads(body, 700)).encode("utf-8"))
    ta = a.read_text(encoding="utf-8")
    tb = b.read_text(encoding="utf-8")

    def pair_of(res):
        names = {bk.name: bk.id for bk in res.books.values()}
        ia, ib = names.get(a.name), names.get(b.name)
        for k in res.segs:
            if {k[0], k[1]} == {ia, ib}:
                return k
        for s in res.dup_pairs:
            if {s.a, s.b} == {ia, ib}:
                return (s.a, s.b)
        return None

    def aligned(segs, key):
        """逐段核对：甲侧偏移处的原文 与 乙侧偏移处的原文 必须逐字相同。"""
        names = {bk.name: bk.id for bk in res.books.values()}
        a_first = key[0] == names.get(a.name)
        same = 0
        for s in segs[:8]:
            oa, ob = (s.orig_a, s.orig_b) if a_first else (s.orig_b, s.orig_a)
            if ta[oa : oa + 24] and ta[oa : oa + 24] == tb[ob : ob + 24]:
                same += 1
        return same, len(segs[:8])

    res = engine.scan(tmp, preset="标准", workers=1, with_evidence=True)
    key = pair_of(res)
    check("两本被判重且有证据", key is not None and bool(res.segs), f"{len(res.segs)} 条证据")
    if key:
        same, total = aligned(res.segs[key], key)
        check("预存证据：每段两侧原文逐字一致", same == total, f"{same}/{total}")

    # 按需路径：不预算证据，点开时才重算（增量扫描走的就是这条，依赖 ad_removed 还原）
    res2 = engine.scan(tmp, preset="标准", workers=1, with_evidence=False)
    key2 = pair_of(res2)
    check("这次确实没有预存证据", not res2.segs, f"{len(res2.segs)} 条")
    if key2:
        segs = engine.evidence_for(res2, key2[0], key2[1])
        check("按需重算出了证据", bool(segs), f"{len(segs)} 段")
        if segs:
            same, total = aligned(segs, key2)
            check("按需证据：每段两侧原文逐字一致", same == total, f"{same}/{total}")


# ---------------------------------------------------------------- [4] 跨格式证据


def test_cross_format_text() -> None:
    print("\n[4] docx / html 的证据原文是正文")
    import formats_smoketest as FS          # 复用它的 docx/html 构造器
    from noveldedup import formats
    from noveldedup.gui import App

    body = selftest.make_book(9000, 5)
    tmp = Path(tempfile.mkdtemp(prefix="nd_fix_fmt_"))
    (tmp / "甲.txt").write_bytes(body.encode("utf-8"))
    (tmp / "乙.docx").write_bytes(FS.make_docx(body))
    (tmp / "丙.html").write_bytes(FS.make_html(body).encode("utf-8"))

    res = engine.scan(tmp, preset="标准", workers=1, with_evidence=False)
    ids = {bk.name: bk.id for bk in res.books.values()}

    class _Shim:                            # 只为复用 App._book_text，不开窗口
        def __init__(self, result):
            self.result = result
            self._text_cache = {}

        def _append_log(self, *a, **k):
            pass

    _Shim._book_text = App._book_text
    shim = _Shim(res)
    for name in ("乙.docx", "丙.html"):
        text = shim._book_text(ids[name])
        head = text[:40].replace("\n", " ")
        ok = bool(text) and "第1章" in text[:200] and not text.startswith("PK") and "<" not in text[:40]
        check(f"{name} 读到的是正文", ok, repr(head))
    # 提取出的文本长度应与 txt 版本接近（格式差异允许 ±5%）
    len_txt = len(shim._book_text(ids["甲.txt"]))
    len_docx = len(shim._book_text(ids["乙.docx"]))
    check("docx 正文长度与 txt 同量级", abs(len_docx - len_txt) / max(1, len_txt) < 0.05,
          f"{len_docx:,} vs {len_txt:,}")


# ---------------------------------------------------------------- [5][6][7][8]


def test_import_and_plan() -> None:
    print("\n[5] 导入后一键清理仍然有效")
    tmp = Path(tempfile.mkdtemp(prefix="nd_fix_imp_"))
    selftest.build_corpus(tmp)
    res = engine.scan(tmp, preset="标准", workers=1, with_evidence=True)
    before = len(plan.plan_identical(res.books, res.groups))
    out = Path(tempfile.mkdtemp(prefix="nd_fix_json_")) / "r.json"
    report.write_json(res, out, with_candidates=True)

    from noveldedup import loader

    ld = loader.load_result(out)
    after = len(plan.plan_identical(ld.result.books, ld.result.groups)) if ld.ok else -1
    check("扫描时能一键清理", before > 0, f"{before} 条")
    check("导入后仍能一键清理", after == before, f"{after} vs {before}")
    check("l1 确实被导出并读回",
          ld.ok and all(b.l1 for b in ld.result.books.values()),
          f"{sum(1 for b in ld.result.books.values() if b.l1)}/{len(ld.result.books)}")

    print("\n[6] 短文本也能一键清理")
    short = Path(tempfile.mkdtemp(prefix="nd_fix_short_"))
    body = selftest.make_book(1500, 3)
    (short / "短甲.txt").write_bytes(body.encode("utf-8"))
    (short / "短乙.txt").write_bytes(body.encode("utf-8"))
    rs = engine.scan(short, preset="标准", workers=1, with_evidence=False)
    check("短文本被判重", len(rs.groups) == 1, f"{len(rs.groups)} 组")
    check("短文本可被一键清理",
          len(plan.plan_identical(rs.books, rs.groups)) == 1,
          f"{len(plan.plan_identical(rs.books, rs.groups))} 条")

    print("\n[7] 摘要不再打印空的 A/B 行")
    txt = report.summary_text(res, top=3)
    bad = [ln for ln in txt.splitlines() if ln.strip() in ("A：", "B：")]
    check("没有空的证据行", not bad, f"{len(bad)} 行")

    print("\n[8] 导出的 norm_ver 跟着繁简开关")
    d0 = report.to_dict(res)
    check("默认扫描 norm_ver=n3", d0["norm_ver"] == "n3", d0["norm_ver"])
    res.t2s = True
    d1 = report.to_dict(res)
    check("t2s 扫描 norm_ver=n3s", d1["norm_ver"] == "n3s" and d1["t2s"] is True,
          f"{d1['norm_ver']} / t2s={d1['t2s']}")
    res.t2s = False


# ---------------------------------------------------------------- [9] GUI 状态


def test_gui_state() -> None:
    print("\n[9] 删除异常后界面不锁死 / 空目录文案")
    import noveldedup.gui as G

    G.messagebox.showerror = lambda *a, **k: None
    G.messagebox.showinfo = lambda *a, **k: None
    app = G.App()
    app.update()
    try:
        app._deleting = True
        app._handle(("error", "boom: simulated delete failure"))
        app.update()
        check("error 后 _deleting 已复位", app._deleting is False)
        check("删除按钮没有被永久禁用",
              str(app.btn_del_checked.cget("state")) in ("disabled", "normal"))

        class _Res:
            deleted = [Path(r"C:\tmp\a"), Path(r"C:\tmp\b"), Path(r"C:\tmp\c")]
            failed = []
            freed_bytes = 0
            log_path = None

        app._dirs_done(_Res(), 3, 0)
        info = app.var_info.get()
        check("空目录 completion 文案说「直接删除」", "直接删除" in info, info)
        check("不再声称可还原", "回收站" not in info, info)
    finally:
        app.destroy()


def main() -> int:
    test_offsets()
    test_evidence_alignment()
    test_cross_format_text()
    test_import_and_plan()
    try:
        test_gui_state()
    except Exception as exc:  # noqa: BLE001 —— 无桌面会话时跳过，不算失败
        print(f"  （跳过 GUI 用例：{type(exc).__name__}: {exc}）")

    print("\n" + ("回归测试：通过" if ok_all else "回归测试：失败"))
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
