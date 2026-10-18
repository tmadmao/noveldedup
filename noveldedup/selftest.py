"""自检：11 组已知答案语料（合成）+ 三条不变量校验。

对应开发计划 §6 的 M0 语料表。合成语料只用来验证**算法与判据**，
真实语料的验证结论见 docs/PROJECT-STATUS.md。
"""

from __future__ import annotations

import random
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import core, judge
from .fingerprint import fingerprints

_POOL = (
    "的一是不了在人有我他这个们中来上大为和国地到以说时要就出会可也你对生能而子那得于着下自之年过发后作里"
    "用道行所然家种事成方多经么去法学如都同现当没动面起看定天分还进好小部其些主样理心她本前开但因只从想实"
    "日军者意无力它与长把机十民第公此已工使情明性知全三又关点正业外将两高间由问很最重并物手应战向头文体政"
    "美相见被利什二等产或新己制身果加西斯月话合回特代内信表化老给世位次度门任常先海通教儿原东声提立及比员"
    "解水名真论处走义各入几口认条平系气题活尔更别打女变四神总何电数安少报才结反受目太量再感建务做接必场件"
    "计管期市直德资命山金指克许统区保至队形社便空决治展马科司五基眼书非则听白却界达光放强即像难且权思王象"
)
_CLAUSE = "的一是在不了有和人这中大为上个国我以要他时来用们生到作地于出就分对成会可主发年动"
_AD = "看正版就来青柠书屋"


def make_book(n_chars: int, seed: int) -> str:
    rng = random.Random(seed)
    pool = _POOL
    n = len(pool)
    out: list[str] = []
    total = 0
    ch = 1
    while total < n_chars:
        head = f"第{ch}章　风起于青萍之末\n\n"
        out.append(head)
        total += len(head)
        body = int(rng.uniform(1500, 2500))
        buf: list[str] = []
        got = 0
        while got < body:
            if rng.random() < 0.6:
                s = "".join(rng.choice(_CLAUSE) for _ in range(rng.randint(6, 14)))
            else:
                s = "".join(pool[rng.randrange(n)] for _ in range(rng.randint(4, 12)))
            buf.append(s)
            buf.append(rng.choice("，。！？；：、"))
            got += len(s) + 1
        out.append("".join(buf) + "\n\n")
        total += got
        ch += 1
    return "".join(out)


def _split_chapters(text: str) -> list[str]:
    parts = [x for x in re.split(r"(?=第\d+章)", text) if x.strip()]
    return parts or [text]


def build_corpus(root: Path) -> dict[str, str]:
    """造 12 个文件，返回 {文件名: 期望关系}（'' = 不该判重）。"""
    root.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}

    A = make_book(22_000, 11)

    def w(name: str, text: str, enc: str = "utf-8") -> None:
        (root / name).write_bytes(text.encode(enc, errors="replace"))

    # ① 复制改名
    w("g01_甲版.txt", A)
    w("g01_乙版.txt", A)
    files["g01_甲版.txt"] = files["g01_乙版.txt"] = judge.SAME_EXACT

    # ② 编码 / 段首缩进 / 全角标点
    styled = "\r\n".join("　　" + ln.strip() for ln in A.split("\n") if ln.strip())
    styled = styled.replace("，", "，").replace("。", "。")
    w("g02_排版版.txt", styled, "gb18030")
    files["g02_排版版.txt"] = judge.SAME_EXACT

    # ③ 每 3000 字插一条广告
    parts = []
    for i in range(0, len(A), 3000):
        parts.append(A[i : i + 3000])
        parts.append("\n" + _AD + "\n")
    w("g03_广告版.txt", "".join(parts))
    files["g03_广告版.txt"] = judge.SAME_INSERT

    # ④ 前面加了 3 万字前言
    preface = make_book(8_000, 99)
    w("g04_加前言.txt", preface + A)
    files["g04_加前言.txt"] = judge.A_SUB_B

    # ⑤ 单卷 vs 三卷合订
    v2 = make_book(20_000, 22)
    v3 = make_book(20_000, 33)
    w("g05_三卷合订.txt", A + v2 + v3)
    files["g05_三卷合订.txt"] = judge.A_SUB_B

    # ⑥ 断更：只有前 75%
    cut = A[: int(len(A) * 0.75)]
    w("g06_断更版.txt", cut)
    files["g06_断更版.txt"] = judge.B_SUB_A

    # ⑦ 章节顺序打乱
    chs = _split_chapters(A)
    order = list(range(len(chs)))
    for i in range(0, len(order) - 1, 2):
        j = min(len(order) - 1, i + 5)
        order[i], order[j] = order[j], order[i]
    w("g07_乱序版.txt", "".join(chs[k] for k in order))
    files["g07_乱序版.txt"] = judge.REORDERED

    # ⑧ 5% 章节被改写
    keep = []
    for i, seg in enumerate(chs):
        keep.append(make_book(2000, 1000 + i) if i % 20 == 3 else seg)
    w("g08_改写版.txt", "".join(keep))
    files["g08_改写版.txt"] = judge.REVISED

    # ⑨ 每 200 字 1 个 OCR 错字
    rng = random.Random(9)
    ocr = []
    for i, ch in enumerate(A):
        ocr.append(_POOL[rng.randrange(len(_POOL))] if i % 200 == 100 else ch)
    w("g09_错字版.txt", "".join(ocr))
    files["g09_错字版.txt"] = judge.SAME_MINOR

    # ⑩ 完全无关
    w("g10_无关书.txt", make_book(22_000, 77))
    files["g10_无关书.txt"] = ""

    # ⑪ 两本无关书共享 3000 字套路 → 不该判重
    boiler = make_book(3000, 555)
    w("g11_套路甲.txt", make_book(12_000, 401)[:6000] + boiler + make_book(12_000, 402)[:6000])
    w("g11_套路乙.txt", make_book(12_000, 501)[:6000] + boiler + make_book(12_000, 502)[:6000])
    files["g11_套路甲.txt"] = files["g11_套路乙.txt"] = ""
    return files


# ------------------------------------------------------------------ 校验


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def verify_kernel() -> list[CheckResult]:
    """三条不变量（与真实语料无关，纯算法）。"""
    out: list[CheckResult] = []
    text = "".join(make_book(30_000, 7))
    text = text.encode("utf-8").decode("utf-8")

    # 1) 密度接近理论值 2/(w+1)
    for w in (150, 40):
        pos, hs = fingerprints(text, core.K, w)
        n_kg = max(1, len(text) - core.K + 1)
        dens = len(pos) / n_kg
        want = 2 / (w + 1)
        ok = abs(dens - want) / want < 0.25
        out.append(CheckResult(f"密度 w={w}", ok, f"实测 {dens:.3%} vs 理论 {want:.3%}"))

    # 2) 保证性：任意长度 t 的子串必与原文共享指纹
    for w in (150, 40):
        t = core.t_of(w)
        pos, hs = fingerprints(text, core.K, w)
        fset = set(hs)
        rng = random.Random(3)
        miss = 0
        for _ in range(60):
            s = rng.randrange(0, len(text) - t)
            _p, hh = fingerprints(text[s : s + t], core.K, w)
            if not (set(hh) & fset):
                miss += 1
        out.append(CheckResult(f"保证性 w={w}（t={t}）", miss == 0, f"未命中 {miss}/60"))

    # 3) 同文不同编码 → 归一化后完全一致
    from .normalize import normalize

    a = normalize(text)
    b = normalize(text.replace("\n", "\r\n"))
    out.append(CheckResult("换行归一", a.text == b.text, f"{len(a.text):,} 字"))
    return out


def run(log=None) -> tuple[int, int, list[str]]:
    """跑 11 组语料，返回 (通过数, 总数, 明细)。"""
    from .engine import scan

    log = log or core.setup_logging()
    tmp = Path(tempfile.mkdtemp(prefix="noveld_selftest_"))
    lines: list[str] = []
    passed = total = 0
    try:
        expect = build_corpus(tmp)
        res = scan(tmp, preset="标准", workers=1, log=log, with_evidence=True)
        found: dict[tuple[int, int], judge.PairStat] = {}
        for st in res.dup_pairs:
            found[(st.a, st.b)] = st
        id_by_name = {b.name: b.id for b in res.books.values()}
        name_by_id = {b.id: b.name for b in res.books.values()}

        base = id_by_name.get("g01_甲版.txt")
        for name, want in expect.items():
            if name == "g01_甲版.txt":
                continue
            bid = id_by_name.get(name)
            total += 1
            if bid is None or base is None:
                lines.append(f"❌ {name}: 文件未进入结果")
                continue
            st = found.get((base, bid)) or found.get((bid, base))
            got = st.rel if st else judge.NO_REL
            if want == "":
                ok = got == judge.NO_REL
            elif want == judge.SAME_EXACT:
                ok = got in (judge.SAME_EXACT, judge.SAME_INSERT)
            elif want == judge.SAME_INSERT:
                ok = got in (judge.SAME_INSERT, judge.SAME_MINOR, judge.SAME_EXACT)
            elif want == judge.SAME_MINOR:
                ok = got in (judge.SAME_MINOR, judge.REVISED)
            else:
                ok = got == want
            passed += int(ok)
            lines.append(f"{'✅' if ok else '❌'} {name:<18} 期望 {want or '不判重':<22} "
                         f"实判 {got:<22} Jaccard {st.jac:.3f}" if st else
                         f"{'✅' if ok else '❌'} {name:<18} 期望 {want or '不判重'}  实判 不判重")

        # g11 两本套路书之间不该判重
        a11 = id_by_name.get("g11_套路甲.txt")
        b11 = id_by_name.get("g11_套路乙.txt")
        total += 1
        if a11 and b11:
            st = found.get((a11, b11)) or found.get((b11, a11))
            ok = st is None
            passed += int(ok)
            lines.append(f"{'✅' if ok else '❌'} g11_套路甲乙之间      期望 不判重"
                         f"                实判 {(st.rel if st else '不判重')}")
        else:
            lines.append("❌ g11 文件缺失")

        # 结构标签：**单独一个小语料**验证（混在 11 组里会被其它版本干扰，
        # 因为 g04/g06/g07/g08 都与 g01 同源，最后会并成一个大组）
        total += 1
        sub = Path(tempfile.mkdtemp(prefix="noveld_omni_"))
        try:
            a2 = make_book(22_000, 11)
            (sub / "单卷.txt").write_bytes(a2.encode("utf-8"))
            (sub / "全集.txt").write_bytes(
                (a2 + make_book(20_000, 22) + make_book(20_000, 33)).encode("utf-8"))
            r2 = scan(sub, preset="标准", workers=1, log=log, with_evidence=False)
            struct = r2.groups[0].structure if r2.groups else "未成组"
            ok = struct == "合订-分卷"
            passed += int(ok)
            lines.append(f"{'✅' if ok else '❌'} 结构识别 单卷vs合订  期望 合订-分卷  "
                         f"实判 {struct}")
        finally:
            shutil.rmtree(sub, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return passed, total, lines


def verify_text() -> str:
    lines: list[str] = []
    n_ok = 0
    items = verify_kernel()
    for it in items:
        n_ok += int(it.ok)
        lines.append(f"{'✅' if it.ok else '❌'} {it.name:<24} {it.detail}")
    lines.append(f"\n内核校验 {n_ok}/{len(items)}")
    return "\n".join(lines)
