"""HTML / XHTML / 网页另存为的 txt：去标签。

小说站"另存为"得到的文件常带一堆导航/广告条，去标签后交给归一化，
广告行由 normalize 的 AD_LINE_RE 处理。
"""

from __future__ import annotations

from pathlib import Path

from ._base import Extracted, decode_html, strip_markup, tidy_chapters


def extract(path: Path, raw: bytes | None = None) -> Extracted:
    raw = raw if raw is not None else path.read_bytes()
    text, enc = decode_html(raw)
    cleaned = tidy_chapters(strip_markup(text))
    return Extracted(text=cleaned, fmt="html", enc=enc,
                     bad=cleaned.count("\ufffd"),
                     note="无正文（可能整页都是脚本/样式）" if not cleaned.strip() else "")
