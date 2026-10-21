"""The only module in this plugin that reaches the cloud executor.

One funnel, one socket, two tokens: the **job token** for plan, apply and job state, the **admin token** for the
guard rails and keys (build contract, "Cloud executor API"). Both are read from the environment at the moment they are
needed (`REZZTOR_CLOUD_JOB_TOKEN`, or the file named by `REZZTOR_CLOUD_JOB_TOKEN_FILE`; the same for
`REZZTOR_CLOUD_ADMIN_TOKEN`), never cached, never returned and never put in a message. The executor is reached over its
Unix socket only: the socket removes the network path, the bearer token is still checked.

There is **no destroy call here** and no way to ask for one: `plan` takes a mode of `apply`, `refresh_only` or
`import`, and `plan_set` plans an ordered set of template steps (ADR 0073); a step of one is applied through `apply` like any
other change. `tests/test_azure_change_executor_client.py` greps this plugin for any other HTTP client.
"""

from __future__ import annotations

import http.client
import json
import os
import time
from typing import Any

from helpers import dotenv, plugins

# The connection class is the Ansible plugin's: the same socket, the same timeout rules, one place to fix.
from plugins._ea_ansible.helpers.executor_client import UNIX_PREFIX, _UnixConnection  # noqa: PLC2701

PLUGIN_NAME = "_ea_azure_change"
JOB_KEY = dotenv.KEY_CLOUD_JOB_TOKEN
ADMIN_KEY = "REZZTOR_CLOUD_ADMIN_TOKEN"
DEFAULT_TIMEOUT = 60
#: tofu init, validate and plan can take minutes; the executor bounds the run itself.
PLAN_TIMEOUT = 600
#: A set is planned step by step in one call (about a minute a step, twelve at most).
PLAN_SET_TIMEOUT = 1200
POLL_INTERVAL = 3.0
#: What a job reports when it will not change any more.
TERMINAL = ("applied", "verify_failed", "failed", "superseded")


class ExecutorError(Exception):
    """Everything this module raises. Never contains a token; the message is safe to show a person."""


class ExecutorUnreachable(ExecutorError):
    """The executor did not answer. Nothing is known to have been done."""


class ExecutorRefused(ExecutorError):
    """The executor answered, and said no."""


class ExecutorBusy(ExecutorRefused):
    """HTTP 429: the executor admits six requests a minute and turned this one away before doing anything. Try the same call again."""


class NotConfigured(ExecutorError):
    """No socket address or no token on this instance."""


def config(agent: Any | None = None) -> dict[str, Any]:
    cfg = plugins.get_plugin_config(PLUGIN_NAME, agent=agent) or {}
    return {
        "executor_url": str(cfg.get("executor_url") or "").strip(),
        "timeout": int(cfg.get("timeout_seconds") or DEFAULT_TIMEOUT),
        "apply_wait_seconds": int(cfg.get("apply_wait_seconds") or 1500),
    }


def _token(key: str) -> str:
    value = str(dotenv.get_dotenv_value(key) or "").strip()
    path = os.getenv(f"{key}_FILE")
    if not value and path:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                value = handle.read().strip()
        except OSError:
            value = ""
    return value


def is_configured(agent: Any | None = None) -> bool:
    return config(agent)["executor_url"].startswith(UNIX_PREFIX + "/") and bool(_token(JOB_KEY))


def _send(agent: Any | None, method: str, path: str, body: Any = None, *, key: str = "", timeout: float | None = None) -> tuple[int, Any]:
    """The one place a request leaves this plugin. Returns (status, parsed JSON)."""
    cfg = config(agent)
    url = cfg["executor_url"]
    if not url.startswith(UNIX_PREFIX + "/"):
        raise NotConfigured("The cloud executor address must be unix:// followed by an absolute socket path.")
    headers = {"Accept": "application/json"}
    if key:
        token = _token(key)
        if not token:
            raise NotConfigured("The cloud executor has no token on this instance. An administrator must provide one.")
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        data = json.dumps(body)
        headers["Content-Type"] = "application/json"
    connection = _UnixConnection(url[len(UNIX_PREFIX):], timeout or cfg["timeout"])
    try:
        connection.request(method, path, data, headers)
        reply = connection.getresponse()
        raw = reply.read()
        status = reply.status
    except (OSError, http.client.HTTPException) as error:
        raise ExecutorUnreachable(f"The cloud executor did not answer: {error}") from error
    finally:
        connection.close()
    try:
        return status, json.loads(raw) if raw else {}
    except ValueError as error:
        raise ExecutorRefused(f"The cloud executor answered with something that is not JSON ({status}).") from error


def _refused(status: int, payload: Any) -> ExecutorRefused:
    return (ExecutorBusy if status == 429 else ExecutorRefused)(_refusal(status, payload))


def _refusal(status: int, payload: Any) -> str:
    if isinstance(payload, dict):
        text = str(payload.get("error") or payload.get("refusal") or "").strip()
        if text:
            return text
    return f"The cloud executor refused the request ({status})."


def health(agent: Any | None = None) -> dict[str, Any]:
    """Reachability, versions and the template catalogue commit. Needs no token and returns no secret."""
    status, payload = _send(agent, "GET", "/healthz")
    if status != 200 or not isinstance(payload, dict):
        raise ExecutorRefused(_refusal(status, payload))
    return payload


def plan(agent: Any | None, *, source: dict[str, Any], workspace: dict[str, str], change_id: str, actor: str,
         mode: str = "apply") -> dict[str, Any]:
    """Plan a change. The answer is `{ok: True, plan_digest, summary, checks, cost, drift, ...}` or
    `{ok: False, stage, refusal}`; a refusal is an answer, not an error. The plan file never leaves the executor."""
    if mode not in ("apply", "refresh_only", "import"):
        raise ExecutorError(f"There is no plan mode '{mode}'.")
    status, payload = _send(agent, "POST", "/v1/plan", {"mode": mode, "source": source, "workspace": workspace,
                                                        "change_id": change_id, "actor": actor}, key=JOB_KEY, timeout=PLAN_TIMEOUT)
    if isinstance(payload, dict) and "ok" in payload:
        return payload
    raise ExecutorRefused(_refusal(status, payload))


def apply(agent: Any | None, *, source: dict[str, Any], workspace: dict[str, str], plan_digest: str, reference: str,
          change_id: str, actor: str) -> str:
    """Start an approved apply and return its job id. The executor plans again and supersedes the job when the digest differs."""
    status, payload = _send(agent, "POST", "/v1/apply", {"source": source, "workspace": workspace, "plan_digest": plan_digest,
                                                         "reference": reference, "change_id": change_id, "actor": actor}, key=JOB_KEY)
    if status == 202 and isinstance(payload, dict) and payload.get("job_id"):
        return str(payload["job_id"])
    raise _refused(status, payload)


def plan_set(agent: Any | None, *, subscription: str, steps: list[dict[str, Any]], change_id: str, actor: str) -> dict[str, Any]:
    """Plan an ordered set of template steps in one call. The answer is `{ok: True, set_digest, steps, cost, checks, ...}` or
    `{ok: False, step, stage, refusal}` (`step` 0 is the set itself); a refusal is an answer, not an error."""
    status, payload = _send(agent, "POST", "/v1/plan-set", {"subscription": subscription, "steps": steps, "change_id": change_id,
                                                            "actor": actor}, key=JOB_KEY, timeout=PLAN_SET_TIMEOUT)
    if isinstance(payload, dict) and "ok" in payload:
        return payload
    raise _refused(status, payload)


def action_preview(agent: Any | None, *, name: str, resource_id: str, variables: dict[str, Any], change_id: str, actor: str) -> dict[str, Any]:
    """What a simple action would do to one resource, and the digest of it. `{ok: True, line, plan_digest, ...}` or
    `{ok: False, stage, refusal}`. Nothing in Azure changes."""
    status, payload = _send(agent, "POST", "/v1/action", {"action": name, "resource_id": resource_id, "variables": variables, "mode": "preview",
                                                          "change_id": change_id, "actor": actor}, key=JOB_KEY, timeout=PLAN_TIMEOUT)
    if isinstance(payload, dict) and "ok" in payload:
        return payload
    raise ExecutorRefused(_refusal(status, payload))


def action_run(agent: Any | None, *, name: str, resource_id: str, variables: dict[str, Any], plan_digest: str, reference: str,
               change_id: str, actor: str) -> str:
    """Start an approved simple action and return its job id. The executor reads Azure again and supersedes the job when the
    digest differs."""
    status, payload = _send(agent, "POST", "/v1/action", {"action": name, "resource_id": resource_id, "variables": variables, "mode": "run",
                                                          "plan_digest": plan_digest, "reference": reference, "change_id": change_id,
                                                          "actor": actor}, key=JOB_KEY)
    if status == 202 and isinstance(payload, dict) and payload.get("job_id"):
        return str(payload["job_id"])
    raise _refused(status, payload)


def refresh_accept(agent: Any | None, *, source: dict[str, Any], workspace: dict[str, str], plan_digest: str, change_id: str,
                   actor: str) -> dict[str, Any]:
    """Write what Azure looks like now into one state, only if the drift is still the approved `plan_digest` (a refresh-only
    plan, `plan(mode="refresh_only")`). It needs no CR or SR number because nothing in Azure changes, and it runs as the
    read identity. `{ok: True, accepted: [native_id]}` or `{ok: False, stage, refusal, superseded?}`."""
    status, payload = _send(agent, "POST", "/v1/refresh/accept", {"source": source, "workspace": workspace, "plan_digest": plan_digest,
                                                                  "change_id": change_id, "actor": actor}, key=JOB_KEY, timeout=PLAN_TIMEOUT)
    if isinstance(payload, dict) and "ok" in payload:
        return payload
    raise ExecutorRefused(_refusal(status, payload))


def adopt_apply(agent: Any | None, *, source: dict[str, Any], workspace: dict[str, str], plan_digest: str, change_id: str,
                actor: str) -> dict[str, Any]:
    """Write the imports of resources that already exist into one state, only if the plan is still the approved `plan_digest`
    (an import plan, `plan(mode="import")` with `source={"adopt": [...]}`). It needs no CR or SR number because nothing in Azure
    changes, and it runs as the read identity. `{ok: True, adopted: [native_id]}` or `{ok: False, stage, refusal, superseded?}`."""
    status, payload = _send(agent, "POST", "/v1/adopt/apply", {"source": source, "workspace": workspace, "plan_digest": plan_digest,
                                                               "change_id": change_id, "actor": actor}, key=JOB_KEY, timeout=PLAN_TIMEOUT)
    if isinstance(payload, dict) and "ok" in payload:
        return payload
    raise ExecutorRefused(_refusal(status, payload))


def job(agent: Any | None, job_id: str) -> dict[str, Any]:
    """`{status, verified, changed_ids, error?, started_at, ended_at}`."""
    safe = "".join(c for c in str(job_id) if c.isalnum() or c in "-_")
    status, payload = _send(agent, "GET", f"/v1/jobs/{safe}", key=JOB_KEY)
    if status != 200 or not isinstance(payload, dict):
        raise ExecutorRefused(_refusal(status, payload))
    return payload


def wait(agent: Any | None, job_id: str, seconds: float) -> dict[str, Any]:
    """Poll until the job is terminal or `seconds` pass; the last answer comes back either way (check `status`)."""
    deadline = time.monotonic() + seconds
    while True:
        answer = job(agent, job_id)
        if answer.get("status") in TERMINAL or time.monotonic() >= deadline:
            return answer
        time.sleep(POLL_INTERVAL)


def admin(agent: Any | None, method: str, path: str, body: Any = None) -> dict[str, Any]:
    """One call under `/v1/admin/` with the admin token. Used by the Settings endpoint only; never returns a key."""
    if not path.startswith("/v1/admin/"):
        raise ExecutorError("Only /v1/admin/ calls go through here.")
    status, payload = _send(agent, method, path, body, key=ADMIN_KEY)
    if status != 200 or not isinstance(payload, dict):
        raise ExecutorRefused(_refusal(status, payload))
    return payload
