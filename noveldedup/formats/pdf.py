"""PDF：只有**带文本层**的能提，扫描版（图片）明确不支持。

依赖 `pypdf`（BSD-3）。没装就抛 UnsupportedFormat，由上层标成"跳过"并说明原因——
不要静默当成"没有重复"，那会让用户以为查过了。

为什么要做 CJK 占比自检：pypdf 对竖排/CID 字体的 PDF 经常"成功返回、内容全乱码"，
不检查就会把一堆乱码算成指纹，浪费算力还可能误判。
"""

from __future__ import annotations

from pathlib import Path

from ._base import Extracted, UnsupportedFormat


def available() -> bool:
    try:
        import pypdf  # noqa: F401

        return True
    except ImportError:
        return False


def extract(path: Path, raw: bytes | None = None) -> Extracted:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - 取决于环境
        raise UnsupportedFormat(
            "PDF 需要可选依赖 pypdf（BSD-3）：pip install pypdf") from exc

    src = str(path)
    try:
        reader = PdfReader(src)
    except Exception as exc:  # noqa: BLE001
        return Extracted("", fmt="pdf", note=f"打不开：{type(exc).__name__}")

    parts: list[str] = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 —— 单页失败不影响整本
            continue

    text = "\n".join(parts)
    if not text.strip():
        return Extracted("", fmt="pdf",
                         note="没有文本层（扫描版 PDF，需 OCR，本工具不做）")

    # 乱码自检交给上层（normalize.selfcheck 的常用字覆盖率），这里只报页数
    return Extracted(text=text, fmt="pdf", enc="", bad=text.count("\ufffd"),
                     note=f"{len(reader.pages)} 页")
