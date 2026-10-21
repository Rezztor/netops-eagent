"""Talking to one SolarWinds instance. Reading only.

The whole design fits in one sentence: this module has a method that reaches
`/Query` and no method that can reach `/Invoke/{entity}/{verb}` at all. Not
gated by a setting -- absent from the code. Acknowledging an alert, editing a
property, anything that changes SolarWinds state is therefore not a thing a
bug, a bad prompt, or a future contributor can accidentally wire up here; it is
a thing that does not exist to call. If SolarWinds' write path is ever wanted,
it is a new module built with its own review, not a flag added to this one.

Runs in the application process, not on the code-execution runner -- the same
shape as `helpers/mailer.py`. The runner's egress is deliberately tight
(ADR 0019/0020), and a SolarWinds service account has no reason to be reachable
from code the agent itself writes and executes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import requests

from plugins._ea_solarwinds.helpers import config as plugin_config

# A response containing this, case-insensitively, names an entity SWIS could
# not resolve -- i.e. a module not installed or not licensed on this instance,
# not a transient failure. Sourced from a real SWIS error a SolarWinds forum
# thread quotes verbatim: "Source entity [Orion.AlertHistory] not found in
# catalog." Not yet exercised against a live instance from this codebase --
# Phase 1's live verification step (README.md) is what confirms it, and this
# pattern is deliberately easy to extend if a real instance turns out to phrase
# it differently.
_ENTITY_NOT_FOUND = re.compile(r"entity\s*\[[^\]]*\]\s*not found in catalog", re.IGNORECASE)


class SolarWindsError(Exception):
    """Base for everything this module raises. Never contains the password."""


class NotConfigured(SolarWindsError):
    """No host, username or password is set yet."""


class ConnectionFailed(SolarWindsError):
    """Reached, or tried to reach, the instance and failed -- network, TLS,
    or the credentials were refused."""


class QueryRefused(ConnectionFailed):
    """SWIS answered with an HTTP error other than 401 (bad column, server
    fault). `status` lets a caller tell a rejected query from an outage."""

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


class CapabilityUnavailable(SolarWindsError):
    """The query named an entity SWIS doesn't recognise on this instance --
    the module behind a capability isn't installed or licensed here. Not a
    bug and not this account's fault: the right response is to say so
    plainly, not to raise a five-line stack trace into a chat transcript."""


@dataclass(slots=True)
class Connection:
    host: str
    port: int
    username: str
    password: str
    verify_tls: bool
    timeout_seconds: int


def resolve_connection() -> Connection:
    """The instance and credentials to use, or raise saying what's missing."""
    from helpers import dotenv

    settings = plugin_config.get_config()
    password = dotenv.get_dotenv_value(dotenv.KEY_SOLARWINDS_PASSWORD) or ""

    missing = [
        name
        for name, value in (
            ("host", settings["host"]),
            ("username", settings["username"]),
            ("password", password),
        )
        if not value
    ]
    if missing:
        raise NotConfigured(
            f"SolarWinds is not configured: missing {', '.join(missing)}. "
            "An administrator sets this under Settings."
        )

    return Connection(
        host=settings["host"],
        port=settings["port"],
        username=settings["username"],
        password=password,
        verify_tls=settings["verify_tls"],
        timeout_seconds=settings["timeout_seconds"],
    )


def query(swql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Run one read query and return its rows.

    The only function in this module that reaches the network, and the only
    one that can. `swql` is always a string this plugin wrote -- see
    `helpers/capabilities.py`, which is the one caller and never builds it by
    interpolating anything the agent supplied.
    """
    connection = resolve_connection()
    url = (
        f"https://{connection.host}:{connection.port}"
        "/SolarWinds/InformationService/v3/Json/Query"
    )
    body: dict[str, Any] = {"query": swql}
    if params:
        body["parameters"] = params

    try:
        response = requests.post(
            url,
            json=body,
            auth=(connection.username, connection.password),
            timeout=connection.timeout_seconds,
            verify=connection.verify_tls,
            # Only `/Query` on the configured host: a redirect would re-send the credentials wherever it points.
            allow_redirects=False,
        )
    except requests.exceptions.SSLError as error:
        raise ConnectionFailed(
            f"TLS certificate verification failed for {connection.host}: {error}. "
            "If this instance uses a self-signed certificate, an administrator "
            "must opt into that explicitly (verify_tls) rather than have it "
            "assumed."
        ) from error
    except requests.exceptions.Timeout as error:
        raise ConnectionFailed(
            f"SolarWinds did not answer within {connection.timeout_seconds}s."
        ) from error
    except requests.exceptions.RequestException as error:
        raise ConnectionFailed(f"Could not reach SolarWinds: {error}") from error

    if response.status_code == 401:
        raise ConnectionFailed("SolarWinds rejected the configured credentials.")
    if 300 <= response.status_code < 400:
        raise ConnectionFailed(f"SolarWinds answered with a redirect ({response.status_code}), which is not followed.")

    if response.status_code >= 400:
        detail = _error_detail(response)
        if _ENTITY_NOT_FOUND.search(detail):
            raise CapabilityUnavailable(detail)
        raise QueryRefused(f"SolarWinds refused the query: {detail}", response.status_code)

    try:
        payload = response.json()
    except ValueError as error:
        raise ConnectionFailed("SolarWinds returned a response that was not JSON.") from error

    rows = payload.get("results") if isinstance(payload, dict) else None
    return rows if isinstance(rows, list) else []


def _error_detail(response: "requests.Response") -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:500]
    if isinstance(payload, dict):
        for key in ("Message", "message", "error"):
            if payload.get(key):
                return str(payload[key])[:500]
    return str(payload)[:500]
