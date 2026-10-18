"""纯文本 txt：编码探测是这一步的全部内容。

GB18030 几乎能解任何字节流，所以必须用**常用字覆盖率**复核
（实测：UTF-8 文件被错解成 GB18030 时 CJK 占比只掉 0.15%，常用字覆盖率从 78% 崩到 2.2%）。
"""

from __future__ import annotations

from pathlib import Path

from ..normalize import detect_encoding
from ._base import Extracted


def extract(path: Path, raw: bytes | None = None) -> Extracted:
    raw = raw if raw is not None else path.read_bytes()
    enc, note = detect_encoding(raw)
    text = raw.decode(enc, errors="replace")
    return Extracted(text=text, fmt="txt", enc=enc, bad=text.count("\ufffd"), note=note)
