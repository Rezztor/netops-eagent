"""Where change records live: one JSON file per change, per user.

Per-user because a change belongs to the person who asked for it, and this
codebase isolates user state by directory rather than by an owner column
(ADR 0003). An approver reads another user's record through the API, which is
where the approver role is checked -- not by reaching into their directory here.

Every status transition is audited from `save()` rather than from the tools and
handlers that call it. There are five callers already and there will be more, and
a change that reached a device without an audit line because someone added a
sixth is the failure this arrangement exists to make impossible. It also means
the audit trail cannot disagree with the record: the same call writes both.

**The file is not trusted on its own.** A record is a file under the user's
directory, and a file can be rewritten by something that is not this module (a
file tool, a restore, a hand). So `save()` signs what a gate trusts -- the
status, the approver, the approved hash, the expiry, the CR/SR reference and the
documents -- with the change id and the owner, under a key that is not a file
(`_key`). Every read checks it: a record that claims to be `approved` or
`applying` without a valid signature is put back to `awaiting_approval`, with the
approval removed, and audited as `network.change.tampered`. It is never applied.

**A signature does not stop a copy, so the approval lives in PostgreSQL** (ADR 0077, table `change_approval`). The
file keeps the plan; the row holds the status, the approver, the approved hash, the CR/SR number, the times and how
far the approval has been used, and every read lays the row over the file. So an approved file put back after the
apply meets a row that says `applied` and is refused. A record with no row is not approved, whatever the file says.

Three functions write an approval and nothing else can: `approve` (called by the approve endpoints), `take` (one
UPDATE that names the status, the hash and the step, so of two applies one proceeds) and `give_back` (the executor
turned the call away before anything ran). `save` writes every other status to the row and refuses to write
`approved` or `applying` unless the row already says so. A read or write outside a user's scope finds no row.

A record that starts to wait for approval is announced to the people who may sign it (`approvers.notify`, ADR 0089),
from `_store`, for the same reason the audit is: every transition passes here.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any

from helpers import db, user_scope

from plugins._ea_ansible.helpers.change_model import APPLYING, APPROVED, AWAITING_APPROVAL, DRAFT, RENDERED, ChangeRecord, plan_hash

from psycopg import errors as db_errors

try:  # pragma: no cover - the audit plugin is bundled but can be switched off
    from plugins._audit.helpers import audit
except Exception:  # noqa: BLE001
    audit = None  # type: ignore[assignment]

DIR_NAME = "network_changes"

#: The session-signing secret, which the deployment sets in the environment and never in a file under `usr/`.
ENV_KEY = "FLASK_SECRET_KEY"
#: Without one (a developer's machine) the key lasts as long as the process, so an approval does not outlive a restart.
_PROCESS_KEY = secrets.token_hex(32)
#: The states a gate acts on. A record in one of them is believed only with a valid seal.
_AUTHORITY = (APPROVED, APPLYING)


#: What the row decides, in the order `_rows` selects it.
_ROW_FIELDS = ("status", "approved_by", "approved_hash", "reference", "approval_basis", "approved_at", "expires_at")
_NOT_APPROVED = (AWAITING_APPROVAL, "", "", "", "", None)  # every row field but the expiry
#: The states a change is still being asked about. A row that has left them is never put back (`save`).
_OPEN = (DRAFT, RENDERED, AWAITING_APPROVAL)


class ChangeNotFound(Exception):
    """No such change for this user."""


class NotApproved(Exception):
    """`save` was asked to write `approved` or `applying` and the approval register does not say so."""


def _dir(user_id: str | None = None) -> str:
    return user_scope.ensure_user_dir(DIR_NAME, user_id=user_id)


def _path(change_id: str, user_id: str | None = None) -> str:
    safe = "".join(c for c in str(change_id) if c.isalnum())
    if not safe:
        raise ChangeNotFound("A change id must be alphanumeric.")
    return os.path.join(_dir(user_id), f"{safe}.json")


def _key() -> bytes:
    return hmac.new((os.getenv(ENV_KEY) or _PROCESS_KEY).encode("utf-8"), b"eagent change record", hashlib.sha256).digest()


def _seal(record: ChangeRecord, path: str) -> str:
    """The signature over what a gate trusts, bound to this change and to the user whose directory holds it.

    The owner is read from the path, so a file copied into another user's directory does not verify there. The
    plan itself is bound through `approved_hash`, which the gates compare with the hash of what would run.
    """
    owner = os.path.basename(os.path.dirname(os.path.dirname(os.path.abspath(path))))
    body = plan_hash(
        id=record.id, owner=owner, status=record.status, approved_by=record.approved_by,
        approved_hash=record.approved_hash, approved_at=record.approved_at, approval_basis=record.approval_basis,
        expires_at=record.expires_at, reference=record.reference, documents=record.documents,
    )
    return hmac.new(_key(), body.encode("utf-8"), hashlib.sha256).hexdigest()


def _write(record: ChangeRecord, path: str) -> None:
    record.seal = _seal(record, path)
    data = record.to_dict()
    del data["current_hash"]  # computed on read; a stored copy is only a convenience for a forger
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({**data, "seal": record.seal}, handle, indent=2, sort_keys=True)


def _scope(user_id: str | None) -> str:
    """The owner whose rows this call may see. With no user at all it is no scope, and no row is visible."""
    return user_id or user_scope.get_current_user_id() or db.NO_SCOPE


def _rows(user_id: str | None, change_id: str | None = None) -> dict[str, tuple]:
    """The owner's approval rows by change id: all of them, or the one named."""
    with db.transaction(_scope(user_id)) as conn:
        found = conn.execute(
            f"SELECT id, {', '.join(_ROW_FIELDS)} FROM change_approval" + (" WHERE id = %s" if change_id else ""),
            (change_id,) if change_id else (),
        ).fetchall()
    return {row[0]: tuple(row[1:]) for row in found}


def _read(path: str, user_id: str | None = None, rows: dict[str, tuple] | None = None) -> ChangeRecord:
    """The record in a file, with the approval register laid over it.

    The row decides the status, the approver, the hash, the number and the times. No row: nothing is approved. A
    row that says approved over a file this server did not sign: the approval is taken back in the row too.
    """
    with open(path, "r", encoding="utf-8") as handle:
        record = ChangeRecord.from_dict(json.load(handle))
    claimed, claimed_by = record.status, record.approved_by
    signed = hmac.compare_digest(record.seal, _seal(record, path))
    row = (_rows(user_id, record.id) if rows is None else rows).get(record.id)
    if row is not None:
        for name, value in zip(_ROW_FIELDS, row):
            setattr(record, name, value)
    elif record.status in _AUTHORITY:
        _take_away(record)
    unsigned = record.status in _AUTHORITY and not signed
    # The documents carry what the approve endpoint checks (which version was issued, when it was downloaded); a file
    # this server did not sign keeps none of it, whatever its status, so the document has to be issued again.
    forged_documents = not signed and bool(record.documents)
    if forged_documents:
        record.documents = []
    if unsigned:
        with db.transaction(_scope(user_id)) as conn:
            conn.execute("UPDATE change_approval SET status = %s, updated_at = now() WHERE id = %s AND status = ANY(%s)",
                         (AWAITING_APPROVAL, record.id, list(_AUTHORITY)))
        _take_away(record)
    if unsigned or forged_documents or (claimed in _AUTHORITY and record.status not in _AUTHORITY):
        # The file was rewritten under an approval, or claimed one the register does not hold (a copy put back
        # after it was used).
        if audit is not None:
            audit.emit(
                "network.change.tampered", change_id=record.id, kind=record.kind, claimed_status=claimed,
                claimed_approved_by=claimed_by, status=record.status, requested_by=record.requested_by,
            )
        _write(record, path)  # once, so the same file is not reported on every read
    return record


def _take_away(record: ChangeRecord) -> None:
    (record.status, record.approved_by, record.approved_hash, record.reference, record.approval_basis,
     record.approved_at) = _NOT_APPROVED


def _store(record: ChangeRecord, user_id: str | None) -> ChangeRecord:
    """Write the file and audit the transition. The row is the caller's business."""
    path = _path(record.id, user_id)
    previous = _status_on_disk(path)
    _write(record, path)
    if previous != record.status:
        _audit_transition(record, previous)
        if record.status == AWAITING_APPROVAL:
            from plugins._ea_ansible.helpers import approvers

            approvers.notify(record, user_id or user_scope.get_current_user_id() or "")  # never raises
    return record


def save(record: ChangeRecord, user_id: str | None = None) -> ChangeRecord:
    """Write the record, and its status to the approval register.

    Never an approval: a record that says `approved` or `applying` is written only when the register already says
    the same (`approve`, `take` and `give_back` are what change that), and `NotApproved` is raised otherwise, so a
    caller that forgot to take the approval fails instead of running without one. The approval columns are theirs
    alone too: this writes the status, the kind and the expiry, never the approver, hash, number or times.

    A record loaded before an approval and saved after it must not undo it, so the row is never moved back to an
    open status (draft, rendered, awaiting approval) once it has left one: such a save is dropped, file and row,
    and the record is returned as it was. Everything legitimate that reopens a change makes a new record.
    The row is written first, in the same transaction as the file, so a refused save leaves the file alone.
    """
    if record.status in _AUTHORITY:
        row = _rows(user_id, record.id).get(record.id)
        if row is None or row[0] != record.status:
            raise NotApproved(f"Change {record.id} is not '{record.status}' in the approval register, so it was not saved as that.")
        return _store(record, user_id)
    values = [getattr(record, name) for name in _ROW_FIELDS]
    with db.transaction(_scope(user_id)) as conn:
        written = conn.execute(
            f"INSERT INTO change_approval (id, owner_id, kind, {', '.join(_ROW_FIELDS)}) "
            f"VALUES (%s, app_user(), %s, {', '.join(['%s'] * len(_ROW_FIELDS))}) "
            "ON CONFLICT (id) DO UPDATE SET kind = EXCLUDED.kind, status = EXCLUDED.status, "
            "expires_at = EXCLUDED.expires_at, updated_at = now() "
            "WHERE change_approval.status = ANY(%s) OR EXCLUDED.status <> ALL(%s) RETURNING id",
            (record.id, record.kind, *values, list(_OPEN), list(_OPEN)),
        ).fetchone()
        if written:
            _store(record, user_id)
    return record


def approve(record: ChangeRecord, approver: str, user_id: str | None = None, reference: str = "", basis: str = "") -> bool:
    """Record the approval of the plan as it stands now. False when the register does not hold this change open.

    The only writer of `approved`. Called by the approve endpoints, which check who is asking, and by
    `approve_standing`. A change that is already approved, in use, used or closed is not approved again; an id
    another user holds is refused by the row's policy.
    """
    now = time.time()
    try:
        with db.transaction(_scope(user_id)) as conn:
            done = conn.execute(
                "INSERT INTO change_approval (id, owner_id, kind, status, approved_by, approved_hash, reference, "
                "approval_basis, approved_at, expires_at) VALUES (%s, app_user(), %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET kind = EXCLUDED.kind, status = EXCLUDED.status, "
                "approved_by = EXCLUDED.approved_by, approved_hash = EXCLUDED.approved_hash, reference = EXCLUDED.reference, "
                "approval_basis = EXCLUDED.approval_basis, approved_at = EXCLUDED.approved_at, "
                "step = 0, used_at = NULL, updated_at = now() WHERE change_approval.status = ANY(%s) RETURNING id, expires_at",
                (record.id, record.kind, APPROVED, approver, record.current_hash, reference, basis, now, record.expires_at,
                 list(_OPEN)),
            ).fetchone()
    except db_errors.InsufficientPrivilege:  # the id is another user's row
        done = None
    if not done:
        return False
    record.expires_at = done[1]  # the register's, set when the change was drafted; the in-memory one could be anything
    record.status, record.approved_by, record.approved_hash, record.approved_at = APPROVED, approver, record.current_hash, now
    record.reference, record.approval_basis = reference, basis
    _store(record, user_id)
    return True


def take(record: ChangeRecord, user_id: str | None = None, step: int = 1) -> bool:
    """Use the approval to run what the record holds now. False when there is none to use.

    One statement: the row must be approved (or, for step n of a change set, applying with step n-1 taken), for
    exactly this hash, and unexpired. Of two calls one gets the row; a copied file, a changed plan, a second
    apply and a change that was cancelled, superseded, rejected or expired get nothing.
    """
    with db.transaction(_scope(user_id)) as conn:
        taken = conn.execute(
            "UPDATE change_approval SET status = %s, step = %s, used_at = now(), updated_at = now() "
            "WHERE id = %s AND status = %s AND step = %s AND approved_hash <> '' AND approved_hash = %s "
            "AND (expires_at IS NULL OR expires_at > %s) RETURNING id",
            (APPLYING, step, record.id, APPROVED if step == 1 else APPLYING, step - 1, record.current_hash, time.time()),
        ).fetchone()
    if not taken:
        return False
    record.status = APPLYING
    _store(record, user_id)
    return True


def give_back(record: ChangeRecord, user_id: str | None = None, step: int = 1) -> None:
    """Undo `take` for a step the executor turned away before anything ran: the approval stands as it did."""
    before = APPROVED if step == 1 else APPLYING
    with db.transaction(_scope(user_id)) as conn:
        conn.execute(
            "UPDATE change_approval SET status = %s, step = %s, used_at = CASE WHEN %s = 0 THEN NULL ELSE used_at END, "
            "updated_at = now() WHERE id = %s AND status = %s AND step = %s",
            (before, step - 1, step - 1, record.id, APPLYING, step),
        )
    record.status = before
    _store(record, user_id)


def approve_standing(record: ChangeRecord, basis: str, user_id: str | None = None) -> ChangeRecord:
    """Record a read as approved by the `read_approval` setting, with the requester as approver.

    The one approval that is not a person pressing a card (ADR 0048). Kept here, not
    in a tool, so no tool sets an approval; `basis` says which standing rule applied.
    """
    if not approve(record, record.requested_by, user_id, basis=basis):
        save(record, user_id)  # not approved: the request stays as it was, and its run is refused
    return record


def _status_on_disk(path: str) -> str:
    """What the record said before this write, or "" for a new one.

    Read rather than tracked in memory, because a record can be loaded, changed
    and saved by two different processes -- the tool and the approval endpoint --
    and neither sees the other's object.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return str(json.load(handle).get("status") or "")
    except (OSError, ValueError):
        return ""


def _audit_transition(record: ChangeRecord, previous: str) -> None:
    """One line per transition. Never the commands themselves.

    The plan hash and the command count are what an auditor needs: the hash ties
    the line to a specific text an approver read, and the text itself is in the
    change record, which is where a reader should have to go to see what was
    pushed. An audit stream carrying device configuration is a different kind of
    artefact with different handling, and ADR 0005 ships this one off-box.
    """
    if audit is None:
        return
    issued = next(
        (d.get("sha256", "") for d in record.documents or () if d.get("version") == "issued" and d.get("format") == "pdf"),
        "",
    )
    audit.emit(
        _event_name(record),
        change_id=record.id,
        kind=record.kind,
        job_id=record.job_id,
        previous_status=previous or "new",
        playbook=record.playbook_name,
        limit=record.limit,
        commit=record.commit,
        command_count=len(record.commands or ()),
        render_method=record.render_method,
        approved_hash=record.approved_hash or "",
        current_hash=record.current_hash or "",
        requested_by=record.requested_by,
        approved_by=record.approved_by,
        exit_code=record.exit_code,
        reference=record.reference,
        plan_digest=record.plan_digest,
        document_sha256=issued,
    )


#: Azure records are audited as `azure.change.*` / `azure.action.*` / `azure.suggestion.written`
#: (build contract, "Audit events"); a device record keeps `network.change.<status>`.
_AZURE_EVENT_PREFIX = {"azure_change": "azure.change", "azure_action": "azure.action"}


def _event_name(record: ChangeRecord) -> str:
    prefix = _AZURE_EVENT_PREFIX.get(record.kind)
    if prefix:
        return f"{prefix}.{'drafted' if record.status == 'awaiting_approval' else record.status}"
    if record.kind == "azure_suggestion":
        return "azure.suggestion.written"
    return f"network.change.{record.status}"


def load(change_id: str, user_id: str | None = None) -> ChangeRecord:
    path = _path(change_id, user_id)
    if not os.path.exists(path):
        raise ChangeNotFound(f"There is no change {change_id!r}.")
    return _read(path, user_id)


def list_records(user_id: str | None = None) -> list[ChangeRecord]:
    directory = _dir(user_id)
    rows = _rows(user_id)
    records: list[ChangeRecord] = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json"):
            continue
        try:
            records.append(_read(os.path.join(directory, name), user_id, rows))
        except (OSError, ValueError):
            # A corrupt record is skipped rather than fatal: one unreadable file
            # must not hide every other change from the register.
            continue
    return sorted(records, key=lambda r: r.created_at, reverse=True)


def find_across_users(change_id: str) -> tuple[ChangeRecord, str]:
    """Locate a change without knowing whose it is.

    For the approver, who is by definition not the requester. The caller checks
    the approver's permission; this only finds the file.
    """
    for user_id in user_scope.list_user_ids():
        try:
            return load(change_id, user_id), user_id
        except ChangeNotFound:
            continue
    raise ChangeNotFound(f"There is no change {change_id!r} on this instance.")


def summarise(record: ChangeRecord) -> dict[str, Any]:
    """The register view: enough to decide what to open, not the whole plan."""
    return {
        "id": record.id,
        "kind": record.kind,
        "expires_at": record.expires_at,
        "status": record.status,
        "playbook_name": record.playbook_name,
        "summary": record.summary,
        "limit": record.limit,
        "created_at": record.created_at,
        "requested_by": record.requested_by,
        "approved_by": record.approved_by,
        "reference": record.reference,
        "command_count": len(record.commands),
        "render_method": record.render_method,
    }
