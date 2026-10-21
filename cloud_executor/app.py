"""The cloud executor's API (build contract, "Cloud executor API"; ADR 0061 to 0066).

Reached over a Unix socket that only the application and this container mount. It takes a template *name* with typed
values, or reviewed code, and a workspace; it never takes a credential and never returns one. Two bearer tokens, and
neither works where the other belongs: the **job token** plans and applies, the **admin token** edits the guard rails
and makes the keys. The same value for both is refused (ADR 0036), and no token configured refuses everything.

There is no route that removes, stops or restarts anything in Azure, and `/v1/plan` has no such mode. `/v1/plan-set`
plans an ordered set of template steps; a set is applied step by step through `/v1/apply`, one change id each.
"""

from __future__ import annotations

import hmac
import os
import re
import threading
import time
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from flask import Flask, jsonify, request

from cloud_executor import actions, adopt, arm, keys, rails as rails_mod, refresh, rolecheck, runner, store, templates
from executor import secret_env

app = Flask(__name__)

ENV_TOKEN = "REZZTOR_CLOUD_JOB_TOKEN"
ENV_ADMIN_TOKEN = "REZZTOR_CLOUD_ADMIN_TOKEN"
ENV_SOCKET = "REZZTOR_CLOUD_SOCKET"
ENV_MIRROR = "REZZTOR_CLOUD_MIRROR"
ENV_VERSION = "REZZTOR_CLOUD_VERSION"
ENV_ACTIONS_COMMIT = "REZZTOR_CLOUD_ACTIONS_COMMIT"
MAX_QUEUED = 5
MAX_PER_MINUTE = 6
LOCK_WAIT = 600
DIGEST = re.compile(r"[0-9a-f]{64}")


# ------------------------------------------------------------------------------------------ authentication

def _secret(name: str) -> tuple[str, str]:
    try:
        return secret_env.get(name), ""
    except secret_env.SecretConfigError as error:
        return "", str(error)


def _same(presented: str, expected: str) -> bool:
    """Constant-time, on bytes: `hmac.compare_digest` raises on a str that is not ASCII, and a header can hold one."""
    return hmac.compare_digest(presented.encode("utf-8", "replace"), expected.encode("utf-8", "replace"))


def _presented() -> str:
    header = request.headers.get("Authorization", "")
    return header[7:].strip() if header.startswith("Bearer ") else ""


def _authorised(admin: bool = False) -> tuple[bool, str]:
    """Fails closed: no configured token means no requests. The admin plane refuses a token equal to the job token."""
    expected, why = _secret(ENV_ADMIN_TOKEN if admin else ENV_TOKEN)
    if why:
        return False, why
    if not expected:
        return False, "The executor has no token configured for this and accepts nothing."
    other, why = _secret(ENV_TOKEN if admin else ENV_ADMIN_TOKEN)
    if why:
        return False, why
    if other and _same(expected, other):
        return False, "The job token and the administration token are the same value. Set them to different values."
    presented = _presented()
    if not presented:
        return False, "A bearer token is required."
    return (True, "") if _same(presented, expected) else (False, "Token rejected.")


def _deny(admin: bool = False):
    ok, why = _authorised(admin)
    return None if ok else (jsonify({"error": why}), 401)


# ----------------------------------------------------------------------------------------- catalogue, limits

_catalogue: dict[str, Any] | None = None
_catalogue_error = ""


def catalogue() -> dict[str, Any]:
    global _catalogue, _catalogue_error
    if _catalogue is None:
        try:
            _catalogue, _catalogue_error = templates.load(), ""
        except templates.CatalogueError as error:
            _catalogue, _catalogue_error = {}, str(error)
    return _catalogue


def current_rails() -> dict[str, Any]:
    return rails_mod.load(sorted({t for e in catalogue().values() for t in e.types}))


_lock = threading.Lock()
_submissions: deque[float] = deque()
_waiting = 0
_workspace_locks: dict[tuple[str, str], threading.Lock] = {}


class Busy(Exception):
    """Turned away rather than queued. The message is shown to a person."""


def _admit() -> None:
    """Six requests a minute at most (the same cap as the Ansible executor)."""
    with _lock:
        cutoff = time.monotonic() - 60
        while _submissions and _submissions[0] < cutoff:
            _submissions.popleft()
        if len(_submissions) >= MAX_PER_MINUTE:
            raise Busy(f"More than {MAX_PER_MINUTE} requests in the last minute. Nothing was queued; try again shortly.")
        _submissions.append(time.monotonic())


@contextmanager
def _one_at_a_time(sub: str, group: str) -> Iterator[None]:
    """One plan or apply at a time per workspace; up to five wait their turn, the rest are refused."""
    global _waiting
    with _lock:
        mine = _workspace_locks.setdefault((sub, group), threading.Lock())
    if not mine.acquire(blocking=False):
        with _lock:
            if _waiting >= MAX_QUEUED:
                raise Busy(f"{MAX_QUEUED} requests are already waiting. Nothing was queued.")
            _waiting += 1
        try:
            if not mine.acquire(timeout=LOCK_WAIT):
                raise Busy("Another change in this resource group did not finish in time.")
        finally:
            with _lock:
                _waiting -= 1
    try:
        yield
    finally:
        mine.release()


# --------------------------------------------------------------------------------------------------- reads

_tofu_version = ""


def _versions() -> tuple[str, dict[str, str]]:
    global _tofu_version
    mirror = Path(os.getenv(ENV_MIRROR) or "/opt/mirror") / "registry.opentofu.org"
    found = {}
    for name, path in (("azurerm", "hashicorp/azurerm"), ("azapi", "azure/azapi")):
        versions = sorted(d.name for d in (mirror / path).iterdir() if d.is_dir()) if (mirror / path).is_dir() else []
        found[name] = versions[-1] if versions else ""
    if not _tofu_version:
        import json  # noqa: PLC0415
        import subprocess  # noqa: PLC0415

        try:
            out = subprocess.run([os.getenv(runner.ENV_TOFU) or "tofu", "version", "-json"], capture_output=True, text=True,
                                 timeout=20, env={"PATH": "/usr/local/bin:/usr/bin:/bin"}, check=True).stdout
            _tofu_version = str(json.loads(out).get("terraform_version") or "")
        except (OSError, ValueError, subprocess.SubprocessError):
            _tofu_version = ""
    return _tofu_version, found


def _configured() -> bool:
    job, _ = _secret(ENV_TOKEN)
    admin, _ = _secret(ENV_ADMIN_TOKEN)
    status = keys.status()
    sealed = keys.sealing_key_ok()
    return bool(job and admin and job != admin and sealed and status["passphrase_set"]
                and status["planner"]["set"] and status["applier"]["set"] and _secret("REZZTOR_DB_CLOUD_PASSWORD")[0])


@app.get("/healthz")
def healthz():
    """Unauthenticated, because a readiness probe cannot hold a token: versions, commits and one boolean, no secret."""
    tofu, providers = _versions()
    entries = catalogue()
    body = {"ok": not _catalogue_error, "version": (os.getenv(ENV_VERSION) or "dev").strip(), "tofu": tofu,
            "providers": providers, "templates_commit": runner.templates_commit(),
            "actions_commit": (os.getenv(ENV_ACTIONS_COMMIT) or "none").strip(), "configured": _configured(),
            "templates": sorted(entries)}
    return jsonify(body), (200 if body["ok"] else 503)


# ------------------------------------------------------------------------------------------ plan and apply

def _body() -> dict[str, Any] | None:
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else None


def _common(body: dict[str, Any]) -> str:
    """A reason the request is malformed, or `""`."""
    if not runner.CHANGE_ID.fullmatch(str(body.get("change_id") or "")):
        return "change_id is required (letters, digits, dot, dash, underscore; at most 80)."
    if not isinstance(body.get("actor", ""), str):
        return "actor must be text."
    return ""


@app.post("/v1/plan")
def plan():
    denied = _deny()
    if denied:
        return denied
    body = _body()
    if body is None or (why := _common(body)):
        return jsonify({"error": why if body is not None else "A plan request must be a JSON object."}), 400
    try:
        sub, group = runner.workspace_of(body)
    except runner.Refusal as refusal:
        return jsonify({"ok": False, "stage": refusal.stage, "refusal": refusal.text}), 200
    try:
        _admit()
        with _one_at_a_time(sub, group):
            run = {"refresh_only": refresh.plan, "import": adopt.plan}.get(body.get("mode"), runner.plan)
            return jsonify(run(body, current_rails(), catalogue())), 200
    except Busy as busy:
        return jsonify({"error": str(busy)}), 429


@app.post("/v1/plan-set")
def plan_set():
    """A change set planned whole (ADR 0073): one admission for the call, each step under its own workspace lock."""
    denied = _deny()
    if denied:
        return denied
    body = _body()
    if body is None or (why := _common(body)):
        return jsonify({"error": why if body is not None else "A plan-set request must be a JSON object."}), 400
    try:
        _admit()
        return jsonify(runner.plan_set(body, current_rails(), catalogue(), lock=_one_at_a_time)), 200
    except Busy as busy:
        return jsonify({"error": str(busy)}), 429


@app.post("/v1/apply")
def apply():
    denied = _deny()
    if denied:
        return denied
    body = _body()
    if body is None or (why := _common(body)):
        return jsonify({"error": why if body is not None else "An apply request must be a JSON object."}), 400
    rails = current_rails()
    if not rails_mod.reference_ok(body.get("reference"), rails):
        return jsonify({"error": "The reference is missing or is not in the format the guard rails require."}), 400
    if not DIGEST.fullmatch(str(body.get("plan_digest") or "")):
        return jsonify({"error": "plan_digest is the digest the engineer approved."}), 400
    try:
        sub, group = runner.workspace_of(body)
        runner.resolve(body.get("source"), sub, group, rails, catalogue())
    except runner.Refusal as refusal:
        return jsonify({"ok": False, "stage": refusal.stage, "refusal": refusal.text}), 422
    try:
        if store.applied_before(body["change_id"]):
            return jsonify({"error": f"The change {body['change_id']} has already been applied."}), 409
        _admit()
    except Busy as busy:
        return jsonify({"error": str(busy)}), 429
    except Exception as error:  # noqa: BLE001 - no database means no apply, said plainly
        return jsonify({"error": f"The executor cannot reach its database: {type(error).__name__}."}), 503
    job_id = store.new_job(body["change_id"], str(body.get("actor") or ""))
    threading.Thread(target=_run_apply, args=(job_id, body), daemon=True, name=f"apply-{job_id[:8]}").start()
    return jsonify({"job_id": job_id}), 202


def _run_apply(job_id: str, body: dict[str, Any]) -> None:
    """The thread owns every transition out of `queued`, and always leaves the job in a final state."""
    result: dict[str, Any] = {"status": "failed", "verified": None, "changed_ids": [], "error": "The apply did not run."}
    try:
        sub, group = runner.workspace_of(body)
        with _one_at_a_time(sub, group):
            store.update(job_id, status="running")
            result = runner.apply(body, current_rails(), catalogue())
    except Busy as busy:
        result["error"] = str(busy)
    except Exception as error:  # noqa: BLE001 - a job must never kill the service, and a trace could carry a secret
        result["error"] = f"The apply stopped on an unexpected {type(error).__name__}."
    store.update(job_id, ended_at=time.time(), **result)


@app.post("/v1/refresh/accept")
def refresh_accept():
    denied = _deny()
    if denied:
        return denied
    body = _body()
    if body is None or (why := _common(body)):
        return jsonify({"error": why if body is not None else "An accept request must be a JSON object."}), 400
    if not DIGEST.fullmatch(str(body.get("plan_digest") or "")):
        return jsonify({"error": "plan_digest is the digest the engineer approved."}), 400
    try:
        sub, group = runner.workspace_of(body)
    except runner.Refusal as refusal:
        return jsonify({"ok": False, "stage": refusal.stage, "refusal": refusal.text}), 200
    try:
        _admit()
        with _one_at_a_time(sub, group):
            return jsonify(refresh.accept(body, current_rails(), catalogue())), 200
    except Busy as busy:
        return jsonify({"error": str(busy)}), 429
    except Exception as error:  # noqa: BLE001 - no database means no accept, said plainly
        return jsonify({"error": f"The executor could not accept the state: {type(error).__name__}."}), 503


@app.post("/v1/adopt/apply")
def adopt_apply():
    denied = _deny()
    if denied:
        return denied
    body = _body()
    if body is None or (why := _common(body)):
        return jsonify({"error": why if body is not None else "An adopt request must be a JSON object."}), 400
    if not DIGEST.fullmatch(str(body.get("plan_digest") or "")):
        return jsonify({"error": "plan_digest is the digest the engineer approved."}), 400
    try:
        sub, group = runner.workspace_of(body)
    except runner.Refusal as refusal:
        return jsonify({"ok": False, "stage": refusal.stage, "refusal": refusal.text}), 200
    try:
        _admit()
        with _one_at_a_time(sub, group):
            return jsonify(adopt.apply(body, current_rails(), catalogue())), 200
    except Busy as busy:
        return jsonify({"error": str(busy)}), 429
    except Exception as error:  # noqa: BLE001 - no database means no adoption, said plainly
        return jsonify({"error": f"The executor could not adopt into the state: {type(error).__name__}."}), 503


@app.post("/v1/action")
def action():
    denied = _deny()
    if denied:
        return denied
    body = _body()
    if body is None or (why := _common(body)):
        return jsonify({"error": why if body is not None else "An action request must be a JSON object."}), 400
    rails = current_rails()
    if body.get("mode") == "preview":
        return jsonify(actions.preview(body, rails)), 200
    if body.get("mode") != "run":
        return jsonify({"error": "mode is preview or run."}), 400
    if not rails_mod.reference_ok(body.get("reference"), rails):
        return jsonify({"error": "The reference is missing or is not in the format the guard rails require."}), 400
    if not DIGEST.fullmatch(str(body.get("plan_digest") or "")):
        return jsonify({"error": "plan_digest is the digest the engineer approved."}), 400
    try:
        _action, _rid, _name, sub, group, _variables = actions.check_request(body, rails)
    except runner.Refusal as refusal:
        return jsonify({"ok": False, "stage": refusal.stage, "refusal": refusal.text}), 422
    try:
        if store.applied_before(body["change_id"]):
            return jsonify({"error": f"The change {body['change_id']} has already been applied."}), 409
        _admit()
    except Busy as busy:
        return jsonify({"error": str(busy)}), 429
    except Exception as error:  # noqa: BLE001 - no database means no change, said plainly
        return jsonify({"error": f"The executor cannot reach its database: {type(error).__name__}."}), 503
    job_id = store.new_job(body["change_id"], str(body.get("actor") or ""))
    threading.Thread(target=_run_action, args=(job_id, body, sub, group), daemon=True, name=f"action-{job_id[:8]}").start()
    return jsonify({"job_id": job_id}), 202


def _run_action(job_id: str, body: dict[str, Any], sub: str, group: str) -> None:
    """As `_run_apply`: the thread owns every transition out of `queued` and always leaves the job in a final state."""
    result: dict[str, Any] = {"status": "failed", "verified": None, "changed_ids": [], "error": "The action did not run."}
    try:
        with _one_at_a_time(sub, group):
            store.update(job_id, status="running")
            result = actions.run(body, current_rails())
    except Busy as busy:
        result["error"] = str(busy)
    except Exception as error:  # noqa: BLE001 - a job must never kill the service, and a trace could carry a secret
        result["error"] = f"The action stopped on an unexpected {type(error).__name__}."
    store.update(job_id, ended_at=time.time(), **result)


@app.get("/v1/jobs/<job_id>")
def job(job_id: str):
    denied = _deny()
    if denied:
        return denied
    record = store.get(job_id)
    return (jsonify(store.public(record)), 200) if record else (jsonify({"error": "No such job."}), 404)


# ----------------------------------------------------------------------------------------------------- admin

@app.route("/v1/admin/guardrails", methods=["GET", "PUT"])
def guardrails():
    denied = _deny(admin=True)
    if denied:
        return denied
    if request.method == "GET":
        return jsonify(current_rails()), 200
    try:
        return jsonify(rails_mod.save(_body(), sorted({t for e in catalogue().values() for t in e.types}))), 200
    except ValueError as error:
        return jsonify({"error": str(error)}), 400


@app.get("/v1/admin/keys")
def key_status():
    denied = _deny(admin=True)
    if denied:
        return denied
    try:
        return jsonify(keys.status()), 200
    except keys.KeyRefused as error:
        return jsonify({"error": str(error)}), 503


@app.post("/v1/admin/keys/<role>")
def make_key(role: str):
    denied = _deny(admin=True)
    if denied:
        return denied
    body = _body() or {}
    try:
        return jsonify(keys.make(role, str(body.get("tenant_id") or ""), str(body.get("client_id") or ""))), 200
    except keys.KeyRefused as error:
        return jsonify({"error": str(error)}), 400


def _readable(role: str) -> dict[str, Any]:
    try:
        listed = (arm.get(arm.token(role), "/subscriptions?api-version=2022-12-01") or {}).get("value") or []
        return {"ok": True, "subscriptions": [{"id": s.get("subscriptionId"), "name": s.get("displayName")} for s in listed]}
    except arm.ArmError as error:
        return {"ok": False, "error": str(error)}


@app.post("/v1/admin/test")
def test_keys():
    """Sign in as both identities, list what each can read, and check the applier's role. Never returns a token."""
    denied = _deny(admin=True)
    if denied:
        return denied
    check = rolecheck.run("applier")
    return jsonify({"planner": _readable("planner"), "applier": _readable("applier"), "role_ok": check["role_ok"],
                    "reason": check["reason"]}), 200


# ------------------------------------------------------------------------------------------------- serving

def listen_options() -> dict[str, Any]:
    path = (os.getenv(ENV_SOCKET) or "/run/cloud-socket/cloud.sock").strip()
    return {"unix_socket": path, "unix_socket_perms": "660"}


def main() -> None:
    """Serve. A function, so that the image can start it from compiled code,
    which `python -m` cannot run (docker/compile_python.py)."""
    from waitress import serve

    options = listen_options()
    if os.path.exists(options["unix_socket"]):
        os.unlink(options["unix_socket"])  # a volume keeps the last run's socket file; waitress would not bind over it
    store.fail_interrupted()
    serve(app, threads=8, **options)


if __name__ == "__main__":
    main()
