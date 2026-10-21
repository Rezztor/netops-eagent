"""Adoption: bring resources that already exist in Azure under the product's records, without changing them (ADR 0071).

The application sends resources it read from its own stored inventory (`{id, name, location?, tags?, parent?}`); this
module makes the OpenTofu code itself, from validated values only: one `import` block and one `azapi_resource` for each, with
the Azure type taken from a reviewed table (`ARM_TYPES`: a type that is not in it cannot be adopted). It plans in `import`
mode and **judges that plan harder than any other**: every managed change must be an import with the action `no-op`, and the
set imported must be exactly the set asked for. A plan that would add, change, replace or delete anything is refused: the
code did not match Azure, and adoption proves nothing in Azure moves.

`apply` writes the imports into the state and runs as the **planner** (Reader), which cannot change Azure even if it wanted
to, so it needs no CR or SR number. It copies the state into `cloud_state_version` first, once per change id. The digest
covers the mode, the workspace, the resources and every changed attribute, so it is never an apply's digest.

Wiring, in `app.py`: `/v1/plan` calls `adopt.plan` when `mode` is `import`, and `POST /v1/adopt/apply` calls
`adopt.apply`, both inside `_one_at_a_time`.

The top of this file is plain Python with no executor import, because the application imports `ARM_TYPES` and `describe`
to decide what to offer; `runner` and `store` are imported where they are used.

# unverified: the import ID with `?api-version=` is the form azapi documents (registry docs for azapi_resource, "Import");
# the api-versions are dated releases of Microsoft's ARM reference, and whether a minimal `azapi_resource` (no `body`) plans as
# no-op after an import is not run against azapi 2.13 yet. The plan is the proof: if it does not match, adoption is refused.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

from cloud_executor import gate

#: lower-case ARM type -> (the type as Azure names it, the api-version of the azapi `type`). Reviewed text: adding a type
#: here makes it adoptable, so a type that carries code or secrets (extensions, run commands, vaults, role assignments)
#: stays out, and `gate.check_code` refuses the denied ones again.
ARM_TYPES: dict[str, tuple[str, str]] = {
    "microsoft.network/virtualnetworks": ("Microsoft.Network/virtualNetworks", "2024-05-01"),
    "microsoft.network/virtualnetworks/subnets": ("Microsoft.Network/virtualNetworks/subnets", "2024-05-01"),
    "microsoft.network/virtualnetworks/virtualnetworkpeerings": ("Microsoft.Network/virtualNetworks/virtualNetworkPeerings", "2024-05-01"),
    "microsoft.network/networksecuritygroups": ("Microsoft.Network/networkSecurityGroups", "2024-05-01"),
    "microsoft.network/networksecuritygroups/securityrules": ("Microsoft.Network/networkSecurityGroups/securityRules", "2024-05-01"),
    "microsoft.network/routetables": ("Microsoft.Network/routeTables", "2024-05-01"),
    "microsoft.network/routetables/routes": ("Microsoft.Network/routeTables/routes", "2024-05-01"),
    "microsoft.network/publicipaddresses": ("Microsoft.Network/publicIPAddresses", "2024-05-01"),
    "microsoft.network/networkinterfaces": ("Microsoft.Network/networkInterfaces", "2024-05-01"),
    "microsoft.network/privateendpoints": ("Microsoft.Network/privateEndpoints", "2024-05-01"),
    "microsoft.network/natgateways": ("Microsoft.Network/natGateways", "2024-05-01"),
    "microsoft.network/privatednszones": ("Microsoft.Network/privateDnsZones", "2024-06-01"),
    "microsoft.network/privatednszones/virtualnetworklinks": ("Microsoft.Network/privateDnsZones/virtualNetworkLinks", "2024-06-01"),
    "microsoft.compute/virtualmachines": ("Microsoft.Compute/virtualMachines", "2025-04-01"),
    "microsoft.compute/disks": ("Microsoft.Compute/disks", "2024-03-02"),
    "microsoft.storage/storageaccounts": ("Microsoft.Storage/storageAccounts", "2023-05-01"),
    "microsoft.web/serverfarms": ("Microsoft.Web/serverfarms", "2024-04-01"),
    "microsoft.web/sites": ("Microsoft.Web/sites", "2024-04-01"),
    "microsoft.operationalinsights/workspaces": ("Microsoft.OperationalInsights/workspaces", "2023-09-01"),
}
MAX_ITEMS = 100
_ID = re.compile(r"/subscriptions/([0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12})/resourcegroups/([^/]+)/providers/([a-z0-9.]+)/"
                 r"([^/]+/[^/]+(?:/[^/]+/[^/]+)?)")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._()-]{0,259}")
_REGION = re.compile(r"[a-z0-9]{3,30}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class Rejected(ValueError):
    """An item the adoption will not take. The message is shown to a person."""


def describe(native_id: Any) -> dict[str, str] | None:
    """`{subscription, resource_group, arm_type, api_version, name}` for an ID of an adoptable type, else None."""
    m = _ID.fullmatch(str(native_id or "").strip().lower())
    if not m:
        return None
    sub, group, namespace, rest = m.groups()
    parts = rest.split("/")
    known = ARM_TYPES.get(f"{namespace}/{'/'.join(parts[0::2])}")
    return {"subscription": sub, "resource_group": group, "arm_type": known[0], "api_version": known[1], "name": parts[-1]} if known else None


def _hcl(text: str) -> str:
    """A quoted HCL string: JSON escaping is valid HCL, and `${` and `%{` are made literal."""
    return json.dumps(text).replace("${", "$${").replace("%{", "%%{")


def _item(raw: Any, sub: str, group: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise Rejected("Each resource to adopt is an object with an id.")
    native = str(raw.get("id") or "").strip()
    found = describe(native)
    if not found:
        raise Rejected(f"{native[:120] or '(no id)'} is not a resource ID of a type that can be adopted.")
    if found["subscription"] != sub.lower() or found["resource_group"] != group.lower():
        raise Rejected(f"{native[:120]} is not in the workspace's subscription and resource group.")
    name = str(raw.get("name") or "")
    if not _NAME.fullmatch(name) or name.lower() != found["name"]:
        raise Rejected(f"The name {name[:80]!r} does not match its ID or has characters adoption does not accept.")
    location = str(raw.get("location") or "").strip().lower()
    if location and not _REGION.fullmatch(location):
        raise Rejected(f"The location {location[:40]!r} is not an Azure region name.")
    tags = raw.get("tags") or {}
    if not isinstance(tags, dict) or len(tags) > 50 or not all(
            isinstance(k, str) and 0 < len(k) <= 128 and isinstance(v, str) and len(v) <= 256
            and not _CONTROL.search(k + v) for k, v in tags.items()):
        raise Rejected(f"The tags of {name} are not up to 50 plain text pairs.")
    parent = str(raw.get("parent") or "")
    return {"id": native.lower(), "name": name, "location": location, "tags": dict(sorted(tags.items())), "parent": parent,
            "arm_type": found["arm_type"], "api_version": found["api_version"]}


def _proper_ids(item: dict[str, Any], sub: str, group: str) -> tuple[str, str]:
    """`(parent_id, id)` with the casing Azure uses: the type's words from the table, the names the application read."""
    namespace, _, types = item["arm_type"].partition("/")
    leaves = types.split("/")
    base = f"/subscriptions/{sub.lower()}/resourceGroups/{group}"
    if len(leaves) == 1:
        return base, f"{base}/providers/{namespace}/{leaves[0]}/{item['name']}"
    # a child: the parent's name is the next-to-last segment of the lower-cased ID unless the application sent its own casing
    parent_name = item["parent"] if item["parent"].lower() == item["id"].split("/")[-3] else item["id"].split("/")[-3]
    parent_id = f"{base}/providers/{namespace}/{leaves[0]}/{parent_name}"
    return parent_id, f"{parent_id}/{leaves[1]}/{item['name']}"


def render(items: list[dict[str, Any]], sub: str, group: str) -> str:
    """The code: an import block and an `azapi_resource` for each item, in the order of the sorted IDs."""
    blocks = []
    for n, item in enumerate(items, 1):
        parent_id, full_id = _proper_ids(item, sub, group)
        address = f"azapi_resource.r{n}"
        import_id = f"{full_id}?api-version={item['api_version']}"
        lines = [f"import {{\n  to = {address}\n  id = {_hcl(import_id)}\n}}",
                 f"resource \"azapi_resource\" \"r{n}\" {{",
                 f"  type      = {_hcl(item['arm_type'] + '@' + item['api_version'])}",
                 f"  name      = {_hcl(item['name'])}",
                 f"  parent_id = {_hcl(parent_id)}"]
        if item["location"]:
            lines.append(f"  location  = {_hcl(item['location'])}")
        if item["tags"]:
            lines.append("  tags = {\n" + "".join(f"    {_hcl(k)} = {_hcl(v)}\n" for k, v in item["tags"].items()) + "  }")
        blocks.append("\n".join(lines) + "\n}")
    return "\n\n".join(blocks) + "\n"


def resolve(source: Any, sub: str, group: str, rails: dict[str, Any]):
    """The `runner.Source` for `{adopt: [...]}`, or `runner.Refusal`. The code is made here and checked by the static gate."""
    from cloud_executor import runner  # noqa: PLC0415 - the top of this file stays importable by the application

    if not isinstance(source, dict) or set(source) != {"adopt"} or not isinstance(source["adopt"], list):
        raise runner.Refusal("gate", "Adoption names the resources to adopt: a source of {adopt: [{id, name, ...}]}.")
    if sub not in {s.lower() for s in rails["subscriptions"]}:
        raise runner.Refusal("gate", f"The subscription {sub} is not one this product may change.")
    if not 1 <= len(source["adopt"]) <= MAX_ITEMS:
        raise runner.Refusal("gate", f"Adopt from 1 to {MAX_ITEMS} resources at a time.")
    try:
        items = sorted((_item(r, sub, group) for r in source["adopt"]), key=lambda i: i["id"])
    except Rejected as error:
        raise runner.Refusal("gate", str(error)) from error
    if len({i["id"] for i in items}) != len(items):
        raise runner.Refusal("gate", "A resource is listed twice.")
    code = render(items, sub, group)
    refusals = gate.check_code({"main.tf": code})
    if refusals:
        raise runner.Refusal("gate", refusals[0])
    digest_items = [{k: i[k] for k in ("id", "name", "location", "tags")} for i in items]
    key = f"{sub}/{group}/adopt/{hashlib.sha256(json.dumps([i['id'] for i in items]).encode()).hexdigest()[:16]}"
    return runner.Source({"main.tf": code}, None,
                         {"template": "adopt", "may_change": [], "restarts_existing": False, "subscription": sub,
                          "resource_group": group, "target_ids": []},
                         {"adopt": digest_items, "mode": "import"}, key, {}), [i["id"] for i in items]


def _imported(plan: dict[str, Any]) -> set[str]:
    return {str((rc["change"].get("importing") or {}).get("id", "")).split("?")[0].lower()
            for rc in gate.managed_changes(plan) if (rc.get("change") or {}).get("importing")}


def judge(plan: dict[str, Any], src: Any, wanted: list[str], rails: dict[str, Any]) -> dict[str, Any]:
    """The policy, then the adoption rule, the digest and the summary; or `runner.Refusal`.

    The adoption rule: every managed resource in the plan is an import whose only action is `no-op`, and what is imported is
    exactly what was asked for. Anything else (an add, a change, a replace, a delete, a forget, a resource nobody asked for,
    one that was asked for and is missing) refuses the whole plan."""
    from cloud_executor import runner  # noqa: PLC0415

    sub, group = src.draft["subscription"], src.draft["resource_group"]
    wide = {**rails, "resource_types": sorted({*(rails.get("resource_types") or []), "azapi_resource"})}  # nothing is made, so only this type
    checks, refusals = gate.check_plan(plan, wide, src.draft, set(wanted))
    if refusals:
        raise runner.Refusal("policy", "\n".join(refusals[:5]))
    managed = gate.managed_changes(plan)
    moving = [rc for rc in managed if list((rc.get("change") or {}).get("actions") or []) != ["no-op"]
              or not (rc.get("change") or {}).get("importing")]
    if moving or _imported(plan) != set(wanted) or len(managed) != len(wanted):
        summary = gate.summarise(plan)
        detail = "\n".join(summary["lines"][:8]) or "the plan does not import exactly what was asked for"
        raise runner.Refusal("policy", f"Adoption must import {len(wanted)} and change 0; this plan does not, so the code does not "
                                       f"match what is in Azure and nothing was adopted:\n{detail}")
    summary = gate.summarise(plan)
    checks.append({"label": f"Importing {len(wanted)}, changes 0", "ok": True, "detail": "every resource is an import with no change"})
    return {"plan_digest": gate.plan_digest(plan, src.digest_source, {"subscription": sub, "resource_group": group}),
            "summary": summary, "checks": checks, "adopting": wanted, "drift": [],
            "cost": {"monthly_usd": 0.0, "items": [], "unknown": [], "priced_at": ""}}


def plan(body: dict[str, Any], rails: dict[str, Any], catalogue: Any = None, lookup: Any = None) -> dict[str, Any]:
    """`POST /v1/plan` with `mode: import`. Same signature as `runner.plan`; a refusal is an answer, not an exception."""
    from cloud_executor import runner  # noqa: PLC0415

    started = time.monotonic()
    try:
        if body.get("mode") != "import":
            raise runner.Refusal("gate", "This plans an adoption: mode import.")
        sub, group = runner.workspace_of(body)
        src, wanted = resolve(body.get("source"), sub, group, rails)
        with runner.workdir(src) as work:
            env, secrets = runner.environment("planner", work)
            result = judge(runner.plan_in(work, src, "import", env, secrets), src, wanted, rails)
    except runner.Refusal as refusal:
        return {"ok": False, "stage": refusal.stage, "refusal": refusal.text}
    return {"ok": True, **result, "duration_s": round(time.monotonic() - started, 1)}


def apply(body: dict[str, Any], rails: dict[str, Any], catalogue: Any = None) -> dict[str, Any]:
    """`POST /v1/adopt/apply`: plan again, and only if the plan is the approved digest write the imports into the state.

    Needs no reference: nothing in Azure changes. Runs as the planner. The state is copied into `cloud_state_version`
    first (reference empty), and a change id is adopted once."""
    from cloud_executor import runner, store  # noqa: PLC0415

    try:
        sub, group = runner.workspace_of(body)
        src, wanted = resolve(body.get("source"), sub, group, rails)
        with runner.workdir(src) as work:
            env, secrets = runner.environment("planner", work)
            result = judge(runner.plan_in(work, src, "import", env, secrets, keep=True), src, wanted, rails)
            if result["plan_digest"] != body.get("plan_digest"):
                return {"ok": False, "stage": "policy", "superseded": True,
                        "refusal": "Azure is not as it was when the adoption was shown, so nothing was adopted. Ask again."}
            try:
                store.record_apply(workspace=src.state_key, state_name=src.workspace_name, change_id=body["change_id"],
                                   code=src.code, variables=src.variables, plan_digest=body["plan_digest"], reference="")
            except store.AlreadyApplied as error:
                return {"ok": False, "stage": "policy", "refusal": str(error)}
            code, _out, err = runner.tofu(["apply", "-input=false", "-no-color", "-lock-timeout=60s", "plan.bin"], work, env, secrets)
            if code:
                return {"ok": False, "stage": "plan", "refusal": f"adopting into the state failed: {err}"}
    except runner.Refusal as refusal:
        return {"ok": False, "stage": refusal.stage, "refusal": refusal.text}
    return {"ok": True, "plan_digest": result["plan_digest"], "adopted": wanted}
