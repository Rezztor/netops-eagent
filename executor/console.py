"""The break-glass console: a second, read-only door onto the executor.

The main console lives in the application, behind its login and its RBAC. This
one exists for when the application is the thing that is broken -- and for a
customer's own tooling, which wants the API rather than a page.

Three deliberate narrowings, because a published port on the workload holding
device credentials is the riskiest thing in this design (ADR 0032 says so in its
consequences, and says it was asked for deliberately).

**Off by default.** No `REZZTOR_ANSIBLE_PUBLIC_PORT`, no listener. An executor
that published a port because someone forgot to turn it off is not a thing that
should be possible.

**Loopback unless told otherwise.** Binding to `0.0.0.0` is a second, separate
decision. The default reaches `docker exec` and a local port-forward and
nothing else, which covers break-glass without putting anything on a network.

**Read-only.** The plan sketched a button here that could run a catalogue read.
It is not built, and the reason is worth recording: browsers attach cached Basic
credentials to cross-origin form posts, so any page anywhere could have made this
executor talk to a device. Reads are free by design and the impact would be
small, but "small" is not the standard for the one workload that can reach the
estate. Running a job needs the API and a bearer token, which a browser cannot be
tricked into sending. There is no POST route in this file, and that is the
control -- not a check inside one.
"""

from __future__ import annotations

import os
import threading
from typing import Any

from flask import Flask, Response, render_template, request

from executor import app as api
from executor import facts, scheduling, store

ENV_PUBLIC_PORT = "REZZTOR_ANSIBLE_PUBLIC_PORT"
ENV_PUBLIC_BIND = "REZZTOR_ANSIBLE_PUBLIC_BIND"

#: Loopback. Reaching this from another host is a separate decision an operator
#: has to make on purpose, and one that needs TLS in front of it -- Basic
#: credentials travel in the clear on every request.
DEFAULT_BIND = "127.0.0.1"

console = Flask(__name__, template_folder="templates")


def public_port() -> int:
    try:
        return int(os.getenv(ENV_PUBLIC_PORT) or 0)
    except ValueError:
        return 0


def _unauthorised() -> Response:
    return Response(
        "This console needs the executor's administration token as the password.\n",
        401,
        {"WWW-Authenticate": 'Basic realm="eagent-ansible"'},
    )


@console.before_request
def _require_the_admin_token() -> Response | None:
    """HTTP Basic, with the administration token as the password.

    Basic rather than a login form and a cookie: a browser handles it natively,
    so there is no session to fix, no cookie to scope and no CSRF surface to
    reason about. The username is ignored. On a read-only surface this is the
    smallest thing that is correct.

    It fails closed on the same terms as the API -- no token configured means no
    console, not an open one.
    """
    expected, _why = api._secret(api.ENV_ADMIN_TOKEN)
    if not expected:
        return _unauthorised()
    auth = request.authorization
    if not auth or not auth.password:
        return _unauthorised()
    if not api._same(auth.password, expected):
        return _unauthorised()
    return None


@console.get("/")
def overview() -> Any:
    try:
        entries = sorted(api.catalogue.load())
        catalogue_error = ""
    except Exception as error:  # noqa: BLE001 - reported on the page, not raised
        entries, catalogue_error = [], str(error)

    jobs = store.store()
    return render_template(
        "overview.html",
        commit=api.catalogue_commit(),
        entries=entries,
        catalogue_error=catalogue_error,
        collections=sorted(facts.installed_collections().items()),
        ansible_version=facts.ansible_version(),
        limits=scheduling.limits(),
        depth=api._scheduler.depth(),
        counts=jobs.counts_by_status(),
        # Through the API's mapper, not straight off the store: the templates
        # speak the API's field names, and Jinja renders an unknown one as empty
        # rather than as an error.
        jobs=[api.public_fields(row) for row in store.rows_to_public(jobs.list(limit=100))],
    )


@console.get("/jobs/<job_id>")
def job(job_id: str) -> Any:
    stored = store.store().get(job_id)
    if stored is None:
        return render_template("missing.html", job_id=job_id), 404
    record = api.public_fields(stored)
    commands = [
        line
        for event in record.get("events") or []
        for line in (event.get("commands") or [])
    ]
    return render_template("job.html", job=record, commands=commands)


def serve_in_background() -> threading.Thread | None:
    """Start the console if a port was configured. Returns None if not.

    A thread rather than a second process: the console reads the same job store
    and the same scheduler, and a second process would need its own connection to
    a SQLite file the first one is writing.
    """
    port = public_port()
    if port <= 0:
        return None

    from waitress import serve

    bind = (os.getenv(ENV_PUBLIC_BIND) or DEFAULT_BIND).strip() or DEFAULT_BIND
    thread = threading.Thread(
        target=lambda: serve(console, host=bind, port=port, threads=2),
        daemon=True,
        name="ansible-console",
    )
    thread.start()
    return thread
