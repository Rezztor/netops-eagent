"""Does the change app hold the reviewed role and nothing else? Changes stay off until it does (ADR 0063, 0065).

The role in `role.json` is the second wall: it grants `*/read`, `*/write` and a short list of named actions, and
withholds every delete and every stop-like action. `evaluate` is a pure function over the role definitions the app
holds; `run` reads them from Azure with the applier's own token. The judgement is deliberately stricter than the
contract's minimum: an action the reviewed role does not list is a reason too, so a role someone widened by hand is
caught even when the new action is not on the forbidden list yet.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cloud_executor import arm

ROLE = json.loads((Path(__file__).resolve().parent / "role.json").read_text(encoding="utf-8"))
GRANTED = {a.lower() for a in ROLE["Actions"]}
FORBIDDEN = {a.lower() for a in ROLE["NotActions"]}
#: The only wildcards a granted action may contain.
WILDCARDS_OK = {"*/read", "*/write"}
MAX_SUBSCRIPTIONS = 25


def evaluate(definitions: list[dict[str, Any]]) -> str:
    """The reason the role is not acceptable, or `""` when it is. `definitions` are role definition resources."""
    if not definitions:
        return "The change app holds no role on any subscription it can see."
    if len(definitions) > 1:
        names = ", ".join(sorted(str((d.get("properties") or {}).get("roleName")) for d in definitions))
        return f"The change app holds more than one role ({names}). It may hold exactly one."
    props = definitions[0].get("properties") or {}
    permissions = props.get("permissions") or []
    if not permissions:
        return "The role has no permissions block that can be read."
    not_actions: set[str] = set()
    for block in permissions:
        if block.get("dataActions"):
            return "The role grants data actions, which the reviewed role never does."
        not_actions |= {str(a).lower() for a in block.get("notActions") or []}
        for action in block.get("actions") or []:
            low = str(action).lower()
            if low in FORBIDDEN:
                return f"The role grants {action}, which is on the forbidden list."
            if low.endswith("/delete"):
                return f"The role grants {action}, which deletes."
            if "*" in low and low not in WILDCARDS_OK:
                return f"The role grants {action}, a wildcard the reviewed role does not use."
            if low not in GRANTED:
                return f"The role grants {action}, which the reviewed role does not list."
    missing = sorted(FORBIDDEN - not_actions)
    if missing:
        return f"The role does not withhold {len(missing)} actions the reviewed role withholds (first: {missing[0]})."
    return ""


def run(role: str = "applier") -> dict[str, Any]:
    """Sign in as `role`, read its role assignments and definitions, and judge them. Never returns a token."""
    try:
        access = arm.token(role)
        oid = str(arm.claims(access).get("oid") or "")
        if not oid:
            return {"role_ok": False, "reason": "The token names no object ID, so its role assignments cannot be read."}
        listed = (arm.get(access, "/subscriptions?api-version=2022-12-01") or {}).get("value") or []
        wanted: dict[str, str] = {}
        for sub in listed[:MAX_SUBSCRIPTIONS]:
            path = (f"/subscriptions/{sub['subscriptionId']}/providers/Microsoft.Authorization/roleAssignments"
                    f"?api-version=2022-04-01&$filter=principalId%20eq%20'{oid}'")
            for assignment in (arm.get(access, path) or {}).get("value") or []:
                role_id = str((assignment.get("properties") or {}).get("roleDefinitionId") or "")
                if role_id.startswith("/"):
                    wanted[role_id.lower()] = role_id
        definitions = [d for d in (arm.get(access, f"{rid}?api-version=2022-04-01") for rid in wanted.values()) if d]
    except arm.ArmError as error:
        return {"role_ok": False, "reason": str(error)}
    reason = evaluate(definitions)
    return {"role_ok": not reason, "reason": reason, "subscriptions": [str(s.get("subscriptionId")) for s in listed]}
