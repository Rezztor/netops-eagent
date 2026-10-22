"""Licence entitlements: what other code asks, and what a Restricted installation does not start.

Everything here is derived from `helpers.license_client.state()`, the verdict of the licence contract
(what the product concludes from the licence server's verdict):

  licensed  full function
  grace1    full function; administrators are warned
  grace2    full function; everybody is warned
  restricted  sign-in, reading, export, settings and the licence page work. New agent runs, new device or
              cloud changes and scheduled tasks do not start. Nothing is deleted, nothing in flight is killed.

An installation with no licence is Restricted when it was built as Enterprise (or UAT), and so is one whose stored
licence does not verify. A Community build — a source checkout, a self-built image, the old open lab stamp — is
not Restricted: every feature works, and there is no seat, device or feature limit.

Where Restricted is applied, so the list can be read in one place:

- `require_agent_runs_allowed()` at `AgentContext.communicate` and at the scheduler's task runner;
- `RESTRICTED_TOOLS` at `extensions/python/tool_execute_before/_05_licence_restricted.py`, for a run that was
  already going when the state changed;
- `helpers.api_policy.REFUSED_WHEN_RESTRICTED` at the API dispatcher, for approvals and manual task runs.

The check is local: one row read and a few Ed25519 verifications. No request path calls the licence server.
"""

from typing import Any, Dict, Optional

from rezlic.formats import EDITION_DEVICES

from helpers import license_client

# The states in which the licence is honoured: its seats and features apply.
HONOURED_STATES = ("licensed", "grace1", "grace2")

#: Licence feature name -> the bundled plugin it stands for. `feature_enabled()` answers from the licence; no
#: tool or endpoint asks it yet, so a feature that is absent is shown and not refused (ADR 0078).
FEATURE_PLUGINS = {
    "network_automation": "_ea_ansible",
    "azure": "_ea_azure",
    "azure_change": "_ea_azure_change",
    "solarwinds": "_ea_solarwinds",
    "siem_forward": "_ea_siem_forward",
    "mail": "_ea_mail",
    "auto_skills": "_ea_auto_skills",
    "attachments": "_ea_attachments",
}

#: Agent tools that draft or apply a device or cloud change. Reads are not here: reading works when Restricted.
RESTRICTED_TOOLS = frozenset({"net_change", "net_apply", "net_plan", "azure_change", "azure_action", "azure_apply"})


class LicenceRestrictedError(RuntimeError):
    """Raised where something new would start on a Restricted installation. The API layer maps it to a 403
    with the message; the scheduler records it as the task's failure."""


def restricted_refusal(state: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """The sentence to refuse with when the installation is Restricted, or None when it is not."""
    state = state or license_client.state()
    if state["state"] != "restricted":
        return None
    return (
        f"This installation is restricted: {state['message'] or 'the licence is not valid.'} "
        "New agent runs, device and cloud changes and scheduled tasks do not start; sign-in, reading, export "
        "and settings keep working. An administrator can fix this on the licence page (Settings, Licence)."
    )


def require_agent_runs_allowed() -> None:
    """Raise LicenceRestrictedError if a new agent run may not start.

    Called at the run chokepoints, not per endpoint: a dozen entry points start runs (UI, REST API key,
    scheduler, MCP, A2A, email, connectors, queue), and a per-endpoint gate is one future plugin away from
    being bypassed.
    """
    refusal = restricted_refusal()
    if refusal:
        raise LicenceRestrictedError(refusal)


def agent_runs_allowed() -> bool:
    return license_client.state()["state"] != "restricted"


def is_licensed() -> bool:
    state = license_client.state()["state"]
    return state in HONOURED_STATES or state == "community"


def seats_used() -> int:
    """Active accounts on this installation: enabled ones."""
    return license_client.seats_used()


def seats_allowed(state: Optional[Dict[str, Any]] = None) -> Optional[int]:
    """Seats the licence permits, or None when there is no limit: a Community build, or no honoured licence."""
    state = state or license_client.state()
    if state["state"] == "community" or state["state"] not in HONOURED_STATES:
        return None
    return state["licence"]["seats"]


def devices_used() -> int:
    """Network devices in use: active inventory devices and Azure firewalls, load balancers and gateways."""
    return license_client.devices_used()


def devices_allowed(state: Optional[Dict[str, Any]] = None) -> Optional[int]:
    """Network devices the licence allows, or None for no limit: the signed `devices` field when the licence has
    one (rezlic verified it is an int >= 1), else its edition's entry in rezlic's `EDITION_DEVICES` (free 100, pro
    and ultra 500); any other edition, a Community build and a trial without the field have none. Nothing is refused at
    the limit; it is shown (ADR 0093)."""
    state = state or license_client.state()
    if state["state"] == "community" or state["state"] not in HONOURED_STATES:
        return None
    licence = state["licence"]
    if "devices" in licence:
        return licence["devices"]
    if licence["kind"] == "trial":
        return None
    return EDITION_DEVICES.get(licence["edition"])


def devices_warning(used: int, allowed: Optional[int]) -> Optional[str]:
    """"over" above the limit, "near" from 90% of it, else None. A warning only: nothing is refused."""
    if allowed is None:
        return None
    if used > allowed:
        return "over"
    return "near" if used * 10 >= allowed * 9 else None


def feature_enabled(name: str) -> bool:
    """Whether this feature is available. A Community build has every feature. Grace counts: it is full function."""
    state = license_client.state()
    if state["state"] == "community":
        return True
    return state["state"] in HONOURED_STATES and name in state["licence"]["features"]


def licence_summary() -> Dict[str, Any]:
    """What every signed-in user may know: the state and whether runs start. No commercial terms.

    A Community build reports ``community`` and no sentence: the app-wide banner stays down. The one line
    about Rezztor Enterprise lives on the administrator's licence page.
    """
    state = license_client.state()
    return {
        "state": state["state"],
        "agent_runs_allowed": state["state"] != "restricted",
        "grace_days_remaining": state["days_left"] if state["state"] in ("grace1", "grace2") else None,
        "reason": None if state["state"] == "community" else state["message"],
    }


def licence_status() -> Dict[str, Any]:
    """Everything the administrator's licence page shows. Never the token, the activation key or a request body."""
    state = license_client.state()
    licence, lease = state["licence"], state["lease"]
    try:
        server = license_client.server_url()
    except license_client.LicenceError as exc:
        server = exc.reason
    devices, devices_limit = devices_used(), devices_allowed(state)
    return {
        "state": state["state"],
        "reason": state["reason"],
        "message": state["message"],
        "problem": state["problem"],
        "clock_behind": state["clock_behind"],
        "agent_runs_allowed": state["state"] != "restricted",
        "restricted_at": state["restricted_at"],
        "days_left": state["days_left"],
        "licence": None if licence is None else {
            "lic": licence["lic"], "ver": licence["ver"], "customer": licence["customer"],
            "kind": licence["kind"], "edition": licence["edition"], "features": licence["features"],
            "seats": licence["seats"], "instances": licence["instances"], "offline": licence["offline"],
            "contract_end": licence["exp"], "grace_days": licence["grace_days"],
        },
        "lease_end": lease["exp"] if lease else None,
        "notice_until": lease["notice_until"] if lease and lease["status"] == "revoked" else None,
        "seats_used": seats_used(),
        "devices_used": devices,
        "devices_allowed": devices_limit,
        "devices_warning": devices_warning(devices, devices_limit),
        "can_release": bool(state["has_token"] and licence and not licence["offline"]),
        "pending": state["pending"],
        "releasing": state["releasing"],
        "last_attempt_at": state["last_attempt_at"] or None,
        "last_success_at": state["last_success_at"] or None,
        "last_result": state["last_result"] or None,
        "next_attempt_at": state["next_attempt_at"] or None,
        "installation_id": license_client.installation_id(),
        "server_url": server,
    }
