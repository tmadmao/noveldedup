"""内存基准：跑一次扫描，报告主进程峰值内存。

为什么量主进程：10 GB 那次爆的是主进程——它同时持有
① 所有候选书的**完整原文**（旧版有）、② 所有候选书的细指纹、③ 每对重复的**匹配点列表**。
后两项现在改成「按重复簇分批 + 用完即清」，峰值应只跟最大的那个簇有关。

用法：
    py -3 tools/bench_memory.py [目录] [--copies N]
    --copies N  把目录里的书复制 N 份（轻微改写）造出大量重复对，用来压测
"""

from __future__ import annotations

import ctypes
import shutil
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _PMC(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def peak_mb() -> float:
    c = _PMC()
    c.cb = ctypes.sizeof(c)
    fn = ctypes.windll.psapi.GetProcessMemoryInfo
    # 64 位下必须声明 argtypes，否则句柄会被截断成 32 位，调用直接失败返回 0
    fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PMC), wintypes.DWORD]
    fn.restype = wintypes.BOOL
    ok = fn(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(c), c.cb)
    return c.PeakWorkingSetSize / 2 ** 20 if ok else 0.0


def make_copies(src: Path, dst: Path, n: int) -> None:
    """把 src 里的书复制 n 份，每份改动 0.5% 的字，制造"同书不同版本"。"""
    dst.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in src.glob("*.txt"))[:3]
    for i in range(n):
        for p in files:
            text = p.read_bytes().decode("gb18030", errors="replace")
            if i:  # 每份丢一点点字，避免被 L1 全文 hash 秒判（那样不走精算）
                step = max(200, len(text) // 200)
                text = "".join(c for j, c in enumerate(text) if j % step != i)
            (dst / f"{p.stem}_v{i}.txt").write_bytes(text.encode("gb18030"))


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1].startswith("--"):
        print(__doc__)
        print("提示：先准备一个小书库（有 2~3 本内容相近的书），再传目录。")
        return 2
    root = Path(argv[1])
    copies = 0
    if "--copies" in argv:
        copies = int(argv[argv.index("--copies") + 1])

    tmp = None
    if copies:
        tmp = Path(tempfile.mkdtemp(prefix="memtest_"))
        print(f"生成 {copies} 份副本到 {tmp} …")
        make_copies(root, tmp, copies)
        root = tmp

    from noveldedup import core, engine

    log = core.setup_logging(False)
    t0 = time.perf_counter()
    res = engine.scan(root, log=log)
    dt = time.perf_counter() - t0
    print()
    print(f"书 {len(res.books)} 本｜组 {len(res.groups)}｜对 {len(res.dup_pairs)}"
          f"｜用时 {dt:.1f}s")
    print(f"主进程峰值内存：{peak_mb():,.0f} MB")
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
