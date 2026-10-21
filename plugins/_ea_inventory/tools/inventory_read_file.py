"""Read a file the user attached, as a redacted view, so it can be mapped to inventory
records. Everything returned went through `helpers/redact.py` inside `readers`; this
tool never opens a file any other way. Gated on `inventory.manage` in
`helpers/tool_permissions.py`."""

from __future__ import annotations

import asyncio
import json

from helpers.tool import Response, Tool

from plugins._ea_inventory.helpers import readers, redact

MAX_CHARS = 20_000


def _int(v, default: int) -> int:
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return default


def view(doc: readers.SourceDoc, offset: int, limit: int) -> tuple[str, dict]:
    """One page of the redacted file and the metadata that locates it."""
    meta: dict = {"source_id": doc.source_id, "kind": doc.kind, "redactions": doc.redactions}
    if doc.headers:  # tabular: the chosen sheet only, rows numbered from 1
        lines = [f"{i}\t" + "\t".join(r.get(h, "") for h in doc.headers) for i, r in enumerate(doc.rows, 1)]
        head, total = ["row\t" + "\t".join(doc.headers)], len(doc.rows)
        meta.update(total_rows=total, sheets=doc.sheets, truncated=doc.truncated)
    else:  # text, config, PDF (page markers are lines), Word
        lines = doc.text.split("\n")
        head, total = [], len(lines)
        meta.update(total_lines=total, pages=doc.pages)
    out, size, n = [], sum(map(len, head)), 0
    for ln in lines[offset: offset + limit]:
        if out and size + len(ln) > MAX_CHARS:
            break
        out.append(ln[:MAX_CHARS])
        size += len(ln) + 1
        n += 1
    meta["next_offset"] = offset + n if offset + n < total else None
    return "\n".join(head + out), meta


class InventoryReadFile(Tool):
    async def execute(self, **kwargs) -> Response:
        a = self.args or {}
        try:
            doc = await asyncio.to_thread(
                readers.read_source, str(a.get("path") or ""), sheet=str(a.get("sheet") or "") or None
            )
            body, meta = view(doc, _int(a.get("offset"), 0), min(_int(a.get("limit"), 100), 500) or 100)
        except Exception as e:  # message is ours (ValueError) or a type name; never file content
            return Response(message=f"Could not read the file: {redact.redact_text(str(e))[0]}", break_loop=False)
        return Response(message=f"{body}\n\n{json.dumps(meta)}", break_loop=False)
