"""Download a change document: the stored PDF, Word copy or runbook, after its SHA-256 is checked against the record.

The file is found from the record (never from a name in the request), so a crafted request cannot name another path.
A user may download their own records' documents (`tools.network`); another user's need `network.approve`, as
`changes_review` does. A document of a superseded change is void and is not served. The first download of an `issued`
document **by the requester** sets `downloaded_at`, which approval requires; a reviewer's download does not count for
them. Audit: `azure.change.document_downloaded`. Returns a JSON error when the file cannot be served.
"""

from __future__ import annotations

import hashlib
import mimetypes
from datetime import datetime, timezone
from typing import Any

from helpers import login, rbac, user_scope
from helpers.api import ApiHandler, Request, Response

from plugins._ea_ansible.helpers import change_store
from plugins._ea_ansible.helpers.change_model import SUPERSEDED
from plugins._ea_azure_change.helpers import records

try:  # pragma: no cover - the audit plugin is bundled but can be switched off
    from plugins._audit.helpers import audit
except Exception:  # noqa: BLE001
    audit = None  # type: ignore[assignment]

VERSIONS = ("issued", "applied", "runbook")
FORMATS = ("pdf", "docx")


class AzureDocument(ApiHandler):
    @classmethod
    def required_permission(cls) -> str | None:
        return rbac.PERM_TOOL_NETWORK

    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        version, fmt = str(input.get("version") or "issued").lower(), str(input.get("format") or "pdf").lower()
        if version not in VERSIONS or fmt not in FORMATS:
            return {"ok": False, "error": f"version must be one of {VERSIONS} and format one of {FORMATS}."}
        try:
            record, owner = change_store.find_across_users(str(input.get("change_id") or "").strip())
        except change_store.ChangeNotFound as error:
            return {"ok": False, "error": str(error)}
        actor = user_scope.get_current_user_id() or ""
        if record.kind not in (*records.KINDS, "azure_suggestion"):
            return {"ok": False, "error": f"{record.id} is not an Azure change."}
        if owner != actor and not rbac.has_permission(login.current_role(), rbac.PERM_NETWORK_APPROVE):
            return {"ok": False, "error": "You can download the documents of your own changes only."}
        if record.status == SUPERSEDED and version == "issued":
            return {"ok": False, "error": "This change was superseded and its document is void. Open the newer change."}
        metas = [d for d in record.documents if d.get("version") == version and d.get("format") == fmt
                 and (version != "issued" or d.get("plan_digest") == record.plan_digest)]
        if not metas:
            return {"ok": False, "error": f"There is no {version} {fmt} document for this change."}
        meta = metas[-1]
        path = records.document_path(record, meta)
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError:
            return {"ok": False, "error": "The document file is missing, so it cannot be relied on. Draft the change again."}
        if hashlib.sha256(data).hexdigest() != meta.get("sha256"):
            return {"ok": False, "error": "The document file no longer matches what was issued, so it is not served."}
        if owner == actor and version == "issued" and not meta.get("downloaded_at"):
            meta["downloaded_at"] = datetime.now(timezone.utc).isoformat()
            change_store.save(record, owner)
        if audit is not None:
            audit.emit("azure.change.document_downloaded", change_id=record.id, version=version, format=fmt, sha256=meta["sha256"],
                       downloaded_by=actor, requested_by=record.requested_by, plan_digest=record.plan_digest)
        name = str(meta.get("filename") or f"azure-change-{record.id[:8]}.{fmt}")
        kind = mimetypes.guess_type(name)[0] or "application/octet-stream"
        return Response(data, content_type=kind, headers={"Content-Disposition": f'attachment; filename="{name}"',
                                                              "Content-Length": str(len(data)), "Cache-Control": "no-store"})
