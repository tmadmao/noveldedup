"""DOCX：zipfile + xml 自己解（零依赖）。

`word/document.xml` 里 `<w:p>` 是段落、`<w:br>` 是换行、`<w:tab>` 是制表。
必须先把它们换成换行，再去标签——否则整本书会粘成一行，章节正则也匹配不到。
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

from ._base import Extracted, strip_markup, tidy_chapters

_MAIN = "word/document.xml"
_P_END = re.compile(r"</w:p\s*>", re.I)
_BR = re.compile(r"<w:br\b[^>]*/?>", re.I)
_TAB = re.compile(r"<w:tab\b[^>]*/?>", re.I)
_P_START = re.compile(r"<w:p\b[^>]*>", re.I)


def extract(path: Path, raw: bytes | None = None) -> Extracted:
    data = raw if raw is not None else path.read_bytes()
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return Extracted("", fmt="docx", note="不是有效 zip（docx 可能已损坏）")

    with z:
        names = set(z.namelist())
        if _MAIN not in names:
            # 少量 docx 用别的部件名；也有 .doc 被改名成 .docx 的情况
            return Extracted("", fmt="docx",
                             note="没有 word/document.xml（可能是改名后的 .doc）")
        xml = z.read(_MAIN).decode("utf-8", "replace")

    xml = _BR.sub("\n", xml)
    xml = _TAB.sub("\t", xml)
    xml = _P_START.sub("\n", xml)
    xml = _P_END.sub("", xml)

    text = tidy_chapters(strip_markup(xml))
    if not text.strip():
        return Extracted("", fmt="docx", note="文档里没有文字（可能全是图片）")
    return Extracted(text=text, fmt="docx", enc="utf-8", bad=text.count("\ufffd"))
