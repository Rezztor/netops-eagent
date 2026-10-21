"""Readiness: are the things behind this working, right now.

`/api/health` stays exactly as it is. ADR 0022 argues that a liveness probe
gating on a dependency turns one outage into a total one -- including the
administration interface an operator needs precisely then -- and compose's
healthcheck points at it. That argument covers startup, and startup is already
sound: migrations run before the server binds and `prepare_database()` raises
rather than warns, so a 200 there does mean the schema is current.

It does not cover the hours afterwards. If PostgreSQL stops at three in the
morning, `/api/health` keeps answering 200, the container stays `healthy`, and
`restart: unless-stopped` does nothing -- it reacts to a process exiting, not to
one that is up and failing every request. So this is a second endpoint with a
different consequence: a monitor scrapes it and pages somebody, while nothing
restarts the container underneath them.

**Unauthenticated, and that is the whole point.** The first version required a
session, which read correctly and did not work: authenticating needs the
database, so with PostgreSQL down the request failed in the auth layer with a
500 before this handler ran at all. A readiness check that cannot answer while
its dependency is down is a readiness check that answers only when it is not
needed. Found by stopping the container and watching it return the wrong thing.

Being unauthenticated is what bounds the body. Each check reports whether it
passed and how long it took, and nothing else -- no exception text, no host, no
pool sizes. "The database is not answering" is what a 503 already says.
"""

from __future__ import annotations

import json
import time

from helpers.api import ApiHandler, Request, Response


class Ready(ApiHandler):

    @classmethod
    def requires_auth(cls) -> bool:
        return False

    @classmethod
    def requires_csrf(cls) -> bool:
        return False

    @classmethod
    def get_methods(cls) -> list[str]:
        return ["GET", "POST"]

    async def process(self, input: dict, request: Request) -> dict | Response:
        checks = {"database": _timed(_database)}
        ready = all(check["ok"] for check in checks.values())

        return Response(
            response=json.dumps({"ok": ready, "checks": checks}),
            # 503 so a monitor can act on the status line alone.
            status=200 if ready else 503,
            mimetype="application/json",
        )


def _timed(check) -> dict:
    """Run a check and report only whether it passed, and how long it took.

    Every exception is caught, including the ones that mean the dependency is
    gone: this endpoint exists to answer during a failure, so raising would be
    the one behaviour it cannot have.
    """
    started = time.monotonic()
    try:
        check()
        ok = True
    except Exception:  # noqa: BLE001 - reporting the failure is the job
        ok = False
    return {"ok": ok, "ms": round((time.monotonic() - started) * 1000, 1)}


def _database() -> None:
    """A query through the pool that serves requests, not a fresh connection.

    A new connection proves the server is up and says nothing about a pool that
    has run out -- which looks identical from outside and is the more likely of
    the two failures.
    """
    from helpers import db

    with db.transaction(db.NO_SCOPE) as conn:
        conn.execute("SELECT 1").fetchone()
