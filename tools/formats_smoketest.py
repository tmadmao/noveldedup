"""格式层测试：同一本书做成 txt / html / epub / docx，必须判为同一本。

同时验证：
- 扩展名错（.txt 里其实是 HTML）也能认出来
- 拿不到的格式（pdf 没装 pypdf）要**明确报出来**，不能静默跳过

跑：py -3 tools/formats_smoketest.py
"""

from __future__ import annotations

import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from noveldedup import engine, formats, selftest   # noqa: E402

ok_all = True


def check(label: str, cond: bool, extra: str = "") -> None:
    global ok_all
    ok_all &= bool(cond)
    print(f"  {'✅' if cond else '❌'} {label}" + (f"   {extra}" if extra else ""))


def make_html(body: str) -> str:
    paras = "\n".join(f"<p>{ln}</p>" for ln in body.splitlines() if ln.strip())
    return ('<!DOCTYPE html><html><head><meta charset="utf-8">'
            "<title>测试书</title><style>p{margin:0}</style></head>"
            f"<body><div class='content'>{paras}</div></body></html>")


def make_epub(book: str) -> bytes:
    paras = "\n".join(f"<p>{ln}</p>" for ln in book.splitlines() if ln.strip())
    xhtml = ('<?xml version="1.0" encoding="utf-8"?>'
             '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>测试书</title></head>'
             f"<body>{paras}</body></html>")
    opf = ('<?xml version="1.0" encoding="utf-8"?>'
           '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">'
           "<metadata><dc:title xmlns:dc=\"http://purl.org/dc/elements/1.1/\">测试书</dc:title>"
           "</metadata><manifest>"
           '<item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/>'
           "</manifest><spine><itemref idref=\"c1\"/></spine></package>")
    container = ('<?xml version="1.0"?>'
                 '<container version="1.0" '
                 'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                 '<rootfile full-path="OEBPS/content.opf" '
                 'media-type="application/oebps-package+xml"/></rootfiles></container>')
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/epub+zip", zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", container)
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/ch1.xhtml", xhtml)
    return buf.getvalue()


def make_docx(book: str) -> bytes:
    import io
    from xml.sax.saxutils import escape

    paras = "".join(f"<w:p><w:r><w:t>{escape(ln)}</w:t></w:r></w:p>"
                    for ln in book.splitlines() if ln.strip())
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f"<w:body>{paras}</w:body></w:document>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
                   'package/2006/content-types"><Default Extension="xml" '
                   'ContentType="application/xml"/></Types>')
        z.writestr("word/document.xml", doc)
    return buf.getvalue()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="nd_fmt_"))
    book = selftest.make_book(60_000, 3)
    print(f"临时目录：{tmp}\n正文 {len(book):,} 字，{book.count('第')} 个章节标题\n")

    (tmp / "甲.txt").write_bytes(book.encode("gb18030"))
    (tmp / "乙.html").write_bytes(make_html(book).encode("utf-8"))
    (tmp / "丙.epub").write_bytes(make_epub(book))
    (tmp / "丁.docx").write_bytes(make_docx(book))
    # 扩展名骗人：其实是 HTML
    (tmp / "戊.txt").write_bytes(make_html(book).encode("utf-8"))
    # 假 PDF（没有 pypdf 时应报"不支持"，装了则应报"打不开"）
    # 补到 2KB：小于 min_bytes(1KB) 的文件根本不进扫描
    (tmp / "己.pdf").write_bytes(
        b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n" + b"%" * 2048)
    # 短文本（计划 §7.1 要求标注"判定可能不稳"）
    short_book = "".join(
        f"第{i}章 短篇\n" + "他走进屋子，看见桌上放着一封没有署名的信。" * 8 + "\n"
        for i in range(1, 8))
    (tmp / "庚短篇.txt").write_bytes(short_book.encode("gb18030"))

    print("[1] 格式识别")
    for name, want in (("甲.txt", "txt"), ("乙.html", "html"), ("丙.epub", "epub"),
                       ("丁.docx", "docx"), ("戊.txt", "html"), ("己.pdf", "pdf")):
        p = tmp / name
        got = formats.detect_format(p, p.read_bytes())
        check(f"{name} → {got}", got == want, f"期望 {want}")

    print("\n[2] 逐格式提取字数")
    for name in ("甲.txt", "乙.html", "丙.epub", "丁.docx", "戊.txt"):
        item = formats.extract(tmp / name)
        check(f"{name} 提取得出正文", len(item.text) > 1000,
              f"fmt={item.fmt} enc={item.enc} {len(item.text):,} 字 {item.note}")

    print("\n[3] 端到端判重（4 种格式应判为同一本）")
    res = engine.scan(tmp, preset="标准", workers=1, with_evidence=True)
    names = {b.name: b for b in res.books.values()}
    for n, b in sorted(names.items()):
        print(f"     {n:<10} fmt={b.fmt:<5} status={b.status:<8} {b.norm_chars:>7,} 汉字")
    check("扫到 7 个文件", len(res.books) == 7, f"实际 {len(res.books)}")

    groups = [g for g in res.groups if len(g.members) >= 4]
    check("4 种格式进了同一个组", bool(groups), f"{len(res.groups)} 组")
    if groups:
        g = groups[0]
        members = sorted(res.books[m].name for m in g.members if m in res.books)
        print(f"     组内：{members}")
        check("同组含 5 个文件（含伪装成 txt 的 html）", len(members) == 5, str(members))
        check("关系为同一版本",
              all(p.rel.startswith("同一版本") for p in g.pairs),
              "；".join(sorted({p.rel for p in g.pairs})))

    print("\n[4] 不支持的格式要明确报出来")
    pdf = names.get("己.pdf")
    if pdf:
        check("pdf 被标记为需要关注", not pdf.ok, f"status={pdf.status} note={pdf.note}")
        check("说明里讲清楚了原因",
              ("pypdf" in pdf.note) or ("打不开" in pdf.note) or ("文本层" in pdf.note),
              pdf.note)
    if not formats.pdf_available():
        print("     （当前环境未安装 pypdf，PDF 按计划标为「需要可选依赖」）")

    print("\n[5] 短文本标注")
    short = names.get("庚短篇.txt")
    if short:
        check("短篇被标为短文本", short.short, f"note={short.note}")
        check("说明里给了字数", "短文本" in short.note, short.note)

    print("\n" + ("格式层测试：通过" if ok_all else "格式层测试：失败"))
    print(f"（临时目录：{tmp}）")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
