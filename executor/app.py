"""The Ansible executor's job API.

The only workload in the deployment permitted to reach the management network,
and the only place `ansible-playbook` runs. It accepts a *name*, a *commit* and
*validated variables* -- never playbook content, never a device command, never a
credential. That refusal is the whole security design (ADR 0030), and
`plugins/_ea_ansible/helpers/job_schema.py` is where the field list lives so
that both sides of the call validate against one definition rather than two.

Deliberately small: no shell-out from the API, one job at a time, bounded output.
History lives in `store.py` -- see that module for why the executor keeps a record
of its own alongside the application's audit trail rather than instead of it.
"""

from __future__ import annotations

import hmac
import os
import shutil
import socket
import tempfile
import time
import uuid
from typing import Any, Callable

from flask import Flask, jsonify, request

from executor import backups, credentials, facts, inventory, networks, redaction, scheduling, secret_env, store
from plugins._ea_ansible.helpers import catalogue, job_schema, network_list

app = Flask(__name__)

# --- configuration, all of it from the environment ---------------------------

ENV_TOKEN = "REZZTOR_ANSIBLE_EXECUTOR_TOKEN"
ENV_ADMIN_TOKEN = "REZZTOR_ANSIBLE_ADMIN_TOKEN"
ENV_SOCKET = "REZZTOR_ANSIBLE_SOCKET"
ENV_COMMIT = "REZZTOR_ANSIBLE_CATALOGUE_COMMIT"
ENV_INVENTORY = "REZZTOR_ANSIBLE_INVENTORY"
ENV_TIMEOUT = "REZZTOR_ANSIBLE_JOB_TIMEOUT"
ENV_MAX_DEVICES = "REZZTOR_ANSIBLE_MAX_DEVICES"
ENV_OUTPUT_LIMIT = "REZZTOR_ANSIBLE_OUTPUT_LIMIT"

DEFAULT_TIMEOUT = 300
DEFAULT_MAX_DEVICES = 10
DEFAULT_OUTPUT_LIMIT = 20000
DEFAULT_SSH_PORT = 22
DEFAULT_HTTPS_PORT = 443  # FortiGate and Palo Alto are reached on their management HTTPS port
PREFLIGHT_HOST_CAP = 10
PREFLIGHT_TCP_TIMEOUT = 3.0

#: A limit is a comma-separated list of host names, and nothing else: no
#: pattern (`*`, `!`, `&`, `[]`, `:`) and nothing that could mean something to a
#: shell. A pattern is one "device" to the cap and can mean the whole estate.
LIMIT_ALLOWED = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,_-"
)


def _int_env(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name) or default))
    except ValueError:
        return default


def catalogue_commit() -> str:
    """What this image's catalogue is pinned to.

    `unpinned` is not a failure mode to hide -- an image built without a commit
    says so, and the caller has to send `unpinned` deliberately rather than
    having the check quietly skipped.
    """
    return (os.getenv(ENV_COMMIT) or "unpinned").strip()


# --- who runs now, who waits -------------------------------------------------

_STARTED_AT = time.time()


def _run_and_record_entry(job_id: str, *args: Any) -> None:
    """The scheduler's callable. Split out so the scheduler can be built before
    `_run_and_record` is defined, and so it stays the only thing the scheduler
    knows about this module."""
    _run_and_record(job_id, *args)


_scheduler = scheduling.Scheduler(_run_and_record_entry)


# --- authentication ----------------------------------------------------------
#
# Two tokens, and neither is accepted where the other belongs.
#
# The **job token** runs catalogue entries. The agent holds it, and it is the
# one that travels: it is in the application's usr/.env, it is sent on every
# read, and it is the token that a compromised application hands over.
#
# The **admin token** writes the inventory and the credentials. Nothing in the
# agent's path has it. That separation is the point: a compromised agent can run
# the reviewed playbooks it was always allowed to run, and cannot rewrite the
# inventory that decides which devices those playbooks reach.
#
# Both fail closed. An unconfigured admin token means the configuration
# endpoints refuse everything, which is what an existing deployment should do
# until someone deliberately turns them on.

def _same(presented: str, expected: str) -> bool:
    """Constant-time, on bytes: `hmac.compare_digest` raises on a str that is not ASCII, and a header can hold one."""
    return hmac.compare_digest(presented.encode("utf-8", "replace"), expected.encode("utf-8", "replace"))


def _presented() -> str:
    header = request.headers.get("Authorization", "")
    prefix = "Bearer "
    return header[len(prefix):].strip() if header.startswith(prefix) else ""


def _secret(name: str) -> tuple[str, str]:
    """`(value, "")`, or `("", why)`: a misconfigured secret refuses, it never opens."""
    try:
        return secret_env.get(name), ""
    except secret_env.SecretConfigError as error:
        return "", str(error)


def _authorised() -> tuple[bool, str]:
    """Fails closed. No configured token means no requests, not open requests."""
    expected, why = _secret(ENV_TOKEN)
    if why:
        return False, why
    if not expected:
        return False, "The executor has no token configured and accepts nothing."

    presented = _presented()
    if not presented:
        return False, "A bearer token is required."
    if not _same(presented, expected):
        return False, "Token rejected."
    return True, ""


def _authorised_admin() -> tuple[bool, str]:
    """The configuration plane. The job token is not accepted here."""
    expected, why = _secret(ENV_ADMIN_TOKEN)
    if why:
        return False, why
    if not expected:
        return False, (
            "This executor has no administration token configured, so its "
            f"inventory and credentials cannot be changed. Set {ENV_ADMIN_TOKEN}."
        )

    job_token, why = _secret(ENV_TOKEN)
    if why:
        return False, why
    if job_token and _same(expected, job_token):
        # Refused rather than allowed, because the deployment that set them the
        # same has not made a smaller mistake than the one that set no admin
        # token -- it has made a bigger one that looks like it works.
        return False, (
            "The administration token and the job token are the same value, which "
            "would give the agent the ability to rewrite its own inventory. "
            "Set them to different values."
        )

    presented = _presented()
    if not presented:
        return False, "A bearer token is required."
    if not _same(presented, expected):
        return False, "Token rejected."
    return True, ""


# --- preflight (read-only; no job) -------------------------------------------
#
# A TCP connect from inside the executor, plus what the inventory already knows
# about a host. Used before an approval card is shown so a missing credential or
# an unreachable address fails as a plain reason rather than a card that can
# never run. Never returns a secret. Tests replace `_tcp_connect`.

def tcp_connect(address: str, port: int, timeout: float = PREFLIGHT_TCP_TIMEOUT) -> str:
    """The outcome of a TCP connect to address:port: "ok", "refused", or "silent" (timeout, no route)."""
    try:
        with socket.create_connection((address, int(port)), timeout=timeout):
            return "ok"
    except ConnectionRefusedError:
        return "refused"
    except OSError:
        return "silent"


#: Tests assign this. Production leaves it as the real connect.
_tcp_connect: Callable[[str, int], str] = tcp_connect


def _host_preflight(name: str, document: dict[str, Any], known_creds: set[str]) -> dict[str, Any]:
    """One host's preflight row. Secrets never appear here."""
    entry = (document.get("hosts") or {}).get(name)
    if entry is None:
        return {
            "in_inventory": False,
            "address": "",
            "port": DEFAULT_SSH_PORT,
            "reachable": False,
            "reason": "",
            "network": "",
            "routed": False,
            "credential": "",
            "credential_present": False,
            "network_os": "",
            "connection": "",
        }

    merged: dict[str, Any] = {}
    credential = ""
    for group in (document.get("groups") or {}).values():
        if name not in (group.get("hosts") or []):
            continue
        merged.update(group.get("vars") or {})
        named = str(group.get("credential") or "").strip()
        if named:
            credential = named
    merged.update(entry.get("vars") or {})

    address = str(entry.get("ansible_host") or "").strip()
    over_https = str(merged.get("ansible_network_os") or "").rsplit(".", 1)[-1] in inventory.API_FROM_EXECUTOR
    try:
        port = int(merged.get("ansible_port") or (DEFAULT_HTTPS_PORT if over_https else DEFAULT_SSH_PORT))
    except (TypeError, ValueError):
        port = DEFAULT_HTTPS_PORT if over_https else DEFAULT_SSH_PORT

    outcome = _tcp_connect(address, port) if address else "silent"
    return {
        "in_inventory": True,
        "address": address,
        "port": port,
        "reachable": outcome == "ok",
        **network_list.classify(address, outcome, networks.load()),
        "credential": credential,
        "credential_present": bool(credential) and credential in known_creds,
        "network_os": str(merged.get("ansible_network_os") or ""),
        "connection": str(merged.get("ansible_connection") or ""),
    }


@app.post("/v1/preflight")
def preflight():
    """Inventory facts + a TCP reachability check. Job token. No job created."""
    ok, why = _authorised()
    if not ok:
        return jsonify({"error": why}), 401

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "A preflight request must be a JSON object."}), 400

    hosts = body.get("hosts")
    if not isinstance(hosts, list):
        return jsonify({"error": "hosts must be a list of host names."}), 400
    if len(hosts) > PREFLIGHT_HOST_CAP:
        return jsonify(
            {
                "error": (
                    f"A preflight may name at most {PREFLIGHT_HOST_CAP} hosts; "
                    f"this one names {len(hosts)}."
                )
            }
        ), 400

    names: list[str] = []
    for host in hosts:
        if not isinstance(host, str) or not host.strip():
            return jsonify({"error": "Each host must be a non-empty string."}), 400
        names.append(host.strip())

    try:
        document = inventory.store().load()
        known = credentials.store().names()
    except (inventory.InventoryError, credentials.CredentialError) as error:
        return jsonify({"error": str(error)}), 503

    return jsonify(
        {"results": {name: _host_preflight(name, document, known) for name in names}}
    ), 200


@app.get("/v1/networks")
def get_networks():
    """The appliance's network list, read-only, for the application. Job token. No secret is in it."""
    ok, why = _authorised()
    if not ok:
        return jsonify({"error": why}), 401
    return jsonify(networks.load()), 200


# --- configuration backups ---------------------------------------------------
#
# Job token, like a read. No route here returns raw configuration text: it is encrypted at
# rest (executor/backups.py) and the only raw export is `ea-ansible backup export`.

def _backup_refused(error: Exception):
    return jsonify({"error": str(error)}), 503 if isinstance(error, credentials.CredentialError) else 400


def _run_backup_playbook(private_dir: str, out_dir: str, limit: str) -> int:
    """The internal playbook. Its output is not read; the text comes back as files in `out_dir`."""
    import ansible_runner  # noqa: PLC0415 - see _run_playbook

    run = ansible_runner.run(
        private_data_dir=private_dir,
        playbook=backups.PLAYBOOK,
        inventory=_inventory_for(private_dir),
        extravars={"eagent_backup_dir": out_dir},
        cmdline=f"--limit {limit}",
        quiet=True,
        timeout=_int_env(ENV_TIMEOUT, DEFAULT_TIMEOUT),
    )
    return run.rc


@app.post("/v1/backups")
def backups_run():
    ok, why = _authorised()
    if not ok:
        return jsonify({"error": why}), 401
    body = request.get_json(silent=True)
    hosts = body.get("hosts") if isinstance(body, dict) else None
    if not isinstance(hosts, list) or not hosts or len(hosts) > backups.HOST_CAP:
        return jsonify({"error": f"hosts must be a list of 1 to {backups.HOST_CAP} host names."}), 400
    if not all(backups.valid_host(host) for host in hosts):
        return jsonify({"error": "Each host must be a usable host name."}), 400
    try:
        return jsonify({"results": backups.take(list(dict.fromkeys(hosts)), _run_backup_playbook)}), 200
    except (backups.BackupError, credentials.CredentialError, inventory.InventoryError) as error:
        return _backup_refused(error)


@app.get("/v1/backups")
def backups_list():
    ok, why = _authorised()
    if not ok:
        return jsonify({"error": why}), 401
    try:
        return jsonify({"backups": backups.store().list(request.args.get("host", "", type=str) or None)}), 200
    except backups.BackupError as error:
        return _backup_refused(error)


@app.get("/v1/backups/<host>/diff")
def backups_diff(host: str):
    ok, why = _authorised()
    if not ok:
        return jsonify({"error": why}), 401
    try:
        return jsonify(backups.store().diff(host, request.args.get("from", ""), request.args.get("to", ""))), 200
    except (backups.BackupError, credentials.CredentialError) as error:
        return _backup_refused(error)


@app.get("/v1/backups/<host>/<backup_id>")
def backups_text(host: str, backup_id: str):
    ok, why = _authorised()
    if not ok:
        return jsonify({"error": why}), 401
    try:
        return jsonify({"text": backups.store().text(host, backup_id)}), 200
    except (backups.BackupError, credentials.CredentialError) as error:
        return _backup_refused(error)


# --- endpoints ---------------------------------------------------------------

@app.get("/healthz")
def healthz():
    """Liveness, the catalogue commit a caller must match, and what is installed.

    Still unauthenticated, because a readiness probe cannot hold a token. That
    is the constraint everything below has to be safe under: versions, counts
    and the catalogue's entry names -- nothing about a device, nothing about a
    job, and no secret.
    """
    try:
        entries = sorted(catalogue.load())
        loaded = True
        detail = ""
    except catalogue.CatalogueError as exc:
        entries, loaded, detail = [], False, str(exc)

    return jsonify(
        {
            "status": "ok" if loaded else "catalogue_invalid",
            "catalogue_commit": catalogue_commit(),
            "catalogue_entries": entries,
            "catalogue_error": detail,
            "busy": _scheduler.busy(),
            "ansible_version": facts.ansible_version(),
            "collections": facts.installed_collections(),
            "uptime_seconds": round(time.time() - _STARTED_AT, 1),
            "history": _history_summary(),
            "queue": _scheduler.depth(),
            # What this executor enforces, so a settings screen can show the
            # number that is actually applied rather than one of its own.
            "limits": scheduling.limits(),
        }
    ), (200 if loaded else 503)


def _history_summary() -> dict[str, Any]:
    """Counts and size, never rows.

    /healthz has no token. A job's playbook name is a description of the estate;
    the number of jobs is not.
    """
    try:
        jobs = store.store()
        return {
            "jobs": sum(jobs.counts_by_status().values()),
            "by_status": jobs.counts_by_status(),
            "store_bytes": jobs.size_bytes(),
        }
    except Exception as exc:  # noqa: BLE001 - an unreadable store is reportable, not fatal
        return {"error": str(exc)}


@app.get("/jobs")
def list_jobs():
    """History, newest first.

    Filters are exact matches handled by the store; a substring search here
    would let a caller ask which playbook names exist by probing, which is a
    description of the estate given away one query at a time.
    """
    ok, why = _authorised()
    if not ok:
        return jsonify({"error": why}), 401

    filters = {
        "status": request.args.get("status", "", type=str),
        "source": request.args.get("source", "", type=str),
        "playbook": request.args.get("playbook", "", type=str),
    }
    jobs = store.store()
    rows = jobs.list(
        limit=request.args.get("limit", 50, type=int),
        offset=request.args.get("offset", 0, type=int),
        **filters,
    )
    return jsonify(
        {
            "jobs": [public_fields(row) for row in store.rows_to_public(rows)],
            "total": jobs.count(**filters),
        }
    ), 200


@app.post("/jobs")
def submit_job():
    ok, why = _authorised()
    if not ok:
        return jsonify({"error": why}), 401

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "A job must be a JSON object."}), 400

    # Field-level refusal first: content, commands and credentials are rejected
    # by name before anything looks at what they contain.
    try:
        job_schema.validate_job_fields(body)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        entries = catalogue.load()
    except catalogue.CatalogueError as exc:
        return jsonify({"error": f"The catalogue is not loadable: {exc}"}), 503

    try:
        entry = catalogue.resolve(body.get("playbook_name"), entries)
    except catalogue.UnknownEntry as exc:
        return jsonify({"error": str(exc)}), 404

    supplied_commit = str(body.get("commit") or "").strip()
    if supplied_commit != catalogue_commit():
        return jsonify(
            {
                "error": (
                    "This job was built against a different catalogue than the one "
                    "installed here. Re-draft it against the current catalogue."
                ),
                "executor_catalogue_commit": catalogue_commit(),
            }
        ), 409

    try:
        variables = entry.validate_variables(body.get("variables"))
    except catalogue.VariableRejected as exc:
        return jsonify({"error": str(exc)}), 400

    limit = str(body.get("limit") or "").strip()
    if set(limit) - LIMIT_ALLOWED:
        return jsonify({"error": "The device selector contains characters that are not allowed."}), 400
    devices = [part for part in limit.split(",") if part.strip()]
    if any(not inventory.NAME.match(name) or name.lower() in inventory.RESERVED for name in devices):
        return jsonify({"error": "The device selector must list device names, separated by commas."}), 400
    if not devices and "any" not in entry.platforms:
        # Without a selector `hosts: all` is every device in the inventory, whatever the cap below says.
        return jsonify({"error": f"'{entry.name}' runs against devices, so the job must name them."}), 400
    max_devices = _int_env(ENV_MAX_DEVICES, DEFAULT_MAX_DEVICES)
    if len(devices) > max_devices:
        return jsonify(
            {"error": f"A job may target at most {max_devices} devices; this one names {len(devices)}."}
        ), 400
    try:
        stored = inventory.store().load()["hosts"]
    except inventory.InventoryError as exc:
        return jsonify({"error": str(exc)}), 503
    # A group name would pass the cap as one device and run against all of its members. Hosts and groups share one
    # namespace (inventory.validate), so "is a stored host" excludes every group. ponytail: an executor still on a
    # mounted inventory file (no managed hosts) cannot tell a group from a host; it gets the name check above only.
    unknown = [name for name in devices if stored and name not in stored]
    if unknown:
        return jsonify({"error": f"Not a device in the executor's inventory: {', '.join(unknown)}."}), 400

    check_mode = body.get("check_mode", True)
    if not isinstance(check_mode, bool):
        return jsonify({"error": "check_mode must be true or false."}), 400
    if not check_mode and entry.kind == "read":
        # A read has nothing to apply, and letting it run "for real" would be a
        # silent no-op that looks like an execution.
        return jsonify({"error": f"'{entry.name}' is a read; it only runs in check mode."}), 400

    job_id = uuid.uuid4().hex
    store.store().record(
        {
            "job_id": job_id,
            # Every job that arrives through this API came from the application.
            # A `console` or `cli` row is written by something else entirely.
            "source": "agent",
            "actor": str(body.get("actor") or "")[:200],
            "playbook": entry.name,
            "catalogue_commit": catalogue_commit(),
            # Structural secrets only. A catalogue variable can legitimately be
            # a community string or a device password, and that is exactly what
            # the approver has to be able to check.
            "variables": redaction.redact_structure(variables, keep_operational=True),
            "limit_pattern": limit,
            "check_mode": check_mode,
            "status": "queued",
            "exit_code": None,
            "started_at": time.time(),
            "finished_at": None,
            "events": [],
            "output": "",
            "error": "",
            "change_id": str(body.get("change_id") or "")[:200],
        }
    )

    # Recorded before admission so a refusal is not the one outcome that leaves
    # no trace. A job refused for rate is still something an operator wants to
    # see when they ask why nothing ran.
    try:
        status = _scheduler.submit(job_id, (entry, variables, limit, check_mode))
    except scheduling.Refused as refusal:
        store.store().update(job_id, status="failed", error=str(refusal), finished_at=time.time())
        return jsonify({"error": str(refusal)}), 429

    return jsonify({"job_id": job_id, "status": status}), 202


@app.get("/jobs/<job_id>")
def job_status(job_id: str):
    ok, why = _authorised()
    if not ok:
        return jsonify({"error": why}), 401

    record = store.store().get(job_id)
    if record is None:
        return jsonify({"error": "No such job."}), 404
    return jsonify(public_fields(record)), 200


#: The store's column names and the API's field names are not the same, and
#: deliberately so: `limit` and `commit` are both SQL keywords, and renaming the
#: API to suit SQLite would have made every caller change for a storage detail.
_COLUMN_TO_FIELD = {
    "playbook": "playbook_name",
    "catalogue_commit": "commit",
    "limit_pattern": "limit",
}


def public_fields(record: dict[str, Any]) -> dict[str, Any]:
    """Store columns -> API field names.

    Not private any more: the break-glass console renders the same rows, and
    when it reached into the store directly it silently showed a blank playbook
    column and "all" for every target. Jinja renders an unknown name as empty,
    so nothing said anything -- which is why there is one mapper and both
    callers use it.
    """
    return {_COLUMN_TO_FIELD.get(key, key): value for key, value in record.items()}


# --- the configuration plane -------------------------------------------------

@app.get("/credentials")
def list_credentials():
    """Names, kinds and fingerprints. There is no endpoint that returns a secret.

    A fingerprint answers the only question an administrator legitimately has
    about a stored credential -- is the value I am holding the value that is
    loaded -- without being the value.
    """
    ok, why = _authorised_admin()
    if not ok:
        return jsonify({"error": why}), 401
    try:
        return jsonify({"credentials": credentials.store().list()}), 200
    except credentials.CredentialError as error:
        return jsonify({"error": str(error)}), 503


@app.put("/credentials/<name>")
def put_credential(name: str):
    ok, why = _authorised_admin()
    if not ok:
        return jsonify({"error": why}), 401

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "A credential must be a JSON object."}), 400
    try:
        stored = credentials.store().put(
            name,
            kind=str(body.get("kind") or ""),
            secret=str(body.get("secret") or ""),
            username=str(body.get("username") or ""),
        )
    except credentials.CredentialError as error:
        return jsonify({"error": str(error)}), 400
    return jsonify(stored), 200


@app.delete("/credentials/<name>")
def delete_credential(name: str):
    ok, why = _authorised_admin()
    if not ok:
        return jsonify({"error": why}), 401
    if not credentials.store().delete(name):
        return jsonify({"error": f"No credential named '{name}' is stored here."}), 404
    return jsonify({"deleted": name}), 200


@app.get("/inventory")
def get_inventory():
    """The document, plus which credentials it names that are not stored.

    Reported rather than refused at save time: an administrator who loads an
    inventory before its credentials is doing them in a reasonable order.
    """
    ok, why = _authorised_admin()
    if not ok:
        return jsonify({"error": why}), 401
    try:
        document = inventory.store().load()
        missing = inventory.unresolved_credentials(document, credentials.store().names())
    except (inventory.InventoryError, credentials.CredentialError) as error:
        return jsonify({"error": str(error)}), 503
    return jsonify({"inventory": document, "unresolved_credentials": missing}), 200


@app.put("/inventory")
def put_inventory():
    ok, why = _authorised_admin()
    if not ok:
        return jsonify({"error": why}), 401

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "An inventory must be a JSON object."}), 400
    try:
        document = inventory.store().save(body.get("inventory", body))
        missing = inventory.unresolved_credentials(document, credentials.store().names())
    except (inventory.InventoryError, credentials.CredentialError) as error:
        return jsonify({"error": str(error)}), 400
    return jsonify({"inventory": document, "unresolved_credentials": missing}), 200


# --- running a job -----------------------------------------------------------

def _run_and_record(
    job_id: str, entry: Any, variables: dict[str, Any], limit: str, check_mode: bool
) -> None:
    # The thread owns the transition out of `queued`, rather than the request
    # that submitted it. A job can start and finish before `submit()` returns,
    # and a caller writing "running" afterwards would overwrite the outcome with
    # the state it had two lines ago.
    try:
        store.store().update(job_id, status="running")

        try:
            result = _run_playbook(entry, variables, limit, check_mode)
        except Exception as exc:  # noqa: BLE001 - a job must never kill the service
            result = {"status": "failed", "exit_code": None, "output": "", "events": [], "error": str(exc)}

        store.store().update(
            job_id, finished_at=time.time(), **redaction.redact_job_result(result)
        )
    finally:
        # Whatever happened, the record's own writes included: a job that failed
        # still has to let the next one start, and a scheduler that leaked a slot
        # per failure would wedge the executor after `max_concurrent_jobs` bad jobs.
        _scheduler.finished()


def _run_playbook(
    entry: Any, variables: dict[str, Any], limit: str, check_mode: bool
) -> dict[str, Any]:
    """Invoke ansible-runner and keep the structured events.

    Imported lazily so this module can be imported -- and its request handling
    tested -- without Ansible installed. The application's venv has no
    ansible-runner and should not grow one.
    """
    import ansible_runner  # noqa: PLC0415 - see docstring

    collected: list[dict[str, Any]] = []

    def on_event(event: dict[str, Any]) -> bool:
        data = (event or {}).get("event_data") or {}
        result = data.get("res") or {}
        if not isinstance(result, dict):
            return True
        # The three keys the approval view is built from. `commands` is what a
        # resource module would push; `rendered` is the offline equivalent.
        keep = {
            key: result[key]
            for key in ("commands", "rendered", "diff", "changed")
            if key in result
        }
        if keep:
            keep["task"] = data.get("task")
            keep["host"] = data.get("host")
            collected.append(keep)
        return True

    extra_vars = dict(variables)
    cmdline_parts = []
    if limit:
        cmdline_parts += ["--limit", limit]
    if check_mode:
        cmdline_parts += ["--check", "--diff"]

    private_data_dir = _fresh_private_data_dir()
    try:
        return _invoke(
            ansible_runner, private_data_dir, entry, extra_vars, cmdline_parts, on_event, collected
        )
    finally:
        # A job leaves nothing behind. The directory holds the playbook's
        # artifacts, which can include device output.
        shutil.rmtree(private_data_dir, ignore_errors=True)


def _inventory_for(private_data_dir: str) -> str | None:
    """The managed inventory if there is one, the mounted file otherwise.

    A deployment that predates the managed inventory keeps working: an empty
    document falls through to REZZTOR_ANSIBLE_INVENTORY rather than rendering an
    inventory with no hosts in it, which would run against nothing and report
    success.

    The rendered file carries resolved credentials, which is why it is written
    into the job's own directory -- tmpfs, removed when the job ends, never
    shared with another job -- and never beside the document it came from.
    """
    document = inventory.store().load()
    if document["hosts"]:
        return inventory.store().render(private_data_dir)
    return os.getenv(ENV_INVENTORY) or None


def _fresh_private_data_dir() -> str:
    """ansible-runner requires this directory to already exist.

    It does not create it, and the failure -- "private_data_dir path is either
    invalid or does not exist" -- arrives at the first real job rather than at
    startup. One directory per job, so two jobs cannot read each other's
    artifacts even if the concurrency cap is ever raised.
    """
    root = os.getenv("REZZTOR_ANSIBLE_RUNDIR", "/tmp/ansible-runs")
    os.makedirs(root, exist_ok=True)
    return tempfile.mkdtemp(prefix="job-", dir=root)


def _invoke(
    ansible_runner: Any,
    private_data_dir: str,
    entry: Any,
    extra_vars: dict[str, Any],
    cmdline_parts: list[str],
    on_event: Any,
    collected: list[dict[str, Any]],
) -> dict[str, Any]:
    run = ansible_runner.run(
        private_data_dir=private_data_dir,
        playbook=catalogue.playbook_path(entry),
        inventory=_inventory_for(private_data_dir),
        extravars=extra_vars,
        cmdline=" ".join(cmdline_parts) if cmdline_parts else None,
        event_handler=on_event,
        quiet=True,
        timeout=_int_env(ENV_TIMEOUT, DEFAULT_TIMEOUT),
    )

    output_limit = _int_env(ENV_OUTPUT_LIMIT, DEFAULT_OUTPUT_LIMIT)
    stdout = ""
    try:
        stdout = run.stdout.read() if run.stdout else ""
    except Exception:  # noqa: BLE001 - output is a convenience, events are the record
        stdout = ""

    return {
        "status": "succeeded" if run.rc == 0 else "failed",
        "exit_code": run.rc,
        "output": stdout[-output_limit:],
        "events": collected,
    }


def listen_options() -> dict[str, Any]:
    """Where waitress listens. With `REZZTOR_ANSIBLE_SOCKET` set it is that Unix
    socket and nothing else -- no TCP listener, so there is no network path to the
    tokens' endpoints. Mode 0660: the application shares the socket's group (ADR 0051).
    """
    path = (os.getenv(ENV_SOCKET) or "").strip()
    if path:
        return {"unix_socket": path, "unix_socket_perms": "660"}
    return {
        "host": os.getenv("REZZTOR_ANSIBLE_BIND", "0.0.0.0"),
        "port": int(os.getenv("REZZTOR_ANSIBLE_PORT", "8500")),
    }


def main() -> None:
    """Serve. A function, so that the image can start it from compiled code,
    which `python -m` cannot run (docker/compile_python.py)."""
    from waitress import serve

    # The break-glass console, if a port was configured. Off by default, and
    # loopback unless an operator binds it somewhere else on purpose -- see
    # executor/console.py for why each of those is a separate decision.
    from executor import console as break_glass

    break_glass.serve_in_background()
    backups.serve_in_background(_run_backup_playbook)

    serve(app, threads=4, **listen_options())


if __name__ == "__main__":
    main()
