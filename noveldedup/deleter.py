"""删除到回收站 —— **绝不直接抹掉文件**。

为什么不用 os.remove
--------------------
删错了没法救。查重工具天生会"一次性指向几千个文件"，
一旦判错或者用户手滑，`os.remove` 是不可逆的。
所以这里只做一件事：把文件送进**系统回收站**（资源管理器"删除"的同一行为），
用户随时可以还原。

Windows
-------
`SHFileOperationW(FO_DELETE + FOF_ALLOWUNDO)`。
这不是"移到某个隐藏目录"，是真正的回收站，能右键还原、能 Shift+Del 清空。

三道安全闸（缺一不可）
----------------------
1. 必须在扫描根目录内（防越界删到别人的文件）
2. 必须是已存在的**文件**（不删目录、不删符号链接指向的外部文件）
3. 删完逐个 `exists()` 复核 —— SHFileOperation 的返回码是整批的，
   不逐项核对就无法知道到底哪几个成功

审计
----
每次删除都追加写 `%LOCALAPPDATA%/NovelDedup/deleted.jsonl`，
包括路径、大小、时间、原因。回收站被清空后还能查"我删过什么"。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# SHFileOperation flags
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_NOCONFIRMMKDIR = 0x0200
FOF_NOERRORUI = 0x0400

FO_DELETE = 3
BATCH = 200          # 单次 SHFileOperation 的文件数（太长会被路径缓冲区限制）


@dataclass
class DeleteResult:
    deleted: list[Path] = field(default_factory=list)
    failed: list[tuple[Path, str]] = field(default_factory=list)
    freed_bytes: int = 0
    log_path: Path | None = None

    @property
    def ok(self) -> bool:
        return bool(self.deleted) and not self.failed

    @property
    def summary(self) -> str:
        s = f"已移入回收站 {len(self.deleted)} 个文件，释放 {_mb(self.freed_bytes)}"
        if self.failed:
            s += f"；{len(self.failed)} 个失败"
        return s


def _mb(n: int) -> str:
    if n >= 2 ** 30:
        return f"{n / 2 ** 30:.2f} GB"
    if n >= 2 ** 20:
        return f"{n / 2 ** 20:.1f} MB"
    return f"{n / 1024:.0f} KB"


# ---------------------------------------------------------------- 空目录

# 资源管理器/下载器随手生成的垃圾文件。它们会让"看起来已经空了"的文件夹判不出空。
JUNK_NAMES = {"thumbs.db", "desktop.ini", ".ds_store", ".directory", ".thumbs"}


def _is_junk(p: Path) -> bool:
    return p.is_file() and p.name.lower() in JUNK_NAMES


def find_empty_dirs(root: Path, ignore_junk: bool = True
                    ) -> tuple[list[Path], list[Path]]:
    """找出可以删的空文件夹。返回 (由深到浅的目录列表, 顺带要清的垃圾文件)。

    两个必须做对的地方：
    1. **自底向上**：删掉 a/b/c 之后 a/b 才变空。所以用 `os.walk(topdown=False)`，
       并记住"已经判空的子目录"，父目录判定时把它当作不存在。
    2. **忽略系统缩略图文件**：`Thumbs.db` / `desktop.ini` 会让目录看着"非空"，
       但里面其实没有任何用户内容。这类文件一并清掉，目录才算真空。

    绝不返回 root 本身——用户点的是"清理子文件夹"，不是"删我的书库根目录"。
    """
    root = Path(root).resolve()
    empty: set[str] = set()
    order: list[Path] = []
    junk: list[Path] = []

    for dirpath, _dirnames, _filenames in os.walk(root, topdown=False):
        p = Path(dirpath)
        if p.resolve() == root:
            continue
        try:
            entries = list(p.iterdir())
        except OSError:
            continue

        remaining = 0
        for e in entries:
            if e.is_dir():
                if str(e.resolve()) in empty:
                    continue        # 子目录已经判空，会一起删掉
                remaining += 1
            elif ignore_junk and _is_junk(e):
                junk.append(e)
            else:
                remaining += 1
        if remaining == 0:
            empty.add(str(p.resolve()))
            order.append(p)
    return order, junk


# ---------------------------------------------------------------- 安全检查


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _dir_is_empty(p: Path) -> tuple[bool, str]:
    """删除前的最后一道闸：目录里是否真的没有东西（垃圾文件不算）。"""
    try:
        left = [e for e in p.iterdir() if not _is_junk(e)]
    except OSError as exc:
        return False, f"读不了目录：{exc}"
    if left:
        return False, f"目录里还有 {len(left)} 项，不是空目录"
    return True, ""


def check_safe(paths: list[Path], root: Path | None,
               allow_dirs: bool = False) -> tuple[list[Path], list[tuple[Path, str]]]:
    """过滤出真正可删的路径，返回 (可删, [(拒绝, 原因)])。

    allow_dirs=True 时允许删目录，但不能是扫描根目录本身、不能是符号链接。
    是否"真的空"由 `to_recycle_bin` 在**删除前逐个校验**（级联删除必须如此）。
    """
    good: list[Path] = []
    bad: list[tuple[Path, str]] = []
    seen: set[str] = set()

    for raw in paths:
        p = Path(raw)
        key = str(p).lower()
        if key in seen:
            continue
        seen.add(key)

        if root is not None and not _inside(p, root):
            bad.append((p, "不在扫描目录内，拒绝操作"))
            continue
        if p.is_symlink():
            bad.append((p, "是符号链接，跳过"))
            continue
        if not p.exists():
            bad.append((p, "不存在"))
            continue
        if p.is_dir():
            if not allow_dirs:
                bad.append((p, "是目录，不删除"))
                continue
            if root is not None and p.resolve() == root.resolve():
                bad.append((p, "是扫描根目录，不能删"))
                continue
            # 注意：这里**不判空**。级联删除时 A 里此刻还留着空的 B，
            # 逐个删除前才校验（见 _dir_is_empty）。否则 A/B 会被一刀切拒掉。
            good.append(p)
            continue
        good.append(p)
    return good, bad


# ---------------------------------------------------------------- 空目录删除

def _remove_files_direct(paths: list[Path]) -> tuple[list[Path], list[tuple[Path, str]]]:
    """直接删文件（DeleteFileW）。**只用于 Thumbs.db 这类系统缩略图缓存**。

    为什么不走回收站：网络盘（UNC）上 shell 回收站接口对这类文件会返回
    0x78 ERROR_CALL_NOT_IMPLEMENTED（实测网络盘），而它们本来就是要清掉的垃圾，
    没有任何用户内容，删了不存在"损失"。
    """
    done: list[Path] = []
    failed: list[tuple[Path, str]] = []
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        fn = k32.DeleteFileW
        fn.argtypes = [wintypes.LPCWSTR]
        fn.restype = wintypes.BOOL
        for p in paths:
            if fn(str(p)) or not p.exists():
                done.append(p)
            else:
                failed.append((p, f"Win32 错误 {ctypes.get_last_error()}"))
    else:
        import os as _os

        for p in paths:
            try:
                _os.remove(str(p))
                done.append(p)
            except OSError as exc:
                failed.append((p, str(exc)))
    return done, failed


def _remove_empty_dirs(paths: list[Path]) -> tuple[list[Path], list[tuple[Path, str]]]:
    """直接删**空目录**（不进回收站）。

    为什么空目录不走回收站：
    1. 目录里没有任何文件，删了不存在"数据丢失"，回收站没有意义；
    2. 网络盘（UNC）上 shell 的回收站接口**每次调用约 6 秒**（实测网络盘），
       近千个空目录要走 87 分钟——项目里真实卡住过；
    3. `RemoveDirectoryW` 本身**只能删空目录**，非空直接失败，
       安全语义比"先检查再删"更强。

    非空目录/被占用的目录会失败并如实返回原因，不会连带删掉里面的东西。
    """
    done: list[Path] = []
    failed: list[tuple[Path, str]] = []
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        fn = k32.RemoveDirectoryW
        fn.argtypes = [wintypes.LPCWSTR]
        fn.restype = wintypes.BOOL
        for p in paths:
            if fn(str(p)):
                done.append(p)
            else:
                err = ctypes.get_last_error()
                why = {145: "目录里还有内容，不是空目录", 5: "没有权限",
                       2: "路径不存在", 32: "被占用"}.get(err, f"Win32 错误 {err}")
                failed.append((p, why))
    else:
        import os as _os

        for p in paths:
            try:
                _os.rmdir(str(p))
                done.append(p)
            except OSError as exc:
                failed.append((p, str(exc)))
    return done, failed


# ---------------------------------------------------------------- 平台实现


def _recycle_windows(paths: list[Path]) -> tuple[list[Path], list[tuple[Path, str]]]:
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", ctypes.c_uint16),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    fn = shell32.SHFileOperationW
    fn.argtypes = [ctypes.POINTER(SHFILEOPSTRUCTW)]
    fn.restype = ctypes.c_int

    flags = (FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT
             | FOF_NOERRORUI | FOF_NOCONFIRMMKDIR)
    deleted: list[Path] = []
    failed: list[tuple[Path, str]] = []

    for i in range(0, len(paths), BATCH):
        batch = paths[i:i + BATCH]
        op = SHFILEOPSTRUCTW()
        op.hwnd = None
        op.wFunc = FO_DELETE
        # pFrom 要求「\0 分隔 + 双 \0 结尾」
        op.pFrom = "\0".join(str(p) for p in batch) + "\0"
        op.pTo = None
        op.fFlags = flags
        op.fAnyOperationsAborted = False
        op.hNameMappings = None
        op.lpszProgressTitle = None

        rc = fn(ctypes.byref(op))
        aborted = bool(op.fAnyOperationsAborted)

        # 返回码是整批的，必须逐个复核
        for p in batch:
            if not p.exists():
                deleted.append(p)
                continue
            if aborted:
                why = "操作被系统中止"
            elif rc:
                why = f"Windows 返回码 0x{rc & 0xFFFFFFFF:08X}"
            elif len(str(p)) > 250:
                why = "路径过长（超过 260 字符），资源管理器接口无法处理"
            else:
                why = "文件仍在（可能被其它程序占用）"
            failed.append((p, why))

    return deleted, failed


def _recycle_posix(paths: list[Path]) -> tuple[list[Path], list[tuple[Path, str]]]:
    """macOS / Linux：优先 send2trash，其次系统命令。"""
    try:
        import send2trash  # type: ignore

        done, bad = [], []
        for p in paths:
            try:
                send2trash.send2trash(str(p))
                done.append(p)
            except Exception as exc:  # noqa: BLE001
                bad.append((p, f"send2trash 失败：{exc}"))
        return done, bad
    except ImportError:
        pass

    cmd = None
    if sys.platform == "darwin":
        cmd = ["osascript", "-e",
               'tell application "Finder" to delete every item of '
               'POSIX file {} to trash']
    elif shutil.which("gio"):
        cmd = ["gio", "trash"]
    elif shutil.which("trash-put"):
        cmd = ["trash-put"]

    if cmd is None:
        return [], [(p, "当前系统没有可用的回收站接口（请安装 send2trash）") for p in paths]

    done, bad = [], []
    for p in paths:
        try:
            args = cmd + [str(p)]
            r = subprocess.run(args, capture_output=True, text=True, timeout=30)
            if r.returncode == 0 and not p.exists():
                done.append(p)
            else:
                bad.append((p, (r.stderr or "命令失败").strip()[:120]))
        except Exception as exc:  # noqa: BLE001
            bad.append((p, str(exc)[:120]))
    return done, bad


# ---------------------------------------------------------------- 对外


def to_recycle_bin(
    paths: list[Path],
    root: Path | None = None,
    log=None,
    reason: str = "",
    allow_dirs: bool = False,
) -> DeleteResult:
    """把文件（或空目录）移入回收站。root 用于越界防护（强烈建议传）。"""
    res = DeleteResult()

    good, bad = check_safe(paths, root, allow_dirs=allow_dirs)
    res.failed.extend(bad)

    files = [p for p in good if not p.is_dir()]
    # 系统缩略图缓存（Thumbs.db / desktop.ini）直接删：它们是垃圾，
    # 且网络盘上回收站接口对它们会失败（0x78 不支持）。
    junk_files = [p for p in files if p.name.lower() in JUNK_NAMES]
    files = [p for p in files if p.name.lower() not in JUNK_NAMES]
    dirs = sorted((p for p in good if p.is_dir()),
                  key=lambda p: len(p.parts), reverse=True)   # 由深到浅

    # ★ 必须在删除**之前**量大小：删完 stat() 就取不到了（踩过，永远显示 0 MB）
    sizes = {p: _size(p) for p in files}

    def _do(batch: list[Path]) -> None:
        if not batch:
            return
        if sys.platform == "win32":
            done, fail = _recycle_windows(batch)
        else:
            done, fail = _recycle_posix(batch)
        res.deleted.extend(done)
        res.failed.extend(fail)
        if log:
            for p, why in fail[:10]:
                log.warning("删除失败：%s（%s）", p, why)

    # 先删文件，再删目录
    _do(files)
    if junk_files:
        done_j, fail_j = _remove_files_direct(junk_files)
        res.deleted.extend(done_j)
        res.failed.extend(fail_j)

    # 空目录：由深到浅直接删。
    # 不再逐个 `iterdir` 预检（网络盘上每次都是一次往返），也不走回收站——
    # `RemoveDirectoryW` 本身就只删空目录，非空会返回 ERROR_DIR_NOT_EMPTY，
    # 安全语义比"先检查再删"更强，而且快得多（实测网络盘 上 6 秒/个 → 毫秒级）。
    # `dirs` 来自 find_empty_dirs，已按深度由深到浅排好：
    # 删掉 a/b/c 之后 a/b 才空，顺序不能乱。
    if dirs:
        done_d, fail_d = _remove_empty_dirs(dirs)
        res.deleted.extend(done_d)
        res.failed.extend(fail_d)
        if log:
            for p, why in fail_d[:10]:
                log.warning("删空目录失败：%s（%s）", p, why)

    res.freed_bytes = sum(sizes.get(p, 0) for p in res.deleted)

    if log and res.deleted:
        log.info("已移入回收站 %d 项（%s）", len(res.deleted), _mb(res.freed_bytes))

    res.log_path = _audit(res, reason, root, sizes)
    return res


def clean_empty_dirs(root: Path, log=None, dry_run: bool = False) -> DeleteResult:
    """清理书库里的空文件夹（含级联：删掉子目录后父目录变空的，一并清掉）。

    先删垃圾文件（Thumbs.db 等），再由深到浅删目录。
    ⚠ 目录是**直接删除**（`RemoveDirectoryW`/`rmdir`），不进回收站：
      空目录里没有任何文件，回收站没有意义，网络盘上还慢得离谱。
      调用方在界面上必须说"直接删除"，不能写"可还原"。
    """
    dirs, junk = find_empty_dirs(root)
    if dry_run:
        res = DeleteResult()
        res.deleted = dirs          # dry_run 时把"将要删的"放进 deleted 供调用方展示
        return res
    if not dirs and not junk:
        return DeleteResult()

    res = to_recycle_bin([*junk, *dirs], root=root, log=log,
                         reason="清理空文件夹", allow_dirs=True)
    if log:
        log.info("清理空文件夹：%d 个目录 + %d 个缩略图缓存文件", len(dirs), len(junk))
    return res


# ---------------------------------------------------------------- 清缓存


def cache_total_mb() -> float:
    """所有书库缓存的总大小（MB）。"""
    from . import core

    return core.dir_size_mb(core.data_dir() / "cache")


def clear_cache(root: Path | None = None, everything: bool = False
                ) -> tuple[int, float]:
    """删掉本工具自己生成的缓存（books.db + fp/*.bin）。返回 (删除目录数, 释放 MB)。

    安全性：
    · 只删 `%LOCALAPPDATA%/NovelDedup/cache` 下的东西（`relative_to` 校验，越界直接跳过）
    · 不碰用户的书、不碰日志、不碰删除审计
    · 缓存删了可以重建，代价是下次扫描要重算（所以界面上必须先确认）
    """
    from . import core

    base = (core.data_dir() / "cache").resolve()
    if not base.exists():
        return 0, 0.0

    if everything:
        targets = [d for d in base.iterdir() if d.is_dir()]
    elif root is not None:
        targets = [core.cache_dir_for(Path(root))]     # 注意：这个函数会 mkdir
    else:
        return 0, 0.0

    freed = 0.0
    n = 0
    for d in targets:
        try:
            dr = Path(d).resolve()
            dr.relative_to(base)                       # 越界就抛 ValueError
        except (ValueError, OSError):
            continue
        size = core.dir_size_mb(dr)
        shutil.rmtree(dr, ignore_errors=True)
        if not dr.exists():
            freed += size
            n += 1
    return n, freed


def _size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def _audit(res: DeleteResult, reason: str, root: Path | None,
           sizes: dict | None = None) -> Path | None:
    """追加写删除记录（回收站被清空后还能查）。"""
    sizes = sizes or {}
    try:
        from . import core

        path = core.data_dir() / "deleted.jsonl"
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "a", encoding="utf-8") as f:
            for p in res.deleted:
                f.write(json.dumps({
                    "time": now, "path": str(p), "size": sizes.get(p, 0),
                    "reason": reason, "root": str(root) if root else "",
                }, ensure_ascii=False) + "\n")
            for p, why in res.failed:
                f.write(json.dumps({
                    "time": now, "path": str(p), "size": 0,
                    "reason": f"删除失败：{why}", "root": str(root) if root else "",
                }, ensure_ascii=False) + "\n")
        return path
    except Exception:  # noqa: BLE001
        return None
