"""Read a user's file into a redacted `SourceDoc`.

`resolve` proves the path stays inside the user's own files; `parse_bytes` is pure
parsing so tests and callers can hand it bytes. Everything that leaves this module
has been through `redact`: `rows`, `headers` and `text` are the only content fields.
`text` is the whole redacted file and is what an evidence quote is checked against;
for tabular files it is the TSV the agent sees (`row<TAB>headers`, then `n<TAB>cells`,
n counted over non-empty data rows from 1; xlsx adds a `[sheet NAME]` line per sheet).
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from helpers import files

from plugins._ea_inventory.helpers import redact

MAX_BYTES = 20 * 1024 * 1024
MAX_ROWS = 50_000
MAX_COLS = 500
#: An .xlsx or .docx is a zip: what it unpacks to is capped too, or 20 MB of zeros becomes gigabytes.
MAX_UNZIPPED = 200 * 1024 * 1024
_TEXT = {".txt", ".log", ".cfg", ".conf", ".md", ".json", ".yaml", ".yml", ".ini", ".text", ".out", ""}
_KINDS = {".csv": "csv", ".tsv": "csv", ".xlsx": "xlsx", ".xlsm": "xlsx", ".xls": "xls", ".pdf": "pdf", ".docx": "docx"}


@dataclass
class SourceDoc:
    name: str
    kind: str  # csv|xlsx|xls|pdf|docx|text
    source_id: str  # sha256 of the file
    text: str  # full redacted text, for the evidence check
    rows: list[dict[str, str]] = field(default_factory=list)
    headers: list[str] = field(default_factory=list)
    sheets: list[str] = field(default_factory=list)
    pages: int | None = None
    redactions: dict[str, int] = field(default_factory=dict)
    truncated: bool = False


def resolve(path: str, root) -> Path:
    """The real file behind `path`, which must lie inside `root` (a dir or a list of dirs)."""
    roots = [Path(r).resolve() for r in ([root] if isinstance(root, (str, Path)) else root)]
    given = str(path or "").strip()
    if not given:
        raise ValueError("No file path given.")
    if os.path.isabs(given):
        cands = [Path(files.fix_dev_path(given))]
    else:  # relative to the root, or a bare upload name
        cands = [r / p for r in roots for p in (given, f"uploads/{given}")]
    for c in cands:
        real = c.resolve()  # collapses `..` and symlinks before the comparison
        if any(real.is_relative_to(r) for r in roots) and real.is_file():
            return real
    # the path as given decides the message: a `..` that escapes is "outside", not "missing"
    first = cands[0].resolve()
    raise ValueError("File not found." if any(first.is_relative_to(r) for r in roots) else "That path is outside your files.")


def read_source(path: str, *, root=None, sheet: str | None = None) -> SourceDoc:
    """Resolve, size-check and parse `path`.

    `root` defaults to the folders a model-named path may point into (`helpers.workdir.user_file_roots`: workdir,
    uploads, downloads, skills, projects), never the whole per-user tree, which also holds the change records with
    their seals and every chat transcript. A project's private folders are refused as `confine_path` refuses them.
    """
    from helpers import workdir

    if root is None:
        root = workdir.user_file_roots()
    real = resolve(path, root)
    if workdir.is_project_private(str(real)):
        raise ValueError("That path is outside your files.")
    if real.stat().st_size > MAX_BYTES:
        raise ValueError(f"File is larger than {MAX_BYTES // 1024 // 1024} MB.")
    return parse_bytes(real.name, real.read_bytes(), sheet=sheet)


def _norm(h) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(h or "").strip().lower()).strip("_")


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return str(v).strip()


def _table(raw, counts: dict, budget: int):
    """(headers, redacted rows as lists, truncated). First non-empty row is the header."""
    headers: list[str] = []
    out: list[list[str]] = []
    for r in raw:
        cells = [_cell(c) for c in r]
        if not any(cells):
            continue
        if not headers:
            seen: dict[str, int] = {}
            for i, h in enumerate(cells):
                n = _norm(h) or f"col{i + 1}"
                seen[n] = seen.get(n, 0) + 1
                headers.append(n if seen[n] == 1 else f"{n}_{seen[n]}")
            continue
        if len(out) >= budget:
            return headers, out, True
        cells += [""] * (len(headers) - len(cells))
        out.append([redact.redact_cell(h, c, counts) for h, c in zip(headers, cells)])
    return headers, out, False


def _render(headers, rows) -> str:
    return "\n".join(["row\t" + "\t".join(headers)] + [f"{i}\t" + "\t".join(r) for i, r in enumerate(rows, 1)])


def _tabular(name, kind, sid, sheets: dict[str, list], pick: str | None) -> SourceDoc:
    """`sheets` maps sheet name -> raw rows (csv has one, unnamed)."""
    if pick is not None and pick not in sheets:
        raise ValueError(f"No sheet named {pick!r}. Sheets: {', '.join(sheets)}.")
    counts: dict[str, int] = {}
    budget, truncated, chosen, parts = MAX_ROWS, False, None, []
    for sname, raw in sheets.items():
        headers, rows, cut = _table(raw, counts, budget)
        budget -= len(rows)
        truncated |= cut
        if headers and chosen is None and pick in (None, sname):
            chosen = (headers, rows)
        if headers:
            parts.append((f"[sheet {sname}]\n" if kind != "csv" else "") + _render(headers, rows))
    headers, rows = chosen or ([], [])
    return SourceDoc(
        name=name, kind=kind, source_id=sid, text="\n".join(parts), headers=headers,
        rows=[dict(zip(headers, r)) for r in rows],
        sheets=[] if kind == "csv" else list(sheets), redactions=counts, truncated=truncated,
    )


def _decode(data: bytes) -> str:
    if b"\x00" in data[:4096]:
        raise ValueError("This looks like a binary file, not text.")
    return data.decode("utf-8-sig", errors="replace")


def _csv_rows(data: bytes, name: str) -> list[list[str]]:
    text = _decode(data)
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel_tab if name.lower().endswith(".tsv") else csv.excel
    try:
        return list(csv.reader(io.StringIO(text, newline=""), dialect))
    except csv.Error as e:
        raise ValueError(f"Could not parse the CSV: {e}") from e


def parse_bytes(name: str, data: bytes, *, sheet: str | None = None) -> SourceDoc:
    """Parse and redact a file's bytes; the suffix of `name` picks the format."""
    if len(data) > MAX_BYTES:
        raise ValueError(f"File is larger than {MAX_BYTES // 1024 // 1024} MB.")
    sid = hashlib.sha256(data).hexdigest()
    ext = Path(name).suffix.lower()
    kind = _KINDS.get(ext) or ("text" if ext in _TEXT else None)
    if kind is None:
        raise ValueError(f"Unsupported file type {ext!r}. Use CSV, Excel, PDF, Word or a text/config file.")
    try:
        if kind in ("xlsx", "docx"):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if sum(item.file_size for item in archive.infolist()) > MAX_UNZIPPED:
                    raise ValueError(f"File unpacks to more than {MAX_UNZIPPED // 1024 // 1024} MB.")
        if kind == "csv":
            return _tabular(name, kind, sid, {"": _csv_rows(data, name)}, None)
        if kind == "xlsx":
            from openpyxl import load_workbook

            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            try:
                # Stop at the caps while reading: a sheet can claim a million rows of sixteen thousand columns in a few bytes.
                sheets = {
                    ws.title: list(ws.iter_rows(
                        values_only=True, max_row=MAX_ROWS + 1,
                        max_col=min(ws.max_column, MAX_COLS) if ws.max_column else None,
                    ))
                    for ws in wb.worksheets
                }
                return _tabular(name, kind, sid, sheets, sheet)
            finally:
                wb.close()
        if kind == "xls":
            import xlrd

            wb = xlrd.open_workbook(file_contents=data)
            sheets = {s.name: [s.row_values(i) for i in range(s.nrows)] for s in wb.sheets()}
            return _tabular(name, kind, sid, sheets, sheet)
        if kind == "pdf":
            import fitz

            with fitz.open(stream=data, filetype="pdf") as pdf:
                pages = [p.get_text() for p in pdf]
            if not "".join(pages).strip():
                raise ValueError("This PDF has no text layer (scanned image?). OCR is not supported.")
            raw = "\n".join(f"[page {i}]\n{t}" for i, t in enumerate(pages, 1))
            text, counts = redact.redact_text(raw)
            return SourceDoc(name=name, kind=kind, source_id=sid, text=text, pages=len(pages), redactions=counts)
        if kind == "docx":
            import docx

            d = docx.Document(io.BytesIO(data))
            raw = "\n".join(
                b.text if hasattr(b, "text") else "\n".join("\t".join(c.text for c in r.cells) for r in b.rows)
                for b in d.iter_inner_content()
            )
        else:
            raw = _decode(data)
    except ValueError:
        raise
    except Exception as e:  # a corrupt or password-protected file
        raise ValueError(f"Could not read {name}: {type(e).__name__}.") from e
    text, counts = redact.redact_text(raw)
    return SourceDoc(name=name, kind=kind, source_id=sid, text=text, redactions=counts)
