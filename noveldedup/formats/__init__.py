"""格式注册表：扩展名 + 魔数分派。

两种判据都看，因为实际书库里两种情况都常见：
- 扩展名错（`.txt` 里其实是 HTML、`.docx` 其实是改名的 `.doc`）
- 扩展名缺（网盘下载回来没有后缀）

**不支持的格式必须报出来**，不能静默跳过——
静默跳过会让用户以为"查过了没有重复"，比报错危险得多。
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from . import docx as _docx
from . import epub as _epub
from . import html as _html
from . import pdf as _pdf
from . import txt as _txt
from ._base import Extracted, UnsupportedFormat

EXTS: tuple[str, ...] = (".txt", ".epub", ".html", ".htm", ".xhtml", ".docx", ".pdf")

_IMPL = {
    "txt": _txt.extract,
    "html": _html.extract,
    "epub": _epub.extract,
    "docx": _docx.extract,
    "pdf": _pdf.extract,
}


def supported_exts() -> tuple[str, ...]:
    return EXTS


def pdf_available() -> bool:
    return _pdf.available()


def detect_format(path: Path, raw: bytes) -> str | None:
    """返回 txt/html/epub/docx/pdf，认不出返回 None。"""
    head = raw[:2048]
    ext = path.suffix.lower()

    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                names = set(z.namelist())
                if "mimetype" in names:
                    try:
                        if b"epub" in z.read("mimetype"):
                            return "epub"
                    except (KeyError, OSError):
                        pass
                if "word/document.xml" in names:
                    return "docx"
                if "META-INF/container.xml" in names:
                    return "epub"
        except (zipfile.BadZipFile, OSError):
            pass
        return None

    if ext in (".html", ".htm", ".xhtml"):
        return "html"
    low = head.lstrip().lower()
    if low.startswith(b"<!doctype html") or low.startswith(b"<html"):
        return "html"          # 扩展名是 .txt 的网页另存
    if ext == ".txt":
        return "txt"
    if ext == ".pdf":
        return "pdf"
    return None


def extract(path: Path, raw: bytes | None = None) -> Extracted:
    """提取纯文本。认不出格式或缺少可选依赖 → 抛 UnsupportedFormat。"""
    raw = raw if raw is not None else path.read_bytes()
    fmt = detect_format(path, raw)
    if fmt is None:
        raise UnsupportedFormat(f"不认识的格式：{path.suffix or '无扩展名'}")
    if fmt == "pdf" and not _pdf.available():
        raise UnsupportedFormat("PDF 需要可选依赖 pypdf（BSD-3）：pip install pypdf")
    return _IMPL[fmt](path, raw)


__all__ = ["EXTS", "Extracted", "UnsupportedFormat", "detect_format",
           "extract", "pdf_available", "supported_exts"]
