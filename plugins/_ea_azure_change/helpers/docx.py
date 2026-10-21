"""Minimal .docx writer: zipfile + WordprocessingML. No python-docx."""

from __future__ import annotations

import io
import zipfile
from typing import Any, Iterable
from xml.sax.saxutils import escape as _escape

from plugins._ea_azure_change.helpers.pdf import plain

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>
"""

_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""

_DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>
"""

_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:styleId="Normal" w:default="1"><w:name w:val="Normal"/>
    <w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="20"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>
    <w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:outlineLvl w:val="0"/></w:pPr>
    <w:rPr><w:b/><w:sz w:val="32"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/>
    <w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:outlineLvl w:val="1"/></w:pPr>
    <w:rPr><w:b/><w:sz w:val="26"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/>
    <w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:outlineLvl w:val="2"/></w:pPr>
    <w:rPr><w:b/><w:sz w:val="22"/></w:rPr></w:style>
</w:styles>
"""

_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def escape(text: str) -> str:
    return _escape(plain(text))  # same words as the PDF


def _p(text: str, style: str | None = None) -> str:
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{ppr}<w:r><w:t xml:space=\"preserve\">{escape(text)}</w:t></w:r></w:p>"


def _mono(text: str) -> str:
    runs = []
    for line in (text or "").splitlines() or [""]:
        runs.append(
            "<w:p><w:r>"
            '<w:rPr><w:rFonts w:ascii="Courier New" w:hAnsi="Courier New"/><w:sz w:val="18"/></w:rPr>'
            f'<w:t xml:space="preserve">{escape(line)}</w:t></w:r></w:p>'
        )
    return "".join(runs)


def _cell(text: str, bold: bool = False, width: int = 2000) -> str:
    rpr = "<w:rPr><w:b/></w:rPr>" if bold else ""
    paras = "".join(
        f'<w:p><w:r>{rpr}<w:t xml:space="preserve">{escape(line)}</w:t></w:r></w:p>' for line in (text or "").split("\n")
    )
    return f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/></w:tcPr>{paras}</w:tc>'


def _table(headers: list[str], rows: list[list[str]], widths: list[float], bold_cols: set[int]) -> str:
    if not any(h.strip() for h in headers):
        headers = []  # a table without a header row prints none
    cols = max(len(headers), max((len(r) for r in rows), default=0))
    if cols == 0:
        return ""
    weights = (list(widths) + [1.0] * cols)[:cols]
    dxa = [int(9026 * w / sum(weights)) for w in weights]
    body = ["<w:tbl><w:tblPr><w:tblW w:w=\"5000\" w:type=\"pct\"/>"
            "<w:tblBorders>"
            '<w:top w:val="single" w:sz="4" w:color="000000"/>'
            '<w:left w:val="single" w:sz="4" w:color="000000"/>'
            '<w:bottom w:val="single" w:sz="4" w:color="000000"/>'
            '<w:right w:val="single" w:sz="4" w:color="000000"/>'
            '<w:insideH w:val="single" w:sz="4" w:color="000000"/>'
            '<w:insideV w:val="single" w:sz="4" w:color="000000"/>'
            "</w:tblBorders></w:tblPr>"]
    if headers:
        cells = list(headers) + [""] * (cols - len(headers))
        body.append("<w:tr><w:trPr><w:tblHeader/></w:trPr>" + "".join(_cell(h, True, dxa[i]) for i, h in enumerate(cells)) + "</w:tr>")
    for row in rows:
        cells = list(row) + [""] * (cols - len(row))
        body.append("<w:tr><w:trPr><w:cantSplit/></w:trPr>"
                    + "".join(_cell(str(c), i in bold_cols, dxa[i]) for i, c in enumerate(cells)) + "</w:tr>")
    body.append("</w:tbl>")
    return "".join(body)


def render(blocks: Iterable[dict[str, Any]]) -> bytes:
    """Return a minimal valid .docx for the given document blocks."""
    parts: list[str] = []
    for block in blocks:
        kind = block.get("type") or block.get("kind") or ""
        if kind == "heading":
            level = int(block.get("level") or 1)
            style = {1: "Heading1", 2: "Heading2", 3: "Heading3"}.get(level, "Heading3")
            parts.append(_p(str(block.get("text") or ""), style))
        elif kind == "para":
            parts.append(_p(str(block.get("text") or "")))
        elif kind == "mono":
            parts.append(_mono(str(block.get("text") or "")))
        elif kind == "rule":
            parts.append('<w:p><w:pPr><w:pBdr><w:bottom w:val="single" w:sz="6" w:space="1" w:color="000000"/></w:pBdr></w:pPr></w:p>')
        elif kind == "table":
            headers = [str(h) for h in (block.get("headers") or [])]
            rows = [[str(c) for c in row] for row in (block.get("rows") or [])]
            parts.append(_table(headers, rows, [float(w) for w in (block.get("widths") or [])], set(block.get("label_cols") or [])))
        else:
            text = str(block.get("text") or "")
            if text:
                parts.append(_p(text))

    document = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document {_NS}><w:body>'
        + "".join(parts)
        + "<w:sectPr><w:pgSz w:w=\"11906\" w:h=\"16838\"/>"
        "<w:pgMar w:top=\"1440\" w:right=\"1440\" w:bottom=\"1440\" w:left=\"1440\"/></w:sectPr>"
        "</w:body></w:document>"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES)
        zf.writestr("_rels/.rels", _RELS)
        zf.writestr("word/_rels/document.xml.rels", _DOC_RELS)
        zf.writestr("word/styles.xml", _STYLES)
        zf.writestr("word/document.xml", document)
    return buf.getvalue()
