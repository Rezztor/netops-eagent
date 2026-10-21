"""Ask the stored Azure inventory: counts, lists, and one resource with its links. Read-only by construction: it
imports `store`, `categories`, `exposure`, `waste` and `redact`, so it never contacts Azure; a test asserts the
imports. The one network use is `waste.priced` for `attention: waste`, which sends prices.azure.com a region and a
SKU and nothing else. Every string that came from Azure passes `redact.redact_text`. The answer always starts with
the age of the data. Gated on `inventory.view` in `helpers/tool_permissions.py`.
"""

from __future__ import annotations

import datetime as _dt
import json
from typing import Any

from helpers.tool import Response, Tool

from plugins._ea_azure.helpers import categories, exposure, signals, store, waste
from plugins._ea_inventory.helpers import redact

LIST_CAP, GROUP_CAP, LINK_CAP, SIGNAL_CAP = 25, 50, 60, 10
STATES = ("running", "stopped", "failed", "ok", "unknown")
_PASSED = ("query", "subscription", "resource_group", "category", "type", "region", "state", "tag", "without",
           "changed_since", "include_removed", "attention")
_SUBNET = "microsoft.network/virtualnetworks/subnets"


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


PERSON = "a user (the name is shown on the Inventory screen)"


def _clean(value: Any) -> Any:
    """Everything Azure named, tagged or addressed goes through the redactor, keys included."""
    if isinstance(value, str):
        return redact.redact_text(value)[0]
    if isinstance(value, dict):
        out = {_clean(k): _clean(v) for k, v in value.items()}
        # A change record's author is a person's sign-in name: the screen shows it to engineers, the model never sees
        # it (build contract, phase 1). An application or a managed identity stays named; it is not a person.
        if "changed_by" in out and (str(out.get("changed_by_type") or "").lower() == "user" or "@" in str(out["changed_by"])):
            out["changed_by"] = PERSON
        return out
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def _header(asof: dict[str, Any]) -> str:
    pull = asof["last_pull"]
    days = (_now() - _dt.datetime.fromisoformat(pull["started_at"])).days
    text = f"Azure inventory as of {pull['started_at']} ({days} day{'' if days == 1 else 's'} ago)."
    counts = pull.get("counts") or {}
    failed = {s.get(k) for s in counts.get("per_subscription") or [] if s.get("status") != "ok" for k in ("id", "name")}
    late = [
        f"{s['name']} (last read {s['last_read_at'] or 'never'})" for s in asof["subscriptions"]
        if not s["readable"] or not s["last_read_at"] or {s["id"], s["name"]} & failed
    ]
    text += f"\nNot read in that pull: {_clean(', '.join(late))}." if late else ""
    # A subscription ID is not a secret: naming it here lets the agent pass a change tool the GUID without a cloud CLI.
    guids = [f"{s['name']} ({s['native_id']})" for s in store.subscriptions()]
    return text + (f"\nSubscriptions: {_clean('; '.join(guids))}." if guids else "")


def _link(link: dict[str, Any]) -> dict[str, Any]:
    row = {"kind": link["kind"], "name": link["name"], "type": link["label"], "state": link["state"]}
    if link["props"]:
        row["props"] = link["props"]
    if not link["id"]:
        row["not_in_inventory"] = link["to_native_id"]
    return {k: v for k, v in row.items() if v not in ("", None)}


def _compact(r: dict[str, Any]) -> dict[str, Any]:
    row = {
        "name": r["name"], "label": r["label"], "category": r["category"], "subscription": r["subscription"]["name"],
        "resource_group": r["resource_group"]["name"], "region": r["region"], "state": r["state"],
        "state_detail": r["state_detail"], "sku": (r["sku"] or {}).get("name", ""), "tags": r["tags"],
        "parent": r["parent"]["name"] if r["parent"] else None,
    }
    return {**row, "removed_at": r["removed_at"]} if r["removed_at"] else row


def _attached(subnet: dict[str, Any], routes: dict[str, Any]) -> dict[str, Any]:
    """What a subnet is attached to, with the route table's routes spelled out and `null` for what it has none of."""
    out: dict[str, Any] = {}
    for kind in ("route_table", "nsg", "nat_gateway"):
        link = next((x for x in subnet["links_out"] if x["kind"] == kind), None)
        out[kind] = {"name": link["name"]} if link else None
        if link and kind == "route_table" and link["id"]:
            if link["id"] not in routes:
                routes[link["id"]] = ((store.get(link["id"]) or {}).get("props") or {}).get("routes", [])
            out[kind]["routes"] = routes[link["id"]]
    return out


def _one(found: dict[str, Any]) -> dict[str, Any]:
    routes: dict[str, Any] = {}
    parts = {
        "children": [{**{k: c[k] for k in ("name", "state", "props")}, **_attached(c, routes)} if c["type"] == _SUBNET
                     else {k: c[k] for k in ("name", "label", "state")} for c in found["children"]],
        "links_out": [_link(x) for x in found["links_out"]],
        "links_in": [_link(x) for x in found["links_in"]],
    }
    budget, body = LINK_CAP, {}
    for key, items in parts.items():
        body[key] = items[:budget]
        budget -= len(body[key])
        if len(items) > len(body[key]):
            body[f"{key}_not_shown"] = len(items) - len(body[key])
    return {
        **{k: found[k] for k in ("name", "label", "type", "category", "region", "state", "state_detail", "sku", "tags",
                                 "props", "first_seen_at", "last_seen_at", "changed_at", "removed_at")},
        "subscription": found["subscription"]["name"], "resource_group": found["resource_group"]["name"],
        "parent": found["parent"]["name"] if found["parent"] else None,
        **(_attached(found, routes) if found["type"] == _SUBNET else {}), **body, "exposure": exposure.of(found),
        "changes": [{k: c[k] for k in ("at", "change", "fields")} for c in found["changes"]],
        "azure_says": _azure_says(found["native_id"]),
    }


def _azure_says(native_id: str) -> dict[str, Any]:
    """What Azure itself reported about one resource at the last pull: Advisor, policy, health and its 14-day change
    records. Microsoft's words and estimates, never ours; a saving is a string to quote, not a number to add up."""
    found = signals.of(native_id)
    if not found["read_at"]:
        return {"read": "Azure's own findings have not been read yet; they come with the next pull."}
    out: dict[str, Any] = {"read_at": found["read_at"]}
    for kind, name in (("advisor", "advisor"), ("policy", "policy_non_compliant"), ("health", "health"),
                       ("change", "azure_change_records")):
        out[name] = found[kind][:SIGNAL_CAP]
        if len(found[kind]) > SIGNAL_CAP:
            out[f"{name}_not_shown"] = len(found[kind]) - SIGNAL_CAP
    return out


def _findings(show: str, filters: dict[str, Any], asof: dict[str, Any]) -> str:
    """`show`: the stored findings of one kind across the estate, highest impact first for Advisor."""
    if not signals.read_at(show):
        return "Azure's own findings (Advisor, policy, health, change records) have not been read yet; they come with the next pull."
    rows, total = signals.search(show, {k: filters.get(k) for k in ("subscription", "resource_group", "q")}, LIST_CAP)
    for r in rows:
        r.pop("target", None)
        r["type"] = categories.label_of(r["type"]) if r["type"] else ""
    body: dict[str, Any] = {"total": total, "findings": rows,
                            "note": f"What Azure reported at the pull of {asof['last_pull']['started_at']}: Microsoft's own wording"
                                    f" and estimates (a saving is Microsoft's estimate at retail rates, over its own look-back window)."}
    if show == "health":
        body["note"] += " Only what is not Available is listed."
    if total > LIST_CAP:
        body["note"] += f" Showing the first {LIST_CAP} of {total}; narrow the query."
    return json.dumps(_clean(body), indent=1)


def _waste(filters: dict[str, Any], asof: dict[str, Any]) -> dict[str, Any]:
    """The findings for what the filters select, with the list prices the price list can give and their total, which
    the tool adds up so the model never has to."""
    keep = {k: v for k, v in filters.items() if k != "attention"}
    out = waste.priced(store.waste(keep))
    found = out["findings"]
    cols = ("rule", "title", "name", "label", "subscription", "resource_group", "region", "evidence", "usd_month")
    body = {"total": len(found), "findings": [{k: f[k] for k in cols if k in f} for f in found[:LIST_CAP]],
            "total_usd_month": out["total_usd_month"], "priced": out["priced"], "unpriced": out["unpriced"],
            "note": "A pointer to look, not a verdict: each finding is the rule that fired on the inventory of "
                    f"{asof['last_pull']['started_at']}. usd_month is a list price from Microsoft's public price list"
                    f"{' fetched ' + out['priced_at'] if out['priced_at'] else ''} (730 hours a month, before data "
                    "transfer, usage-based charges and any discount); total_usd_month adds only the findings that have one."}
    if len(found) > LIST_CAP:
        body["note"] += f" Showing the first {LIST_CAP} of {len(found)}; narrow the query."
    return body


def _drifted(filters: dict[str, Any], asof: dict[str, Any]) -> dict[str, Any]:
    """The resources the product made that changed in Azure outside it, with what changed (dated lines, newest last)."""
    found = store.drifted({k: v for k, v in filters.items() if k != "attention"})
    cols = ("name", "label", "subscription", "resource_group", "native_id")
    body = {"total": len(found), "resources": [{**{k: f[k] for k in cols}, "last_applied_by_change": f["change_id"], "changes": f["lines"][-6:]}
                                              for f in found[:LIST_CAP]],
            "note": f"From the pulls up to {asof['last_pull']['started_at']}: a change line on a resource the product made that no apply of its own explains, "
                    "until an engineer accepts it (azure_change drift_accept). Nothing is put back; undoing it is a suggestion for the engineer."}
    if len(found) > LIST_CAP:
        body["note"] += f" Showing the first {LIST_CAP} of {len(found)}; narrow the query."
    return body


def answer(a: dict[str, str]) -> str:
    """The tool's whole answer for cleaned string arguments; a sentence when an argument is refused."""
    for key, allowed in (("category", tuple(categories.CATEGORIES)), ("state", STATES), ("without", store.WITHOUT),
                         ("count_by", store.BY), ("attention", store.ATTENTION)):
        if a.get(key) and a[key].lower() not in allowed and not (key == "without" and a[key].lower().startswith("tag:")):
            return f"{key} must be one of {', '.join(allowed)}."
    type_ = a.get("type", "").lower()
    if type_ and "/" not in type_ and type_ not in categories.TYPE_ALIASES:
        return f"type must be an Azure type such as microsoft.web/sites, or one of {', '.join(categories.TYPE_ALIASES)}."
    if a.get("show") and a["show"].lower() not in signals.SHOWN:
        return f"show must be one of {', '.join(signals.SHOWN)}."
    asof = store.as_of()
    if not asof["last_pull"]:
        return "No Azure inventory has been pulled yet."
    head = _header(asof)
    if a.get("resource"):
        try:
            found = store.get(a["resource"])
        except store.Ambiguous as e:
            return head + "\n\n" + _clean(f"Several resources are named {a['resource']}; ask again with one of these as resource:\n"
                                          + json.dumps(e.candidates, indent=1))
        if not found:
            return head + "\n\n" + _clean(f"No resource matches {a['resource']}.")
        return head + "\n\n" + json.dumps(_clean(_one(found)), indent=1)
    filters = {("q" if k == "query" else k): a[k] for k in _PASSED if a.get(k)}
    filters["type"] = categories.TYPE_ALIASES.get(type_, type_)
    for key in ("category", "state", "without", "attention"):
        if key in filters:
            filters[key] = filters[key].lower()
    if a.get("show"):
        return head + "\n\n" + _findings(a["show"].lower(), filters, asof)
    try:
        if a.get("count_by"):
            groups, total = store.counts(filters, a["count_by"].lower())
            body = {"total": total, "groups": [
                {"key": g["key"], "count": g["count"], **({"label": categories.label_of(g["key"])} if a["count_by"].lower() == "type" else {})}
                for g in groups[:GROUP_CAP]]}
            if len(groups) > GROUP_CAP:
                body["note"] = f"Showing the largest {GROUP_CAP} of {len(groups)} groups; narrow the query."
        elif filters.get("attention") == "waste":
            body = _waste(filters, asof)
        elif filters.get("attention") == "drifted":
            body = _drifted(filters, asof)
        else:
            found, total = store.search(filters, LIST_CAP)
            body = {"total": total, "resources": [_compact(r) for r in found]}
            if total > LIST_CAP:
                body["note"] = f"Showing the first {LIST_CAP} of {total}; narrow the query."
    except store.BadFilter as e:
        return str(e)
    return head + "\n\n" + json.dumps(_clean(body), indent=1)


class AzureInventory(Tool):
    async def execute(self, **kwargs) -> Response:
        a = {k: str(v if v is not None else "").strip() for k, v in (self.args or {}).items()}
        return Response(message=answer(a), break_loop=False)
