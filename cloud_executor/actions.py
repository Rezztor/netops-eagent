"""Simple actions: one reviewed ARM call each (build contract, "Simple actions"; ADR 0065).

Seven additive actions and no others: four starts, two scale-outs and a tag merge. The model names an action, an
inventory resource and typed values; the URL is `https://management.azure.com` + that resource's ID + a suffix fixed by
the action's kind + a constant api-version. Nothing here takes a URL, a verb or a body from a caller.

The ARM client has two writing verbs, POST and PATCH (a test scans this file for any other). A scale-out reads the current
count and refuses one that is not higher; a start is refused unless the resource is stopped; a tag is added or its value
changed, never emptied. Before the call the state is read, after it the state is read again, and the answer body of the
write is never kept.

`preview` answers what would happen and a digest of it (the approved artefact, ADR 0062). `run` reads again, refuses
unless the digest is the approved one, records the apply once per change id, makes the call, waits for it on the
management host only, and verifies. The route in `app.py` calls these two functions and nothing else of this module.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
import yaml

from cloud_executor import arm, cost, store, templates
from cloud_executor.runner import Refusal

FILE = Path(__file__).resolve().parent / "actions.yaml"
#: The whole list. An entry in `actions.yaml` that is not named here is refused when the file is loaded.
ALLOWED = ("vm_start", "site_start", "aks_start", "pg_flexible_start", "vmss_scale_out", "plan_scale_out", "tags_merge")
TAGS_SUFFIX = "/providers/Microsoft.Resources/tags/default"
#: kind -> (the only method, the only suffix) it may use.
KINDS = {"start": ("POST", "/start"), "scale_out": ("PATCH", ""), "tags": ("PATCH", TAGS_SUFFIX)}
TIMEOUT = 30
WAIT_SECONDS = 300
POLL_SECONDS = 5
MAX_CAPACITY = 1000
MAX_STEP = 20
MAX_TAGS = 20
MAX_TAGS_ON_ONE = 50
_API = re.compile(r"\d{4}-\d{2}-\d{2}")
_GUID = r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}"
_NAME = r"[A-Za-z0-9._()-]{1,90}"
_ID = re.compile(rf"/subscriptions/(?P<sub>{_GUID})/resourcegroups/(?P<group>{_NAME})"
                 rf"(?:/providers/(?P<ns>[A-Za-z0-9.]+)/(?P<type>[A-Za-z0-9]+)/(?P<name>{_NAME}))?", re.IGNORECASE)
_sleep = time.sleep  # a test replaces it


class ActionsError(Exception):
    """`actions.yaml` is malformed or asks for something the product never does. The message names the entry."""


@dataclass(frozen=True)
class Action:
    name: str
    kind: str
    resource_type: str
    method: str
    suffix: str
    api_version: str
    risk: str
    current: dict[str, Any]
    verify: dict[str, Any]
    requires: str
    backout: str


def kind_of(name: str) -> str:
    return "tags" if name == "tags_merge" else "scale_out" if name.endswith("_scale_out") else "start"


def load(path: Path = FILE) -> dict[str, Action]:
    """The reviewed actions by name. Anything outside the contract is a refusal to load, so a bad file runs nothing."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ActionsError(f"actions.yaml cannot be read: {error}") from error
    found: dict[str, Action] = {}
    for entry in data or []:
        name = str(entry.get("name") if isinstance(entry, dict) else "")
        if name not in ALLOWED or name in found:
            raise ActionsError(f"`{name}` is not an action this product has, or it is listed twice.")
        call, kind = entry.get("call") or {}, kind_of(name)
        method, suffix = KINDS[kind]
        if call.get("method") != method or call.get("suffix") != suffix or not _API.fullmatch(str(call.get("api_version"))):
            raise ActionsError(f"{name}: a {kind} is {method} with the suffix '{suffix}' and a dated api-version, nothing else.")
        if entry.get("risk") not in ("low", "high") or (kind == "scale_out" and entry["risk"] != "high"):
            raise ActionsError(f"{name}: risk is low or high, and a scale-out is high.")
        if not str(entry.get("requires") or "").strip() or not str(entry.get("backout") or "").strip() or entry.get("response") != "drop":
            raise ActionsError(f"{name}: it needs `requires`, `backout` and `response: drop`.")
        if not isinstance(entry.get("current"), dict) or not isinstance(entry.get("verify"), dict):
            raise ActionsError(f"{name}: it needs `current` and `verify`.")
        found[name] = Action(name, kind, str(entry.get("resource_type") or "").lower(), method, suffix, str(call["api_version"]),
                             entry["risk"], entry["current"], entry["verify"], str(entry["requires"]), str(entry["backout"]).strip())
    return found


@lru_cache(maxsize=1)
def catalogue() -> dict[str, Action]:
    return load()


# ---------------------------------------------------------------------------------------------------- checking

@dataclass
class Plan:
    action: Action
    resource_id: str
    name: str
    sub: str
    group: str
    variables: dict[str, Any]
    body: dict[str, Any] | None
    state: str
    after: str
    line: str
    digest: str
    lines: list[str]
    checks: list[dict[str, Any]]
    cost: dict[str, Any]
    location: str


def _variables(action: Action, raw: Any) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    if action.kind == "start":
        if raw:
            raise Refusal("gate", f"{action.name} takes no values.")
        return {}
    if action.kind == "scale_out":
        capacity = raw.get("capacity")
        if set(raw) != {"capacity"} or isinstance(capacity, bool) or not isinstance(capacity, int) or not 1 <= capacity <= MAX_CAPACITY:
            raise Refusal("gate", f"{action.name} takes one value, `capacity`: a whole number from 1 to {MAX_CAPACITY}.")
        return {"capacity": capacity}
    tags = raw.get("tags")
    if set(raw) != {"tags"} or not isinstance(tags, dict) or not 1 <= len(tags) <= MAX_TAGS or not all(
            isinstance(k, str) and isinstance(v, str) and templates._TAG_KEY.fullmatch(k) and v and templates._TAG_VALUE.fullmatch(v)
            for k, v in tags.items()):
        raise Refusal("gate", f"tags_merge takes one value, `tags`: 1 to {MAX_TAGS} name and value pairs; a value is never empty and "
                              "none holds < > % & \\ ? /.")
    return {"tags": dict(sorted(tags.items()))}


def check_request(body: dict[str, Any], rails: dict[str, Any]) -> tuple[Action, str, str, str, str, dict[str, Any]]:
    """Everything that needs no network: `(action, resource id, name, subscription, group, variables)` or `Refusal`."""
    action = catalogue().get(str(body.get("action") or ""))
    if action is None:
        raise Refusal("gate", f"`{body.get('action')}` is not a simple action. They are: {', '.join(ALLOWED)}.")
    rid = str(body.get("resource_id") or "").strip()
    found = _ID.fullmatch(rid)
    if not found or (action.kind != "tags" and f"{found['ns']}/{found['type']}".lower() != action.resource_type):
        raise Refusal("gate", f"{action.name} acts on one {action.resource_type} named by its full resource ID.")
    sub, group = found["sub"].lower(), found["group"].lower()
    if sub not in {str(s).lower() for s in rails.get("subscriptions") or []}:
        raise Refusal("policy", f"The subscription {sub} is not one this product may change.")
    if group not in {str(g).lower() for g in (rails.get("resource_groups") or {}).get("existing") or []}:
        raise Refusal("policy", f"The resource group {found['group']} is not one this product may change.")
    return action, rid, found["name"] or found["group"], sub, group, _variables(action, body.get("variables"))


def _pluck(doc: Any, path: str) -> Any:
    for part in path.split("."):
        doc = doc.get(part) if isinstance(doc, dict) else None
    if isinstance(doc, list):  # an instance view: the one PowerState code among the statuses
        codes = [str(i.get("code")) for i in doc if isinstance(i, dict) and str(i.get("code", "")).startswith("PowerState/")]
        return codes[0] if codes else None
    return doc


def _read(action: Action, rid: str, spec: dict[str, Any], token: str) -> Any:
    doc = arm.get(token, f"{rid}{spec['via']}?api-version={action.api_version}")
    if doc is None:
        raise Refusal("read", "Azure does not have that resource now.")
    return doc


def _scale_cost(action: Action, rid: str, doc: dict[str, Any], delta: int, rails: dict[str, Any]) -> dict[str, Any]:
    """The list price of `delta` more instances, by the plan estimator's own rules. Compute only, priced as Linux."""
    sku = str((doc.get("sku") or {}).get("name") or "")
    kind, attr = ("azurerm_service_plan", "sku_name") if action.name == "plan_scale_out" else ("azurerm_linux_virtual_machine", "size")
    after = {"location": str(doc.get("location") or ""), attr: sku}
    fake = {"resource_changes": [{"address": f"{rid}#{i}", "mode": "managed", "type": kind, "change": {"actions": ["create"], "after": after}}
                                 for i in range(delta)]}
    estimate = cost.estimate(fake)
    checks, refusals = cost.policy(estimate, rails, store.committed_usd())
    if refusals:
        raise Refusal("cost", "\n".join(refusals[:3]))
    return {**estimate, "note": "compute only at list price, as Linux; licences, disks and data transfer are extra"}


def build(body: dict[str, Any], rails: dict[str, Any]) -> Plan:
    """Check the request, read Azure as the planner identity, and decide exactly what the one call would do."""
    action, rid, name, sub, group, variables = check_request(body, rails)
    token = arm.token("planner")
    doc = _read(action, rid, action.current, token)
    state, no_cost = _pluck(doc, action.current["path"]), {"monthly_usd": None, "items": [], "unknown": [], "priced_at": "",
                                                           "note": "resumes billing for what already exists; no new resource"}
    checks = [{"label": "Reviewed action", "ok": True, "detail": action.name},
              {"label": "Scope", "ok": True, "detail": f"subscription {sub}, resource group {group}"}]
    body_out: dict[str, Any] | None = None
    price, location = no_cost, str(doc.get("location") or "") if isinstance(doc, dict) else ""
    if action.kind == "start":
        if state not in action.current["startable"]:
            raise Refusal("policy", f"{name} is {state or 'in a state that cannot be read'}, so there is nothing to start.")
        after, lines = str(action.verify["expect"]), [f"~ {name}  start: {state} -> {action.verify['expect']}"]
        checks.append({"label": "Stopped now", "ok": True, "detail": str(state)})
    elif action.kind == "scale_out":
        want = variables["capacity"]
        if not isinstance(state, int) or isinstance(state, bool):
            raise Refusal("read", f"The current instance count of {name} cannot be read.")
        sku = doc.get("sku") or {}
        if want <= state or want - state > MAX_STEP or not sku.get("name"):
            raise Refusal("policy", f"{name} has {state} instances. A scale-out must be higher (never lower, which is a scale-in the product "
                                    f"does not do), at most {MAX_STEP} more at a time, and the size stays {sku.get('name') or 'as it is'}.")
        keep = {k: sku[k] for k in ("name", "tier") if sku.get(k)}
        body_out, after = {"sku": {**keep, "capacity": want}}, str(want)
        lines = [f"~ {name}  instances: {state} -> {want}  (size {sku['name']} unchanged)"]
        price = _scale_cost(action, rid, doc, want - state, rails)
        checks.append({"label": "Cost", "ok": True, "detail": f"about {price['monthly_usd']} USD a month more ({price['note']})"})
    else:
        tags = {k: v for k, v in variables["tags"].items() if (state or {}).get(k) != v} if isinstance(state, dict) or state is None else {}
        if not tags:
            raise Refusal("policy", f"{name} already has those tags.")
        if len(set(state or {}) | set(tags)) > MAX_TAGS_ON_ONE:
            raise Refusal("policy", f"{name} would have more than {MAX_TAGS_ON_ONE} tags, the most Azure allows.")
        body_out, after = {"operation": "Merge", "properties": {"tags": tags}}, json.dumps(tags, sort_keys=True)
        lines = [f"~ {name}  tag {k}: {(state or {}).get(k, '(none)')} -> {v}" for k, v in sorted(tags.items())]
        state = json.dumps({k: (state or {}).get(k) for k in tags}, sort_keys=True)
        checks.append({"label": "Tags are added or changed, never removed", "ok": True, "detail": ", ".join(sorted(tags))})
        checks.append({"label": "Tags can drive automation", "ok": True, "level": "warn",
                       "detail": "A tag such as one an auto-shutdown or a policy reads can change what runs. Check what reads it."})
    line = f"{action.method} {arm.MANAGEMENT}{rid}{action.suffix}?api-version={action.api_version}" + (
        " " + json.dumps(body_out, sort_keys=True) if body_out else "")
    digest = hashlib.sha256(json.dumps({"action": action.name, "resource": rid.lower(), "call": [action.method, action.suffix, action.api_version],
                                        "body": body_out, "before": state}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return Plan(action, rid, name, sub, group, variables, body_out, str(state), after, line, digest, lines, checks, price, location)


def preview(body: dict[str, Any], rails: dict[str, Any]) -> dict[str, Any]:
    """`POST /v1/action` with `mode: preview`. A refusal is an answer (`ok: false, stage, refusal`), never an exception."""
    try:
        plan = build(body, rails)
    except Refusal as refusal:
        return {"ok": False, "stage": refusal.stage, "refusal": refusal.text}
    except arm.ArmError as error:
        return {"ok": False, "stage": "read", "refusal": str(error)}
    verb = {"start": "start", "scale_out": "scale out", "tags": "add tags"}[plan.action.kind]
    resource = {"address": plan.resource_id, "type": plan.action.resource_type if plan.action.kind != "tags" else "tags", "action": verb,
                "name": plan.name, "before": {"state": plan.state}, "after": {"state": plan.after}}
    return {"ok": True, "action": plan.action.name, "line": plan.line, "plan_digest": plan.digest, "current": {"state": plan.state},
            "checks": plan.checks, "summary": {"add": 0, "change": 1, "import": 0, "lines": plan.lines, "resources": [resource]},
            "cost": plan.cost, "risk": plan.action.risk, "backout": plan.action.backout, "requires": plan.action.requires,
            "scope": {"subscription": plan.sub, "resource_group": plan.group, "region": plan.location}}


# --------------------------------------------------------------------------------------------------- the call

def _fail(text: str) -> dict[str, Any]:
    return {"status": "failed", "verified": None, "changed_ids": [], "error": text}


def _error_text(reply: requests.Response) -> str:
    try:
        error = reply.json().get("error") or {}
        return f"Azure answered {reply.status_code}: {str(error.get('code') or '')[:80]} {str(error.get('message') or '')[:200]}".strip()
    except (ValueError, AttributeError):
        return f"Azure answered {reply.status_code}."


def _same_host(url: str) -> bool:
    found = urlparse(url)
    return found.scheme == "https" and found.hostname == urlparse(arm.MANAGEMENT).hostname and not found.username


def _write(plan: Plan, token: str) -> requests.Response:
    """The one place a change leaves this module. POST for a start, PATCH for the rest; never follows a redirect."""
    url = f"{arm.MANAGEMENT}{plan.resource_id}{plan.action.suffix}?api-version={plan.action.api_version}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        if plan.action.method == "POST":
            return requests.post(url, headers=headers, timeout=TIMEOUT, allow_redirects=False)
        return requests.patch(url, headers=headers, json=plan.body, timeout=TIMEOUT, allow_redirects=False)
    except requests.RequestException as error:
        raise arm.ArmError(f"Azure could not be reached: {type(error).__name__}.") from error


def _wait(reply: requests.Response, token: str, deadline: float) -> None:
    """Follow an accepted call to its end on the management host only, at Retry-After, until the deadline."""
    if reply.status_code != 202:
        return
    url = reply.headers.get("Azure-AsyncOperation") or reply.headers.get("Location") or ""
    operation = "Azure-AsyncOperation" in reply.headers
    while True:
        if not _same_host(url):
            raise arm.ArmError("Azure's answer pointed somewhere other than the management host, so it was not followed.")
        if time.monotonic() >= deadline:
            raise arm.ArmError(f"Azure had not finished after {WAIT_SECONDS // 60} minutes. Check the portal; nothing is retried.")
        try:
            _sleep(min(max(int(reply.headers.get("Retry-After") or POLL_SECONDS), 1), 30))
            reply = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT, allow_redirects=False)
            status = str(reply.json().get("status") or "") if operation and reply.status_code == 200 else ""
        except (requests.RequestException, ValueError) as error:
            raise arm.ArmError(f"Azure could not be reached or answered oddly: {type(error).__name__}.") from error
        if reply.status_code not in (200, 202, 204):
            raise arm.ArmError(_error_text(reply))
        if status in ("Failed", "Canceled"):
            raise arm.ArmError(f"Azure ended the operation as {status}.")
        if (operation and status in ("", "InProgress", "Running", "Accepted")) or (not operation and reply.status_code == 202):
            continue
        return


def _verified(plan: Plan, token: str) -> bool:
    try:
        got = _pluck(_read(plan.action, plan.resource_id, plan.action.verify, token), plan.action.verify["path"])
    except (arm.ArmError, Refusal):
        return False  # unreadable now is not verified; the caller reads again until the deadline
    if plan.action.kind == "tags":
        return isinstance(got, dict) and all(got.get(k) == v for k, v in plan.body["properties"]["tags"].items())
    return str(got) == str(plan.action.verify["expect"]).format(**plan.variables)


def run(body: dict[str, Any], rails: dict[str, Any]) -> dict[str, Any]:
    """The job body of a `run` action: read again, refuse unless the digest is the approved one, record once, call, verify.

    Returns the job fields (`status`, `verified`, `changed_ids`, `error`). `changed_ids` is always empty: nothing was made.
    The request has already been checked for the reference and for a change id used before.
    """
    try:
        plan = build(body, rails)
        if plan.digest != body.get("plan_digest"):
            return {"status": "superseded", "verified": None, "changed_ids": [],
                    "error": "Azure is not as it was when this was approved. Draft it again."}
        try:
            store.record_apply(workspace=f"{plan.sub}/{plan.group}", state_name="", change_id=body["change_id"], code=plan.line,
                               variables=plan.variables, plan_digest=plan.digest, reference=str(body["reference"]).strip().upper())
        except store.AlreadyApplied as error:
            return _fail(str(error))
        deadline = time.monotonic() + WAIT_SECONDS
        applier = arm.token("applier")
        reply = _write(plan, applier)
        if reply.status_code not in (200, 202, 204):
            return _fail(_error_text(reply))
        _wait(reply, applier, deadline)
        if plan.cost.get("monthly_usd"):
            store.commit_usd(body["change_id"], plan.cost["monthly_usd"])
        planner = arm.token("planner")
        while not (ok := _verified(plan, planner)) and time.monotonic() < deadline:
            _sleep(POLL_SECONDS)
    except Refusal as refusal:
        return _fail(f"{refusal.stage}: {refusal.text}")
    except arm.ArmError as error:
        return _fail(str(error))
    if ok:
        return {"status": "applied", "verified": True, "changed_ids": []}
    return {"status": "verify_failed", "verified": False, "changed_ids": [],
            "error": "Azure accepted the call but the state read back is not the intended one. Check it in the portal."}
