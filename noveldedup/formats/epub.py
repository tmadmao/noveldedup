"""EPUB：zipfile + xml 自己解（**不用 EbookLib，它是 AGPL-3.0**）。

顺序很重要：按 OPF 的 `spine` 读，才是"人读书的顺序"；
直接按 zip 里的文件名顺序读，章节会乱（部分 epub 的 xhtml 文件名不按章序）。
"""

from __future__ import annotations

import io
import posixpath
import re
import zipfile
from pathlib import Path
from urllib.parse import unquote

from ._base import Extracted, decode_html, strip_markup, tidy_chapters

_CONTAINER = "META-INF/container.xml"

_ROOTFILE = re.compile(rb"<(?:\w+:)?rootfile\b[^>]*full-path\s*=\s*[\"']([^\"']+)[\"']", re.I)
_ITEM = re.compile(r"<(?:\w+:)?item\b([^>]*?)/?>", re.I)
_ITEMREF = re.compile(r"<(?:\w+:)?itemref\b([^>]*?)/?>", re.I)
_HREF = re.compile(r"href\s*=\s*[\"']([^\"']+)[\"']", re.I)
_ID = re.compile(r"\bid\s*=\s*[\"']([^\"']+)[\"']", re.I)
_IDREF = re.compile(r"idref\s*=\s*[\"']([^\"']+)[\"']", re.I)


def _opf_path(z: zipfile.ZipFile, names: list[str]) -> str | None:
    if _CONTAINER in names:
        try:
            m = _ROOTFILE.search(z.read(_CONTAINER))
            if m:
                return m.group(1).decode("utf-8", "replace")
        except (KeyError, OSError):
            pass
    for n in names:                      # 兜底：随便找一个 .opf
        if n.lower().endswith(".opf"):
            return n
    return None


def _spine(z: zipfile.ZipFile, opf: str) -> list[str]:
    """OPF 的 spine 顺序 → zip 内路径列表。"""
    try:
        xml = z.read(opf).decode("utf-8", "replace")
    except (KeyError, OSError):
        return []

    base = posixpath.dirname(opf)
    manifest: dict[str, str] = {}
    for tag in _ITEM.findall(xml):
        m_h, m_i = _HREF.search(tag), _ID.search(tag)
        if m_h and m_i:
            manifest[m_i.group(1)] = m_h.group(1)

    out: list[str] = []
    for tag in _ITEMREF.findall(xml):
        m = _IDREF.search(tag)
        if not m:
            continue
        href = manifest.get(m.group(1))
        if not href:
            continue
        path = posixpath.normpath(posixpath.join(base, unquote(href)))
        out.append(path)
    return out


def extract(path: Path, raw: bytes | None = None) -> Extracted:
    data = raw if raw is not None else path.read_bytes()
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return Extracted("", fmt="epub", note="不是有效 zip（epub 可能已损坏）")

    with z:
        names = z.namelist()
        opf = _opf_path(z, names)
        order = _spine(z, opf) if opf else []
        if not order:
            order = [n for n in names if n.lower().endswith((".xhtml", ".html", ".htm"))]

        parts: list[str] = []
        enc_seen = ""
        for name in order:
            try:
                raw_html = z.read(name)
            except (KeyError, OSError):
                continue
            text, enc = decode_html(raw_html)
            enc_seen = enc_seen or enc
            body = strip_markup(text)
            if body:
                parts.append(body)

    if not parts:
        return Extracted("", fmt="epub", note="压缩包里没有可读正文（可能是纯图片版）")

    text = tidy_chapters("\n\n".join(parts))
    return Extracted(text=text, fmt="epub", enc=enc_seen, bad=text.count("\ufffd"))
