"""Durable job history for the executor.

The executor used to keep jobs in an `OrderedDict` capped at two hundred, and
said so in its own docstring: the durable record of what ran belonged to the
application's audit trail, not here. That was right while the executor had one
caller. It stops being right the moment a person can open a console and ask what
ran last week, because the audit trail records *that* a job ran and by whose
authority -- it does not carry the per-task `commands` and `diff` an engineer
actually reads.

So this is a second record with a different job, not a duplicate of the first.
The audit trail answers "who did what"; this answers "what did the device say".

SQLite rather than a table in the application's database, because the whole
point of ADR 0030 is that this container is reachable from the application and
not the other way round. A store the application could write to would be a
route back in.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any, Iterable

#: Where the store lives. A directory, not a file, because the WAL and shared
#: memory files sit beside the database and all three need to be writable --
#: which is also why `readOnlyRootFilesystem: true` needs a volume here.
ENV_STATE_DIR = "REZZTOR_ANSIBLE_STATE_DIR"
DEFAULT_STATE_DIR = "/var/lib/eagent-ansible"

ENV_RETENTION_DAYS = "REZZTOR_ANSIBLE_JOB_RETENTION_DAYS"
ENV_RETENTION_MAX = "REZZTOR_ANSIBLE_JOB_RETENTION_MAX"
DEFAULT_RETENTION_DAYS = 90
DEFAULT_RETENTION_MAX = 5000

#: Who asked for the job. `cli` can only ever be self-reported -- see the note
#: on `ea-ansible` in the admin guide -- so it is a label, not an assurance.
SOURCES = ("agent", "console", "cli")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id            TEXT PRIMARY KEY,
    source            TEXT NOT NULL,
    actor             TEXT NOT NULL DEFAULT '',
    playbook          TEXT NOT NULL,
    catalogue_commit  TEXT NOT NULL DEFAULT '',
    variables         TEXT NOT NULL DEFAULT '{}',
    limit_pattern     TEXT NOT NULL DEFAULT '',
    check_mode        INTEGER NOT NULL DEFAULT 1,
    status            TEXT NOT NULL,
    exit_code         INTEGER,
    started_at        REAL NOT NULL,
    finished_at       REAL,
    events            TEXT NOT NULL DEFAULT '[]',
    output            TEXT NOT NULL DEFAULT '',
    error             TEXT NOT NULL DEFAULT '',
    change_id         TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS jobs_started_at ON jobs (started_at DESC);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs (status);
CREATE INDEX IF NOT EXISTS jobs_source ON jobs (source);
CREATE INDEX IF NOT EXISTS jobs_playbook ON jobs (playbook);
"""

#: Every column, in the order `record()` writes them.
COLUMNS = (
    "job_id", "source", "actor", "playbook", "catalogue_commit", "variables",
    "limit_pattern", "check_mode", "status", "exit_code", "started_at",
    "finished_at", "events", "output", "error", "change_id",
)

#: Columns carrying JSON, decoded on the way out so a caller never has to know
#: that the store is SQLite.
_JSON_COLUMNS = ("variables", "events")


def state_dir() -> str:
    return (os.getenv(ENV_STATE_DIR) or DEFAULT_STATE_DIR).strip() or DEFAULT_STATE_DIR


def _int_env(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name) or default))
    except ValueError:
        return default


class JobStore:
    """One SQLite file, one connection, one lock.

    A connection per thread would be the usual answer, but the write rate here
    is one row per job plus one update when it finishes. A single serialised
    connection is simpler to reason about and cannot deadlock against itself.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        # WAL so the console can read history while a job is being recorded.
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(SCHEMA)
        self._db.commit()

    # --- writing -------------------------------------------------------------

    def record(self, record: dict[str, Any]) -> None:
        """Insert a job as it starts. Replaces on conflict so a retry is idempotent.

        Unknown keys are refused rather than dropped. The store's column names
        and the API's field names differ on purpose -- `limit` and `commit` are
        SQL keywords -- so passing the API's name is an easy mistake, and one
        that silently loses the value. It has already happened once.
        """
        row = dict(record)
        unknown = set(row) - set(COLUMNS)
        if unknown:
            raise ValueError(
                "Unknown job columns: "
                + ", ".join(sorted(unknown))
                + ". The store's names are not the API's; see app.public_fields."
            )
        for column in _JSON_COLUMNS:
            row[column] = json.dumps(row.get(column) or ([] if column == "events" else {}))
        values = [row.get(column) for column in COLUMNS]
        placeholders = ", ".join("?" for _ in COLUMNS)
        with self._lock:
            self._db.execute(
                f"INSERT OR REPLACE INTO jobs ({', '.join(COLUMNS)}) VALUES ({placeholders})",
                values,
            )
            self._db.commit()
        self.prune()

    def update(self, job_id: str, **fields: Any) -> None:
        """Patch a job in place. Unknown keys are dropped rather than raising.

        A job thread calls this in a `finally`; a typo there must not be the
        thing that loses the record of a change that reached a device.
        """
        allowed = {
            "status", "exit_code", "finished_at", "events", "output", "error", "change_id",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        for column in _JSON_COLUMNS:
            if column in updates:
                updates[column] = json.dumps(updates[column])
        assignments = ", ".join(f"{key} = ?" for key in updates)
        with self._lock:
            self._db.execute(
                f"UPDATE jobs SET {assignments} WHERE job_id = ?",
                [*updates.values(), job_id],
            )
            self._db.commit()

    # --- reading -------------------------------------------------------------

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return _decode(row) if row is not None else None

    def list(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        status: str = "",
        source: str = "",
        playbook: str = "",
    ) -> list[dict[str, Any]]:
        """Newest first. Filters are exact matches, never patterns.

        A `LIKE` here would be a small convenience and a way to ask the store
        questions about rows the caller was not offered.
        """
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (("status", status), ("source", source), ("playbook", playbook)):
            if value:
                clauses.append(f"{column} = ?")
                params.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params += [max(1, min(int(limit), 500)), max(0, int(offset))]
        with self._lock:
            rows = self._db.execute(
                f"SELECT * FROM jobs {where} ORDER BY started_at DESC LIMIT ? OFFSET ?",
                params,
            ).fetchall()
        return [_decode(row) for row in rows]

    def count(self, *, status: str = "", source: str = "", playbook: str = "") -> int:
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (("status", status), ("source", source), ("playbook", playbook)):
            if value:
                clauses.append(f"{column} = ?")
                params.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            row = self._db.execute(f"SELECT COUNT(*) AS n FROM jobs {where}", params).fetchone()
        return int(row["n"])

    def counts_by_status(self) -> dict[str, int]:
        with self._lock:
            rows = self._db.execute(
                "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"
            ).fetchall()
        return {str(row["status"]): int(row["n"]) for row in rows}

    def size_bytes(self) -> int:
        try:
            return os.path.getsize(self.path)
        except OSError:
            return 0

    # --- retention -----------------------------------------------------------

    def prune(self) -> int:
        """Drop what is older than the window, then what is over the row cap.

        Both, not either: an idle month must not keep a job forever, and a busy
        afternoon must not fill the volume. Returns the number of rows removed
        so a test can assert the policy rather than infer it.
        """
        days = _int_env(ENV_RETENTION_DAYS, DEFAULT_RETENTION_DAYS)
        cap = _int_env(ENV_RETENTION_MAX, DEFAULT_RETENTION_MAX)
        removed = 0
        with self._lock:
            if days > 0:
                cutoff = time.time() - (days * 86400)
                removed += self._db.execute(
                    "DELETE FROM jobs WHERE started_at < ?", (cutoff,)
                ).rowcount
            if cap > 0:
                removed += self._db.execute(
                    "DELETE FROM jobs WHERE job_id IN ("
                    "  SELECT job_id FROM jobs ORDER BY started_at DESC LIMIT -1 OFFSET ?"
                    ")",
                    (cap,),
                ).rowcount
            self._db.commit()
        return max(0, removed)

    def close(self) -> None:
        with self._lock:
            self._db.close()


def _decode(row: sqlite3.Row) -> dict[str, Any]:
    record = dict(row)
    for column in _JSON_COLUMNS:
        try:
            record[column] = json.loads(record.get(column) or "null")
        except (TypeError, ValueError):
            record[column] = [] if column == "events" else {}
    record["check_mode"] = bool(record.get("check_mode"))
    return record


# --- the process-wide store --------------------------------------------------

_store: JobStore | None = None
_store_lock = threading.Lock()


def store() -> JobStore:
    """Opened on first use rather than at import.

    The module has to be importable in the application's test suite, which has
    no `/var/lib/eagent-ansible` and no business creating one.
    """
    global _store
    with _store_lock:
        if _store is None:
            _store = JobStore(os.path.join(state_dir(), "jobs.sqlite"))
        return _store


def reset_for_tests(path: str | None = None) -> JobStore | None:
    """Point the module at a different file, or close what it has.

    Named for what it is. Production code never calls it.
    """
    global _store
    with _store_lock:
        if _store is not None:
            _store.close()
        _store = JobStore(path) if path else None
        return _store


def rows_to_public(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Shape rows for a listing: everything except the bulky per-job detail.

    `events` and `output` are what make a job record large, and a list of two
    hundred jobs does not need either. A caller that wants them asks for one job.
    """
    slim = []
    for row in rows:
        item = {key: value for key, value in row.items() if key not in ("events", "output")}
        item["event_count"] = len(row.get("events") or [])
        slim.append(item)
    return slim
