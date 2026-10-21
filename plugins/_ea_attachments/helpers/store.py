"""Where an attachment lives once it has been retrieved.

Under the owner's own directory, beside their chats and memory, so it is removed
with the account and is never reachable through another user's session. The
download endpoint resolves the owner from the session and never from the request,
so an id guessed or copied from someone else's transcript resolves to a path in
the guesser's own directory, which does not exist.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from helpers import files, user_scope

ATTACHMENTS_DIR = "attachments"
MANIFEST = "manifest.json"

# What survives of a name the agent chose. Everything else becomes an
# underscore, so a name can never be a path, a traversal or a hidden file.
_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def safe_filename(name: str, fallback: str = "attachment") -> str:
    base = os.path.basename(str(name or "").strip())
    cleaned = _SAFE.sub("_", base).strip("._") or fallback
    return cleaned[:120]


def root(user_id: str) -> str:
    return user_scope.get_user_path(ATTACHMENTS_DIR, user_id=user_id)


def _manifest_path(user_id: str) -> str:
    return os.path.join(root(user_id), MANIFEST)


def read_manifest(user_id: str) -> dict[str, Any]:
    try:
        with open(_manifest_path(user_id), "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def write_manifest(user_id: str, manifest: dict[str, Any]) -> None:
    path = Path(_manifest_path(user_id))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def save(
    user_id: str,
    *,
    filename: str,
    content: bytes,
    context_id: str,
    source_path: str,
    max_files: int,
) -> dict[str, Any]:
    """Store one file and return its record."""
    attachment_id = uuid.uuid4().hex
    name = safe_filename(filename)

    folder = Path(root(user_id)) / attachment_id
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name

    # The name is already reduced to a safe set, but assert the result rather
    # than trust the regex: this is the boundary where a path is built.
    if not files.is_in_dir(str(target), root(user_id)):
        raise ValueError("Refusing to write outside the attachments directory.")

    temporary = folder / ".partial"
    temporary.write_bytes(content)
    os.replace(temporary, target)

    record = {
        "id": attachment_id,
        "filename": name,
        "bytes": len(content),
        "context_id": context_id,
        "source_path": source_path,
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    manifest = read_manifest(user_id)
    manifest[attachment_id] = record
    _prune(user_id, manifest, max_files)
    write_manifest(user_id, manifest)
    return record


def _prune(user_id: str, manifest: dict[str, Any], max_files: int) -> None:
    """Drop the oldest beyond the cap. Nothing else ever removes these."""
    if max_files <= 0 or len(manifest) <= max_files:
        return
    import shutil

    ordered = sorted(manifest.values(), key=lambda r: str(r.get("created") or ""))
    for record in ordered[: len(manifest) - max_files]:
        attachment_id = str(record.get("id") or "")
        if not attachment_id:
            continue
        folder = Path(root(user_id)) / attachment_id
        if files.is_in_dir(str(folder), root(user_id)):
            shutil.rmtree(folder, ignore_errors=True)
        manifest.pop(attachment_id, None)


def locate(user_id: str, attachment_id: str) -> tuple[str, dict[str, Any]] | None:
    """The path and record for one of this user's attachments, or None.

    The id is validated as a token rather than trusted as a path component, and
    the resolved path is checked against the user's own directory.
    """
    token = str(attachment_id or "").strip().lower()
    if not token or len(token) != 32 or not all(c in "0123456789abcdef" for c in token):
        return None

    record = read_manifest(user_id).get(token)
    if not isinstance(record, dict):
        return None

    path = Path(root(user_id)) / token / str(record.get("filename") or "")
    if not files.is_in_dir(str(path), root(user_id)) or not path.is_file():
        return None
    return str(path), record
