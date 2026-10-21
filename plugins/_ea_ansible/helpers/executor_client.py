"""The only module in this plugin that reaches the executor.

E1 in `tests/test_net_contract.py` asserts that: no other file here may import
an HTTP client. One funnel, one host, one token -- so "what can this plugin
talk to" is answered by reading one file rather than by grepping the tree.

It sends a name, a commit, validated variables, a device selector and a
check-mode flag. `job_schema.validate_job_fields` runs here as well as on the
executor, so a job that could never be accepted is refused before it is sent.
"""

from __future__ import annotations

import http.client
import json as jsonlib
import socket
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import requests

from plugins._ea_ansible.helpers import config as plugin_config
from plugins._ea_ansible.helpers import job_schema, network_list

# The executor is on an internal network and answers quickly or not at all;
# a job's own duration is bounded by the executor, not by this timeout.
CONNECT_TIMEOUT = 5
READ_TIMEOUT = 30
# A backup waits for a playbook run, which the executor bounds at its own job timeout.
BACKUP_TIMEOUT = 330
POLL_INTERVAL = 2.0

#: An executor address of `unix:///run/.../executor.sock` is a socket, not a host
#: (ADR 0051). The tokens still apply: the socket removes the network path, not
#: the authentication.
UNIX_PREFIX = "unix://"


class ExecutorError(Exception):
    """Base for everything this module raises. Never contains the token."""


class ExecutorUnreachable(ExecutorError):
    """The executor did not answer."""


class ExecutorRefused(ExecutorError):
    """The executor answered, and said no. The message is safe to show a user."""


class JobStillRunning(ExecutorRefused):
    """This side stopped waiting, or could no longer read the job's state; the job was taken by the executor and may
    yet finish."""


class NotConfigured(ExecutorError):
    """No executor address or no token on this instance."""


@dataclass(frozen=True)
class JobResult:
    job_id: str
    status: str
    exit_code: int | None
    events: tuple[dict[str, Any], ...]
    output: str

    @property
    def succeeded(self) -> bool:
        return self.status == "succeeded"

    def commands(self) -> list[str]:
        """Every command the run reported, in the order the tasks produced them.

        This is what an engineer approves. `commands` is what a resource module
        says it configured -- under --check, what it would have configured.
        """
        found: list[str] = []
        for event in self.events:
            value = event.get("commands")
            if isinstance(value, list):
                found.extend(str(line) for line in value)
            elif isinstance(value, str):
                found.append(value)
        return found

    def diffs(self) -> list[dict[str, Any]]:
        return [event["diff"] for event in self.events if event.get("diff")]


def _base(agent: Any | None) -> str:
    url = plugin_config.get_config(agent)["executor_url"]
    if not url:
        raise NotConfigured(
            "Network automation is not configured on this instance. An "
            "administrator must set the Ansible executor address."
        )
    return url


def _headers(agent: Any | None) -> dict[str, str]:
    token = plugin_config.executor_token(agent)
    if not token:
        raise NotConfigured(
            "Network automation has no executor token on this instance. An "
            "administrator must set one."
        )
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def setup_problem(agent: Any | None = None) -> str | None:
    """Why the executor is not set up on this instance (no address or no token), or None when it is."""
    try:
        _base(agent)
        _headers(agent)
    except NotConfigured as error:
        return str(error)
    return None


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._path)


class _Reply:
    """The part of `requests.Response` the callers use."""

    def __init__(self, status_code: int, content: bytes) -> None:
        self.status_code = status_code
        self.content = content

    def json(self) -> Any:
        return jsonlib.loads(self.content)  # ValueError on garbage, as requests raises


def _send(
    agent: Any | None,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    json: Any = None,
    params: dict[str, Any] | None = None,
    timeout: float = READ_TIMEOUT,
) -> Any:
    """The one place a request leaves this plugin, over TCP or the executor's socket."""
    base = _base(agent)
    try:
        if not base.startswith(UNIX_PREFIX):
            return requests.request(
                method, f"{base}{path}", headers=headers, json=json, params=params,
                timeout=(CONNECT_TIMEOUT, timeout),
            )
        socket_path = base[len(UNIX_PREFIX):]
        if not socket_path.startswith("/"):
            raise NotConfigured("The executor address unix:// must be followed by an absolute socket path.")
        headers = dict(headers or {})
        body = None
        if json is not None:
            body = jsonlib.dumps(json)
            headers["Content-Type"] = "application/json"
        connection = _UnixConnection(socket_path, timeout)
        try:
            connection.request(method, f"{path}?{urlencode(params)}" if params else path, body, headers)
            reply = connection.getresponse()
            return _Reply(reply.status, reply.read())
        finally:
            connection.close()
    except (requests.exceptions.RequestException, OSError, http.client.HTTPException) as error:
        raise ExecutorUnreachable(f"The Ansible executor did not answer: {error}") from error


def health(agent: Any | None = None) -> dict[str, Any]:
    """Reachability and the catalogue commit a job must quote. Needs no token."""
    response = _send(agent, "GET", "/healthz")
    if response.status_code != 200:
        raise ExecutorRefused(f"The executor reported itself unhealthy ({response.status_code}).")
    return response.json()


def submit(
    agent: Any | None,
    playbook_name: str,
    commit: str,
    variables: dict[str, Any],
    limit: str,
    check_mode: bool,
) -> str:
    """Start a job and return its id. Never sends anything but these five fields."""
    job = {
        "playbook_name": playbook_name,
        "commit": commit,
        "variables": dict(variables or {}),
        "limit": limit,
        "check_mode": bool(check_mode),
    }
    # Validated here as well as at the executor. Two checks of one definition,
    # not two definitions.
    job_schema.validate_job_fields(job)

    response = _send(agent, "POST", "/jobs", json=job, headers=_headers(agent))
    if response.status_code == 202:
        return str(response.json()["job_id"])
    raise ExecutorRefused(_refusal(response))


def preflight(agent: Any | None, hosts: list[str]) -> dict[str, Any]:
    """What the executor knows about these hosts, from inside its network.

    Read-only and secret-free; the executor caps a call at 10 hosts. Returns the
    `results` object keyed by host.
    """
    response = _send(agent, "POST", "/v1/preflight", json={"hosts": list(hosts)}, headers=_headers(agent))
    if response.status_code != 200:
        raise ExecutorRefused(_refusal(response))
    return dict(response.json().get("results") or {})


def networks(agent: Any | None = None) -> dict[str, Any]:
    """The appliance's network list as the executor read it; `known` is false when there is none."""
    response = _send(agent, "GET", "/v1/networks", headers=_headers(agent))
    if response.status_code != 200:
        raise ExecutorRefused(_refusal(response))
    return network_list.parse(response.json())


def _backup_call(agent: Any | None, method: str, path: str, timeout: int = READ_TIMEOUT, **kwargs: Any) -> dict[str, Any]:
    """One call to the executor's backup routes. None of them returns raw configuration text."""
    response = _send(agent, method, f"/v1/backups{path}", headers=_headers(agent), timeout=timeout, **kwargs)
    if response.status_code != 200:
        raise ExecutorRefused(_refusal(response))
    return response.json()


def backup_run(agent: Any | None, hosts: list[str]) -> dict[str, Any]:
    """Back up up to 10 hosts; `{host: {ok, id, taken_at, sha256, size, changed, error}}`."""
    return dict(_backup_call(agent, "POST", "", BACKUP_TIMEOUT, json={"hosts": list(hosts)}).get("results") or {})


def backup_list(agent: Any | None, host: str | None = None) -> dict[str, list[dict[str, Any]]]:
    """`{host: [{id, taken_at, checked_at, sha256, size}]}`, newest first; every host when none is named."""
    return dict(_backup_call(agent, "GET", "", params={"host": host} if host else None).get("backups") or {})


def backup_text(agent: Any | None, host: str, backup_id: str) -> str:
    """The executor's redacted view of one backup."""
    return str(_backup_call(agent, "GET", f"/{host}/{backup_id}").get("text") or "")


def backup_diff(agent: Any | None, host: str, old: str, new: str) -> dict[str, Any]:
    """`{diff, changed_lines}` with every line redacted by the executor."""
    return _backup_call(agent, "GET", f"/{host}/diff", params={"from": old, "to": new})


def jobs(
    agent: Any | None = None,
    *,
    limit: int = 50,
    offset: int = 0,
    status: str = "",
    source: str = "",
    playbook: str = "",
) -> dict[str, Any]:
    """The executor's own history. Filters are passed through, not interpreted.

    This client does not cache or merge the answer with anything local: the
    executor is the workload that talks to devices, so its record is the one
    that is about the estate rather than about what this application saw.
    """
    params = {"limit": limit, "offset": offset}
    for name, value in (("status", status), ("source", source), ("playbook", playbook)):
        if value:
            params[name] = value
    response = _send(agent, "GET", "/jobs", headers=_headers(agent), params=params)
    if response.status_code != 200:
        raise ExecutorRefused(_refusal(response))
    return response.json()


def poll(agent: Any | None, job_id: str) -> dict[str, Any]:
    response = _send(agent, "GET", f"/jobs/{job_id}", headers=_headers(agent))
    if response.status_code != 200:
        raise ExecutorRefused(_refusal(response))
    return response.json()


def run(
    agent: Any | None,
    playbook_name: str,
    commit: str,
    variables: dict[str, Any],
    limit: str,
    check_mode: bool,
    wait_seconds: int | None = None,
) -> JobResult:
    """Submit and wait. The executor caps the job's own duration; this caps ours."""
    job_id = submit(agent, playbook_name, commit, variables, limit, check_mode)
    deadline = time.monotonic() + (
        wait_seconds or plugin_config.get_config(agent)["job_timeout_seconds"]
    )

    record: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            record = poll(agent, job_id)
        except ExecutorError as error:  # sent: from here on nothing may be told that it did not run
            raise JobStillRunning(f"Job {job_id} was sent, and then its state could not be read: {error}") from error
        if record.get("status") in job_schema.TERMINAL_STATUSES:
            break
        time.sleep(POLL_INTERVAL)
    else:
        raise JobStillRunning(
            f"Job {job_id} was still running when this request gave up waiting."
        )

    return JobResult(
        job_id=job_id,
        status=str(record.get("status") or "unknown"),
        exit_code=record.get("exit_code"),
        events=tuple(record.get("events") or ()),
        output=str(record.get("output") or ""),
    )


def _refusal(response: Any) -> str:
    """The executor's own message, which is written to be shown to a person."""
    try:
        payload = response.json()
        message = str(payload.get("error") or "").strip()
    except ValueError:
        message = ""
    return message or f"The executor refused the job ({response.status_code})."
