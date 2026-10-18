"""归一化 + 指纹的吞吐基准（计划 §7.2 的性能基线）。

用法：
    py -3 tools/bench_norm.py [文件或目录] [--w 150]

不给路径时用合成语料（81 万字），方便在任何机器上复现。
输出每个环节的耗时与吞吐，用来判断"某次扫描变慢"是算法退步还是数据变了。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from noveldedup import core, formats   # noqa: E402
from noveldedup import selftest        # noqa: E402
from noveldedup.fingerprint import fingerprints_no_pos   # noqa: E402
from noveldedup.normalize import normalize               # noqa: E402


def bench_one(path: Path | None, w: int, label: str) -> None:
    if path is None:
        raw = selftest.make_book(810_000, 3).encode("utf-8")
        enc = "utf-8"
        item = formats.Extracted(text=raw.decode(enc), fmt="txt", enc=enc)
        read = 0.0
    else:
        t = time.perf_counter()
        data = path.read_bytes()
        item = formats.extract(path, data)
        read = time.perf_counter() - t

    t = time.perf_counter()
    doc = normalize(item.text, src_enc=item.enc, bad=item.bad)
    t_norm = time.perf_counter() - t

    t = time.perf_counter()
    hashes = fingerprints_no_pos(doc.text, core.K, w)
    t_fp = time.perf_counter() - t

    n = len(doc.text)
    total = read + t_norm + t_fp
    print(f"{label}")
    print(f"  原文 {len(item.text):,} 字符 → 归一化 {n:,} 字  ({item.fmt}/{item.enc})")
    print(f"  读盘     {read:7.2f}s")
    print(f"  归一化   {t_norm:7.2f}s   {n / max(t_norm, 1e-9) / 1e4:>8,.1f} 万字/秒")
    print(f"  指纹 w={w} {t_fp:7.2f}s   {n / max(t_fp, 1e-9) / 1e4:>8,.1f} 万字/秒"
          f"   {len(hashes):,} 条")
    print(f"  合计     {total:7.2f}s   {n / max(total, 1e-9) / 1e4:>8,.1f} 万字/秒")
    print(f"  内存峰值 {core.peak_memory_mb():,.0f} MB\n")


def main(argv: list[str]) -> int:
    w = core.PRESETS["标准"]
    args = [a for a in argv[1:]]
    if "--w" in args:
        i = args.index("--w")
        w = int(args[i + 1])
        del args[i:i + 2]

    print(f"小说查重 v{__import__('noveldedup').__version__} —— 吞吐基准\n")
    if not args:
        bench_one(None, w, "合成语料（81 万字）")
        return 0

    target = Path(args[0])
    if target.is_file():
        bench_one(target, w, target.name)
        return 0

    files = [p for p in sorted(target.rglob("*"))
             if p.is_file() and p.suffix.lower() in formats.supported_exts()]
    print(f"{target}：{len(files)} 个支持的文件\n")
    total_chars = 0.0
    for p in files[:5]:
        bench_one(p, w, p.name)
    for p in files[:5]:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
