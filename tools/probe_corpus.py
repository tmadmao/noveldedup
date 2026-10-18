"""真语料体检：编码探测 / 字数 / CJK 占比 / 章节数 / 广告行数。

用法：
    python tools/probe_corpus.py [目录]

这是开发计划 M1 里 `probe_encoding.py` 的第一版。它只做"读得对不对"，
不做判重——判重见 tools/probe_real.py。

为什么必须先做这一步：中文 txt 的编码错判后果是**整本静默失效**
（指纹全废、判不出重复，但程序不报错）。所以编码正确率必须先量出来。
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

CJK = re.compile(r"[\u4e00-\u9fff]")
CHAP = re.compile(r"^\s*第\s*[0-9一二三四五六七八九十百千万零两]+\s*[章节回卷]", re.M)
AD_HINT = re.compile(
    r"(www\.|http|本书由|首发|转载|手机阅读|TXT下载|请记住本站|最新章节|无弹窗|免费阅读)", re.I
)
BAD = re.compile(r"[\ufffd]")


def try_decode(raw: bytes, enc: str) -> tuple[str | None, int]:
    try:
        t = raw.decode(enc)
    except UnicodeDecodeError:
        # 容错解码，统计替换字符数
        t = raw.decode(enc, errors="replace")
        return t, len(BAD.findall(t))
    return t, 0


def detect_encoding(raw: bytes) -> tuple[str, str, int]:
    """返回 (编码, 说明, 替换字符数)。顺序与计划 §3.1 一致。"""
    if raw.startswith(b"\xef\xbb\xbf"):
        t, bad = try_decode(raw, "utf-8-sig")
        return "utf-8-sig", "BOM 命中", bad
    if raw.startswith(b"\xff\xfe"):
        t, bad = try_decode(raw, "utf-16-le")
        return "utf-16-le", "BOM 命中", bad
    if raw.startswith(b"\xfe\xff"):
        t, bad = try_decode(raw, "utf-16-be")
        return "utf-16-be", "BOM 命中", bad

    # 无 BOM：先严格试 UTF-8（含中文的合法 UTF-8 极难被误判）
    try:
        raw.decode("utf-8")
        return "utf-8", "严格 UTF-8 全部通过", 0
    except UnicodeDecodeError:
        pass

    # 退到 GB18030：它几乎能解任何字节流，所以必须用 CJK 占比校验
    t, bad = try_decode(raw, "gb18030")
    ratio = len(CJK.findall(t)) / max(1, len(t))
    if ratio >= 0.30:
        return "gb18030", f"CJK 占比 {ratio:.1%}", bad

    # 都不像：取 CJK 占比最高的那个兜底
    best = ("gb18030", ratio, t, bad)
    for enc in ("big5", "utf-16-le", "latin-1"):
        t2, b2 = try_decode(raw, enc)
        r2 = len(CJK.findall(t2)) / max(1, len(t2))
        if r2 > best[1]:
            best = (enc, r2, t2, b2)
    return best[0], f"兜底，CJK 占比 {best[1]:.1%}", best[3]


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    root = Path(argv[1])
    if not root.is_dir():
        print(f"目录不存在：{root}")
        return 2

    files = sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() == ".txt")
    print(f"目录：{root}")
    print(f"文件数：{len(files)}\n")
    header = f"{'文件':<46}{'体积MB':>8}{'编码':<12}{'字数':>10}{'CJK%':>7}{'章节':>6}{'广告行':>7}{'替换符':>7}"
    print(header)
    print("-" * len(header))

    total_chars = 0
    total_cjk = 0
    rows = []
    for p in files:
        raw = p.read_bytes()
        t0 = time.perf_counter()
        enc, note, bad = detect_encoding(raw)
        text = raw.decode(enc, errors="replace")
        dt = time.perf_counter() - t0
        n = len(text)
        cjk = len(CJK.findall(text))
        chaps = len(CHAP.findall(text))
        ads = sum(1 for ln in text.splitlines() if len(ln) < 60 and AD_HINT.search(ln))
        total_chars += n
        total_cjk += cjk
        name = p.name if len(p.name) <= 44 else p.name[:41] + "..."
        print(
            f"{name:<46}{len(raw)/2**20:>8.1f}{enc:<12}{n:>10,}{cjk/max(1,n):>6.1%}"
            f"{chaps:>6}{ads:>7}{bad:>7}"
        )
        rows.append((p.name, enc, n, cjk, chaps, ads, bad, dt))

    print("-" * len(header))
    print(f"合计：{total_chars:,} 字符（CJK {total_cjk:,}），{len(files)} 本")
    slow = max(rows, key=lambda r: r[7])
    print(f"解码最慢：{slow[0]}  {slow[7]:.2f}s")

    encs: dict[str, int] = {}
    for r in rows:
        encs[r[1]] = encs.get(r[1], 0) + 1
    print(f"编码分布：{encs}")

    bad_files = [r for r in rows if r[6] > 0]
    if bad_files:
        print(f"\n⚠ 有替换字符的文件（可能解码错）：{[(r[0], r[6]) for r in bad_files]}")

    low_cjk = [r for r in rows if r[3] / max(1, r[2]) < 0.30]
    if low_cjk:
        print(f"\n⚠ CJK 占比过低（可疑）：{[(r[0], f'{r[3]/r[2]:.1%}') for r in low_cjk]}")
    else:
        print("\n✅ 全部文件 CJK 占比正常（>30%）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
