"""Minimal PDF 1.4 writer: Helvetica, wrap, headings, rules, tables, page numbers.

No images, no font files, no third-party layout engine. Built-in Helvetica only.
# ponytail: approximate AFM widths; swap for embedded AFM if non-Latin text matters.
"""

from __future__ import annotations

import unicodedata
from typing import Any, Iterable

# A4 in points (1/72 inch)
PAGE_W = 595.28
PAGE_H = 841.89
MARGIN_L = 56.0
MARGIN_R = 56.0
MARGIN_T = 56.0
MARGIN_B = 64.0  # room for footer
CONTENT_W = PAGE_W - MARGIN_L - MARGIN_R

# Helvetica AFM widths for WinAnsi printable ASCII (units/1000). Missing → 556.
_W: dict[int, int] = {
    32: 278, 33: 278, 34: 355, 35: 556, 36: 556, 37: 889, 38: 667, 39: 191,
    40: 333, 41: 333, 42: 389, 43: 584, 44: 278, 45: 333, 46: 278, 47: 278,
    48: 556, 49: 556, 50: 556, 51: 556, 52: 556, 53: 556, 54: 556, 55: 556,
    56: 556, 57: 556, 58: 278, 59: 278, 60: 584, 61: 584, 62: 584, 63: 556,
    64: 1015, 65: 667, 66: 667, 67: 722, 68: 722, 69: 667, 70: 611, 71: 778,
    72: 722, 73: 278, 74: 500, 75: 667, 76: 556, 77: 833, 78: 722, 79: 778,
    80: 667, 81: 778, 82: 722, 83: 667, 84: 611, 85: 722, 86: 667, 87: 944,
    88: 667, 89: 667, 90: 611, 91: 278, 92: 278, 93: 278, 94: 469, 95: 556,
    96: 333, 97: 556, 98: 556, 99: 500, 100: 556, 101: 556, 102: 278, 103: 556,
    104: 556, 105: 222, 106: 222, 107: 500, 108: 222, 109: 833, 110: 556, 111: 556,
    112: 556, 113: 556, 114: 333, 115: 500, 116: 278, 117: 556, 118: 500, 119: 722,
    120: 500, 121: 500, 122: 500, 123: 334, 124: 260, 125: 334, 126: 584,
}


# The one translation table: characters outside Helvetica's WinAnsi set become plain ASCII words, so nothing prints as "?".
# Latin-1 letters and the middle dot are WinAnsi already and pass through. Applied to every string the writer prints or
# measures, and by docx.py, so the Word copy shows the same words.
_PLAIN = {
    "\u2248": "~", "\u2013": "-", "\u2014": "-", "\u2212": "-", "\u2010": "-", "\u2011": "-", "\u2500": "-",
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201c": '"', "\u201d": '"', "\u201e": '"',
    "\u2026": "...", "\u2022": "-", "\u2192": "->", "\u2190": "<-", "\u2264": "<=", "\u2265": ">=",
    "\u00d7": "x", "\u20ac": "EUR", "\u2122": "(TM)", "\u2713": "yes", "\u2717": "no",
    "\u202f": " ", "\u2009": " ", "\u2002": " ", "\u2003": " ", "\u200b": "", "\ufeff": "",
}


def plain(text: str) -> str:
    out = []
    for ch in str(text):
        if ord(ch) < 256:
            out.append(ch)
        elif ch in _PLAIN:
            out.append(_PLAIN[ch])
        else:  # accents stripped when that leaves ASCII, otherwise an honest "?"
            base = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode()
            out.append(base or "?")
    return "".join(out)


def _width(text: str, size: float, mono: bool = False) -> float:
    if mono:  # Courier: every glyph is 600 units
        return len(plain(text)) * 600 * size / 1000.0
    return sum(_W.get(ord(ch), 556) for ch in plain(text)) * size / 1000.0


def _escape(text: str) -> str:
    # PDF literal string with WinAnsi-ish: keep ASCII; drop/replace control.
    out = []
    for ch in plain(text).replace("\r", "").replace("\t", " "):
        o = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == "(":
            out.append("\\(")
        elif ch == ")":
            out.append("\\)")
        elif 32 <= o <= 126:
            out.append(ch)
        elif ch in "\n":
            out.append(" ")
        else:
            # Keep common Latin-1 as octal so non-ASCII titles survive.
            out.append(f"\\{o:03o}")
    return "".join(out)


def _wrap(text: str, size: float, max_w: float) -> list[str]:
    """Wrap at spaces (hard-break an overlong token). Line breaks and leading indent in ``text`` are kept."""
    lines: list[str] = []
    for raw in plain(text or "").replace("\r", "").split("\n"):
        ind = " " * (len(raw) - len(raw.lstrip(" ")))
        words = raw.split()
        if not words:
            lines.append("")
            continue
        cur = ind + words[0]
        for word in words[1:]:
            trial = f"{cur} {word}"
            if _width(trial, size) <= max_w:
                cur = trial
            else:
                lines.append(cur)
                cur = ind + word
        lines.append(cur)
    fixed: list[str] = []
    for line in lines:
        if _width(line, size) <= max_w:
            fixed.append(line)
            continue
        buf = ""
        for ch in line:
            if buf and _width(buf + ch, size) > max_w:
                fixed.append(buf)
                buf = ch
            else:
                buf += ch
        if buf:
            fixed.append(buf)
    return fixed or [""]


def _wrap_mono(raw: str, n: int) -> list[str]:
    """Wrap a Courier line to ``n`` characters: at a space where possible, hard-wrapped otherwise, indent kept."""
    raw = plain(raw).replace("\t", "    ")
    ind = min(len(raw) - len(raw.lstrip(" ")), n // 2)
    out: list[str] = []
    while len(raw) > n:
        cut = raw.rfind(" ", ind + 1, n + 1)
        if cut > ind:
            out.append(raw[:cut].rstrip())
            rest = raw[cut:].lstrip(" ")
        else:
            out.append(raw[:n])
            rest = raw[n:]
        raw = " " * ind + rest
    out.append(raw)
    return out


class _Page:
    def __init__(self) -> None:
        self.ops: list[str] = []

    def text(self, x: float, y: float, size: float, font: str, s: str) -> None:
        self.ops.append(
            f"BT /{font} {size:.2f} Tf 1 0 0 1 {x:.2f} {y:.2f} Tm ({_escape(s)}) Tj ET"
        )

    def line(self, x1: float, y1: float, x2: float, y2: float, w: float = 0.6) -> None:
        self.ops.append(f"{w:.2f} w {x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S")

    def rect(self, x: float, y: float, w: float, h: float, stroke: bool = True) -> None:
        op = "S" if stroke else "f"
        self.ops.append(f"0.6 w {x:.2f} {y:.2f} {w:.2f} {h:.2f} re {op}")


def render(
    blocks: Iterable[dict[str, Any]],
    *,
    footer_left: str = "",
    generated: str = "",
) -> bytes:
    """Lay out blocks on A4 and return a PDF 1.4 byte string."""
    pages: list[_Page] = []
    page = _Page()
    y = PAGE_H - MARGIN_T

    def new_page() -> None:
        nonlocal page, y
        pages.append(page)
        page = _Page()
        y = PAGE_H - MARGIN_T

    def ensure(need: float) -> None:
        nonlocal y
        if y - need < MARGIN_B:
            new_page()

    def draw_para(text: str, size: float, font: str, leading: float, indent: float = 0.0) -> None:
        nonlocal y
        for line in _wrap(text, size, CONTENT_W - indent):
            ensure(leading)
            page.text(MARGIN_L + indent, y - size, size, font, line)
            y -= leading

    blocks = list(blocks)
    mono_n = int(CONTENT_W // (9.0 * 0.6))
    TS, PAD = 9.0, 3.0  # table font size and cell padding

    def table_parts(block: dict[str, Any]) -> tuple[list[str], list[list[str]], list[float]]:
        headers = [str(h) for h in (block.get("headers") or [])]
        if not any(h.strip() for h in headers):
            headers = []  # a table without a header row prints none
        rows = [[str(c) for c in row] for row in (block.get("rows") or [])]
        cols = max(len(headers), max((len(r) for r in rows), default=0))
        weights = [float(w) for w in (block.get("widths") or [])][:cols]
        weights += [1.0] * (cols - len(weights))
        return headers, rows, [CONTENT_W * w / sum(weights) for w in weights] if cols else []

    def row_h(cells: list[str], widths: list[float]) -> float:
        n = max((len(_wrap(c, TS, w - 2 * PAD)) for c, w in zip(cells, widths)), default=1)
        return n * (TS + 2) + 2 * PAD

    def lead_height(block: dict[str, Any] | None) -> float:
        """Room a heading needs under it: the first two lines, or the first table row, of the next block."""
        if block is None:
            return 0.0
        kind = block.get("type") or block.get("kind") or ""
        text = str(block.get("text") or "")
        if kind == "para":
            return min(2, len(_wrap(text, 10.0, CONTENT_W))) * 13.0 + 2
        if kind == "mono":
            return min(2, len(text.splitlines()) or 1) * 12.0 + 2
        if kind == "table":
            headers, rows, widths = table_parts(block)
            first = [headers] if headers else []
            first += rows[:1]
            return 4 + sum(row_h(r + [""] * (len(widths) - len(r)), widths) for r in first)
        if kind == "heading":
            return 8.0 + 4 + {1: 16.0, 2: 13.0}.get(int(block.get("level") or 1), 11.0)
        return 10.0

    for idx, block in enumerate(blocks):
        kind = block.get("type") or block.get("kind") or ""
        if kind == "heading":
            level = int(block.get("level") or 1)
            size = {1: 16.0, 2: 13.0, 3: 11.0}.get(level, 11.0)
            font = "F2" if level <= 2 else "F1"
            leading = size + 4
            gap = 10.0 if level == 1 else 8.0
            nxt = blocks[idx + 1] if idx + 1 < len(blocks) else None
            ensure(gap + leading + lead_height(nxt))  # keep with what follows
            y -= gap
            draw_para(str(block.get("text") or ""), size, font, leading)
        elif kind == "para":
            ensure(4)
            y -= 2
            draw_para(str(block.get("text") or ""), 10.0, "F1", 13.0)
        elif kind == "mono":
            ensure(4)
            y -= 2
            for raw in str(block.get("text") or "").splitlines() or [""]:
                for line in _wrap_mono(raw, mono_n):
                    ensure(12)
                    page.text(MARGIN_L, y - 9.0, 9.0, "F3", line)
                    y -= 12.0
        elif kind == "rule":
            ensure(10)
            y -= 4
            page.line(MARGIN_L, y, MARGIN_L + CONTENT_W, y)
            y -= 6
        elif kind == "table":
            headers, rows, widths = table_parts(block)
            cols = len(widths)
            if cols == 0:
                continue
            bold_cols = set(block.get("label_cols") or [])

            def draw_row(cells: list[str], head: bool) -> None:
                nonlocal y
                cells = list(cells) + [""] * (cols - len(cells))
                h = row_h(cells, widths)
                x = MARGIN_L
                page.rect(MARGIN_L, y - h, CONTENT_W, h)
                for i in range(cols):
                    if i:
                        page.line(x, y, x, y - h)
                    font = "F2" if head or i in bold_cols else "F1"
                    cy = y - PAD - TS
                    for ln in _wrap(cells[i], TS, widths[i] - 2 * PAD):
                        page.text(x + PAD, cy, TS, font, ln)
                        cy -= TS + 2
                    x += widths[i]
                y -= h

            # Header (if any) + body; on a page break the header is repeated.
            first = [headers] if headers else []
            first += rows[:1]
            if y - sum(row_h(r + [""] * (cols - len(r)), widths) for r in first) < MARGIN_B:
                new_page()
            y -= 4
            if headers:
                draw_row(headers, True)
            for row in rows:
                if y - row_h(row + [""] * (cols - len(row)), widths) < MARGIN_B:
                    new_page()
                    if headers:
                        draw_row(headers, True)
                draw_row(row, False)
        else:
            # Unknown block: show as paragraph so nothing is silently dropped.
            text = str(block.get("text") or "")
            if text:
                draw_para(text, 10.0, "F1", 13.0)

    pages.append(page)
    total = len(pages)

    # Footers: document number · short digest | Page n of m | generated
    for i, p in enumerate(pages, start=1):
        foot_y = 36.0
        left = footer_left
        mid = f"Page {i} of {total}"
        right = generated
        p.text(MARGIN_L, foot_y, 8.0, "F1", left)
        mid_w = _width(mid, 8.0)
        p.text((PAGE_W - mid_w) / 2, foot_y, 8.0, "F1", mid)
        if right:
            p.text(PAGE_W - MARGIN_R - _width(right, 8.0), foot_y, 8.0, "F1", right)
        p.line(MARGIN_L, foot_y + 12, MARGIN_L + CONTENT_W, foot_y + 12, 0.4)

    return _assemble(pages)


def _assemble(pages: list[_Page]) -> bytes:
    objects: list[bytes] = []

    def add(obj: bytes) -> int:
        objects.append(obj)
        return len(objects)

    # 1 catalog, 2 pages tree — filled later
    add(b"<< /Type /Catalog /Pages 2 0 R >>")
    add(b"")  # placeholder for pages

    font_f1 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    font_f2 = add(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"
    )
    font_f3 = add(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>"
    )

    page_ids: list[int] = []
    for page in pages:
        stream = ("\n".join(page.ops) + "\n").encode("latin-1", errors="replace")
        content_id = add(
            f"<< /Length {len(stream)} >>\nstream\n".encode("ascii")
            + stream
            + b"endstream"
        )
        page_id = add(
            (
                f"<< /Type /Page /Parent 2 0 R "
                f"/MediaBox [0 0 {PAGE_W:.2f} {PAGE_H:.2f}] "
                f"/Resources << /Font << /F1 {font_f1} 0 R /F2 {font_f2} 0 R /F3 {font_f3} 0 R >> >> "
                f"/Contents {content_id} 0 R >>"
            ).encode("ascii")
        )
        page_ids.append(page_id)

    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("ascii")

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f"{i} 0 obj\n".encode("ascii"))
        out.extend(obj)
        out.extend(b"\nendobj\n")
    xref_pos = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    out.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(out)
