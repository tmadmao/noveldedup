"""格式提取的公共类型与小工具。

许可证红线（计划 §五）
----------------------
| 禁用 | 许可证 | 这里怎么做 |
| --- | --- | --- |
| EbookLib | AGPL-3.0 | 标准库 zipfile + xml.etree 自己解 epub |
| mobi | GPL-3.0 | 不支持 MOBI |
| PyMuPDF | AGPL-3.0 | pypdf（BSD-3，可选） |

所以 epub / docx 全部是标准库实现，**不引入任何第三方依赖**。
"""

from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass


@dataclass
class Extracted:
    """一次格式提取的结果。"""

    text: str
    fmt: str = "txt"
    enc: str = ""        # 文本类格式才有意义
    bad: int = 0         # 替换字符数（解码失败程度的信号）
    note: str = ""       # "无文本层" / "压缩包里没有正文" 等提示

    @property
    def ok(self) -> bool:
        return bool(self.text.strip())


class UnsupportedFormat(Exception):
    """格式不认识，或缺少可选依赖（例如没装 pypdf 却给了 pdf）。"""


# ---------------------------------------------------------------- 去标签

_SCRIPT = re.compile(r"<(script|style|head)\b[^>]*>.*?</\1\s*>", re.I | re.S)
_BLOCK = re.compile(r"<\s*(?:br|/p|/div|/h[1-6]|/li|/tr|/section|/article)\s*/?\s*>", re.I)
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_TAG = re.compile(r"<[^>]{0,400}>")
_BLANK = re.compile(r"[ \t\u3000]+\n")
_MANY_NL = re.compile(r"\n{3,}")


def strip_markup(markup: str) -> str:
    """HTML/XHTML → 纯文本。

    块级结束标签转换行（否则整本书会粘成一行，章节正则也匹配不到），
    再去标签、反转义实体、压掉多余空行。
    """
    s = _COMMENT.sub("", markup)
    s = _SCRIPT.sub("", s)
    s = _BLOCK.sub("\n", s)
    s = _TAG.sub("", s)
    s = _html.unescape(s)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = _BLANK.sub("\n", s)
    s = _MANY_NL.sub("\n\n", s)
    return s.strip()


_CHARSET = re.compile(rb"""charset\s*=\s*["']?\s*([A-Za-z0-9_\-]+)""", re.I)


def decode_html(raw: bytes) -> tuple[str, str]:
    """HTML 解码：优先按 <meta charset>，否则走项目的编码探测。"""
    from ..normalize import detect_encoding

    m = _CHARSET.search(raw[:4096])
    if m:
        enc = m.group(1).decode("ascii", "replace").lower()
        alias = {"gbk": "gb18030", "gb2312": "gb18030", "utf8": "utf-8"}
        enc = alias.get(enc, enc)
        try:
            return raw.decode(enc, errors="replace"), enc
        except LookupError:
            pass
    enc, _note = detect_encoding(raw)
    return raw.decode(enc, errors="replace"), enc


def tidy_chapters(text: str) -> str:
    """把"一行里挤了很多章"的文本拆开（部分 epub/html 转出来的通病）。"""
    return re.sub(r"(?<=[。！？”』])\s*(?=第[0-9零〇一二三四五六七八九十百千两]{1,7}[章节回])",
                  "\n", text)
