"""What the executor remembers: job records, the cost ledger, and the write-ahead record of every apply.

Jobs and the ledger are small JSON files on the executor's own volume (`REZZTOR_CLOUD_STATE_DIR`), written beside the
target and renamed. A job that was running when the executor stopped is `failed` on the next start: nothing resumes a
half-made change on its own. `cloud_state_version` is the one table here, written before every apply as `eagent_cloud`;
its unique `change_id` is what makes a change apply once (ADR 0062, 0064).
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from typing import Any

from cloud_executor import db, keys

_lock = threading.Lock()
RUNNING = ("queued", "running")


class AlreadyApplied(Exception):
    """This change id has an apply on record. A change applies once."""


def _dir(*parts: str) -> str:
    path = os.path.join(keys.state_dir(), *parts)
    os.makedirs(os.path.dirname(path) if parts[-1].endswith(".json") else path, exist_ok=True)
    return path


def _write(path: str, data: Any) -> None:
    tmp = f"{path}.tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    os.replace(tmp, path)


# -------------------------------------------------------------------------------------------------------- jobs

def new_job(change_id: str, actor: str) -> str:
    job_id = uuid.uuid4().hex
    update(job_id, status="queued", verified=None, changed_ids=[], change_id=change_id, actor=actor[:200],
           started_at=time.time(), ended_at=None)
    return job_id


def update(job_id: str, **fields: Any) -> dict[str, Any]:
    path = _dir("jobs", f"{job_id}.json")
    with _lock:
        record = get(job_id) or {}
        record.update(fields)
        _write(path, record)
    return record


def get(job_id: str) -> dict[str, Any] | None:
    if not re.fullmatch(r"[0-9a-f]{32}", job_id or ""):
        return None
    try:
        with open(_dir("jobs", f"{job_id}.json"), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def public(record: dict[str, Any]) -> dict[str, Any]:
    """The answer to `GET /v1/jobs/{id}`: status, verification, the IDs it made, an error text, the times."""
    keep = ("status", "verified", "changed_ids", "error", "started_at", "ended_at")
    return {k: record[k] for k in keep if k in record and (k != "error" or record[k])}


def fail_interrupted() -> int:
    """At start: every job that was queued or running did not finish, and says so."""
    count = 0
    folder = _dir("jobs", "x.json")
    for name in os.listdir(os.path.dirname(folder)):
        job_id = name.removesuffix(".json")
        record = get(job_id)
        if record and record.get("status") in RUNNING:
            update(job_id, status="failed", ended_at=time.time(), error="The executor stopped while this change was running.")
            count += 1
    return count


# ----------------------------------------------------------------------------------------------------- ledger

def committed_usd() -> float:
    """What the applied changes already add each month. The product never removes anything, so this only grows."""
    try:
        with open(_dir("ledger.json"), encoding="utf-8") as handle:
            return float(sum(float(v) for v in json.load(handle).values()))
    except (OSError, ValueError, AttributeError):
        return 0.0


def ledger_ids() -> set[str]:
    """The change ids whose apply ended without error. No ledger yet is an empty one; an unreadable one raises."""
    try:
        with open(_dir("ledger.json"), encoding="utf-8") as handle:
            return set(json.load(handle))
    except FileNotFoundError:
        return set()


def commit_usd(change_id: str, usd_month: float) -> None:
    with _lock:
        try:
            with open(_dir("ledger.json"), encoding="utf-8") as handle:
                ledger = json.load(handle)
        except (OSError, ValueError):
            ledger = {}
        ledger[change_id] = usd_month
        _write(_dir("ledger.json"), ledger)


# --------------------------------------------------------------------------------------- cloud_state_version

def applied_before(change_id: str) -> bool:
    with db.transaction() as conn:
        return conn.execute("SELECT 1 FROM cloud_state_version WHERE change_id = %s", (change_id,)).fetchone() is not None


def record_apply(*, workspace: str, state_name: str, change_id: str, code: str, variables: dict[str, Any],
                 plan_digest: str, reference: str) -> None:
    """The write-ahead record: the state as it is now (ciphertext, copied untouched), the code and the number. Raises
    `AlreadyApplied` when the change id is on record, which is what refuses a second apply."""
    import psycopg  # noqa: PLC0415
    from psycopg.types.json import Jsonb  # noqa: PLC0415

    try:
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO cloud_state_version (workspace, change_id, state, code, variables, plan_digest, reference) "
                "VALUES (%s, %s, COALESCE((SELECT data FROM states WHERE name = %s), ''), %s, %s, %s, %s)",
                (workspace, change_id, state_name, code, Jsonb(variables), plan_digest, reference))
    except psycopg.errors.UniqueViolation as error:
        raise AlreadyApplied(f"The change {change_id} has already been applied.") from error


def _made_rows(subscription: str, makers: list[str]) -> list[tuple[str, str]]:
    """`(workspace, change_id)` of every write-ahead row of a template in `makers`. The workspace is split here, in SQL,
    on `/`: a group name may hold `_`, which a `LIKE` would take for a wildcard."""
    with db.transaction() as conn:
        return [(w, c) for w, c in conn.execute(
            "SELECT workspace, change_id FROM cloud_state_version "
            "WHERE split_part(workspace, '/', 1) = %s AND split_part(workspace, '/', 3) = ANY(%s)",
            (subscription.lower(), list(makers))).fetchall()]


def made_groups(subscription: str, makers: list[str]) -> set[str]:
    """Lower-case names of the resource groups this executor made (ADR 0074): a row of its own `cloud_state_version`
    for a template in `makers` (the templates whose types include a resource group) whose change id is in its ledger,
    so the apply ended without error. Raises on a database or ledger error; it never answers with an empty set instead."""
    ledger = ledger_ids()
    found = set()
    for workspace, change_id in _made_rows(subscription, makers):
        parts = workspace.split("/")
        if change_id in ledger and len(parts) == 4:
            found.add(parts[1].lower())
    return found
