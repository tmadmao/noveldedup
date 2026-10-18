"""全局常量、路径、预设、日志。

版本号约定
----------
NORM_VER   归一化规则版本。改动 normalize.py 的映射表/广告规则/编码探测 → 必须 +1。
FEAT_VER   指纹算法版本（含 k 与粗指纹窗口 w）。改档位即改 FEAT_VER。
任何一个变了，缓存里的指纹就作废重算。
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from . import APP_EN

# ---------------------------------------------------------------- 算法常数

K = 12                  # k-gram 长度（噪声阈值）
FINE_W = 40             # 细指纹窗口：保证 t = 51 字，用于证据与精算
AD_MAXLEN = 30          # 广告行判定：整行字数上限
SHORT_CHARS = 2000      # 归一化字数低于此值 → 标「短文本」，判据不稳（计划 §7.1）

NORM_VER = "n3"         # n3 = 单张 translate 表（v2.3）

# 判据版本。**改了 classify() / 阈值，就必须 +1** ——
# 否则缓存里的关系表会带着旧判据的结果被复用，
# 修复对用户完全无效（真实踩过：改了判据但重扫还是老结果）。
#   j1 = v1.0.0（"章节顺序不同"排在 Jaccard 之前，导致整库误连）
#   j2 = v1.0.1（先过相似度底线 + 对称门槛 + 组大小上限）
JUDGE_VER = "j2"


def norm_ver(t2s: bool = False) -> str:
    """归一化版本号。开启繁简转换就是另一套规则 → 缓存必须全部失效重算。"""
    return "n3s" if t2s else NORM_VER


def feat_ver(w: int) -> str:
    """粗指纹版本号。换档位（w）即失效。"""
    return f"f{K}_{w}"


PRESETS: dict[str, int] = {
    "极速": 300,
    "快速": 200,
    "标准": 150,
    "精细": 100,
    "极精细": 40,
}
DEFAULT_PRESET = "标准"
INDEX_BUDGET_MB = 600   # 索引预算，超过则自动升档


def t_of(w: int) -> int:
    """该档位保证检出的最短公共子串长度：t = w + k - 1。"""
    return w + K - 1


def pick_preset(total_chars: int, budget_mb: int = INDEX_BUDGET_MB) -> tuple[str, int]:
    """按实际总字数自动选档（从精细到极速，取第一个满足预算的）。"""
    for name in ("精细", "标准", "快速", "极速"):
        w = PRESETS[name]
        mb = total_chars * (2 / (w + 1)) * 4 / 2 ** 20
        if mb <= budget_mb:
            return name, w
    return "极速", PRESETS["极速"]


def est_index_mb(total_chars: int, w: int) -> float:
    return total_chars * (2 / (w + 1)) * 4 / 2 ** 20


# ---------------------------------------------------------------- 判据阈值

class TH:
    """判据阈值。全部需在真实语料上复核（见 docs/开发计划 §3.4.3）。"""

    min_shared = 8          # 进候选的绝对下限
    min_shared_ratio = 0.05  # 且 共享数 / min(|A|,|B|) 不低于此值
    # 且 共享数 / max(|A|,|B|) 不低于此值。
    # 这一条专治"短篇 vs 长篇"：4250 字的短篇被 352 万字的长篇包含时，
    # shared/min ≈ 1（通过），但 shared/max ≈ 0.003（拦住）。
    # 没有它，真实书库里 2/3 的边都是这种噪声。
    min_shared_sym = 0.02
    max_group = 60          # 单个重复组的成员上限（正常作品不会有 60 个副本）
    chain_floor = 4         # LIS 链长下限
    ratio_thr = 0.25        # 链长 / min(指纹数)
    in_order = 0.6          # 链长 / 共享数 低于此值 → 章节乱序
    contain_hi = 0.95       # 包含度：判定"是子集"
    contain_lo = 0.90       # 包含度：判定"对方有大量独有内容"
    same_ver = 0.98         # 双向包含度都 ≥ 此值 → 同一版本
    cov_same = 0.97         # 字符覆盖率都 ≥ 此值 → 只是插入/排版
    cov_same_jac = 0.90     # 且 Jaccard ≥ 此值 → "仅插入"；否则"有细微字符差异"
    jac_same = 0.55         # Jaccard 下限：低于此值不算重复
    jac_rev = 0.25          # 疑似改字版
    max_df = 50             # 某指纹出现在 > 这么多本书里 → 当套话丢弃
    common_min = 0.40       # 常用字覆盖率下限（编码自检）
    cjk_min = 0.30          # CJK 占比下限（第二道检查）


# ---------------------------------------------------------------- 路径

def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def app_root() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def data_dir() -> Path:
    """缓存根目录（不写进用户的书籍目录）。"""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
    root = Path(base) / APP_EN if base else Path.home() / (".%s" % APP_EN.lower())
    root.mkdir(parents=True, exist_ok=True)
    return root


def cache_dir_for(scan_root: Path) -> Path:
    """每个书籍根目录一个缓存子目录，按路径哈希区分。"""
    import hashlib

    key = hashlib.blake2b(str(scan_root.resolve()).encode("utf-8", "replace"), digest_size=8)
    d = data_dir() / "cache" / key.hexdigest()
    (d / "fp").mkdir(parents=True, exist_ok=True)
    return d


def dir_size_mb(path: Path) -> float:
    """目录总大小（MB）。用来在界面上告诉用户"指纹缓存占了多少地方"。"""
    total = 0
    try:
        for p in Path(path).rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                continue
    except OSError:
        return 0.0
    return total / 2 ** 20


# ---------------------------------------------------------------- 内存

def peak_memory_mb() -> float:
    """当前进程的峰值工作集（MB）。Windows 用 psapi，其它平台返回 0。

    为什么要报这个：真实书库（上万本 / 1.7 万对重复）曾把内存吃到 10 GB。
    峰值必须能在日志里看到，否则下次再涨也没人发现。
    """
    try:
        import ctypes
        from ctypes import wintypes

        class _PMC(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        c = _PMC()
        c.cb = ctypes.sizeof(c)
        fn = ctypes.windll.psapi.GetProcessMemoryInfo
        fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PMC), wintypes.DWORD]
        fn.restype = wintypes.BOOL
        if not fn(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(c), c.cb):
            return 0.0
        return c.PeakWorkingSetSize / 2 ** 20
    except Exception:  # noqa: BLE001
        return 0.0


# ---------------------------------------------------------------- 日志

def setup_logging(verbose: bool = False, logfile: Path | None = None) -> logging.Logger:
    """日志。

    ⚠ pythonw.exe（双击 .pyw）下 sys.stderr 是 None，
    直接 logging.StreamHandler(sys.stderr) 会让每条日志在 emit 时静默失败。
    所以先判断 stderr 是否可用；文件日志始终写，出问题才能查。
    """
    log = logging.getLogger("noveldedup")
    if log.handlers:
        return log
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")
    if sys.stderr is not None:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        log.addHandler(sh)
    path = logfile if logfile else (data_dir() / "run.log")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(path, encoding="utf-8")
        fh.setFormatter(fmt)
        log.addHandler(fh)
    except OSError:
        pass
    if not log.handlers:
        log.addHandler(logging.NullHandler())
    return log
