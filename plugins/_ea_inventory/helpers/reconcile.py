"""What a SolarWinds run would change, decided without touching anything (ADR 0042). Pure.

`reconcile(observed, devices, observations)` matches each observed node to at most
one device, then lists the fields SolarWinds is allowed to change (`precedence.plan`; a human's
differing value becomes a conflict, ADR 0043). It never plans a removal: a device that stopped appearing is only reported as stale.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from plugins._ea_inventory.helpers import precedence

SOURCE = "solarwinds"
#: What SolarWinds may set. Everything else on a device (name, role, site, status,
#: tags, ha_group, asset_tag, custom) is a person's to decide.
OWNED = ("vendor", "model", "software_version", "mgmt_ip", "platform")
_MATCH_CONFIDENCE = {"serial": 1.0, "source_id": 0.95, "mgmt_ip": 0.8, None: 0.9}


def _diff(
    node: dict[str, Any], device: dict[str, Any], min_confidence: float, pins_kept: tuple = (set(), set()),
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """(changes, conflicts) by the shared precedence rules (ADR 0043): a human's differing value is a conflict."""
    wanted = {f: node.get(f) for f in OWNED}
    if node.get("confidence", 0) < min_confidence:
        wanted["platform"] = None  # a weak guess is not worth a request
    wanted["serial"] = node.get("serial")
    result = precedence.plan(device, wanted, SOURCE, *pins_kept)
    return result.propose, result.conflicts


def _review(node: dict[str, Any], match: dict[str, Any], reason: str, count: int = 1) -> dict[str, Any]:
    """`match` is the device (or, for a repeat inside one run, the earlier node) it may duplicate."""
    more = f" ({count} devices match; the first is named)" if count > 1 else ""
    return {
        "node_id": node["node_id"], "observed_name": node["name"], "device_id": match.get("id"),
        "device_name": match["name"], "reason": reason + more,
    }


def reconcile(
    observed: list[dict[str, Any]], devices: list[dict[str, Any]], observations: list[dict[str, Any]],
    *, stale_days: int = 7, min_platform_confidence: float = 0.6, now: _dt.datetime | None = None,
    complete: bool = True, pins_kept: dict[str, tuple] | None = None,
) -> dict[str, Any]:
    """`{create, update, unchanged_seen, stale, skipped, needs_review, conflicts}`.

    `pins_kept` is `conflicts.load_all()`: per device id, the pins and kept observations.

    `observations` are what sources said before this run; `complete=False` (the fetch
    stopped at its cap) means an absent device proves nothing, so none is reported stale.
    """
    now = now or _dt.datetime.now(_dt.timezone.utc)
    plan: dict[str, Any] = {k: [] for k in ("create", "update", "unchanged_seen", "stale", "skipped", "needs_review", "conflicts")}
    plan["matched"] = {}  # node_id -> device_id, for the observation each node leaves
    by_id = {d["id"]: d for d in devices}
    by_serial = {d["serial"]: d for d in devices if d.get("serial")}
    by_ip = {d["mgmt_ip"]: d for d in devices if d.get("mgmt_ip") and d["status"] != "decommissioned"}
    by_name = {d["name"].lower(): d for d in devices}
    known = {
        o["source_id"]: o["device_id"] for o in observations
        if o.get("source") == SOURCE and o.get("device_id") and str(o["device_id"]) in by_id
    }
    seen_keys: dict[tuple[str, str], dict[str, Any]] = {}
    seen_ids: set[str] = set()

    for node in observed:
        clash = next(
            (seen_keys[k] for k in (("serial", node.get("serial")), ("mgmt_ip", node.get("mgmt_ip")))
             if k[1] and k in seen_keys), None,
        )
        if clash:
            plan["needs_review"].append(_review(
                node, clash, f"Same serial or address as node {clash['node_id']} in this run."))
            continue
        for k in (("serial", node.get("serial")), ("mgmt_ip", node.get("mgmt_ip"))):
            if k[1]:
                seen_keys[k] = node

        hits: dict[str, str] = {}  # device id -> how it matched
        for how, device in (
            ("serial", by_serial.get(node.get("serial"))),
            ("source_id", by_id.get(str(known.get(node["node_id"])))),
            ("mgmt_ip", by_ip.get(node.get("mgmt_ip"))),
        ):
            if device:
                hits.setdefault(device["id"], how)
        if len(hits) > 1:
            first, *rest = sorted((by_id[i] for i in hits), key=lambda d: d["name"])
            plan["needs_review"].append(_review(
                node, first, "Serial, SolarWinds id and address point at different devices.", len(rest) + 1))
            continue
        if not hits:
            same = next((by_name[n] for n in node.get("aliases", [node["name"]]) if n in by_name), None)
            if same:
                plan["needs_review"].append(_review(
                    node, same, "Same name as an existing device but a different serial and address: a possible duplicate."))
            else:
                plan["create"].append({
                    "node_id": node["node_id"], "name": node["name"], "confidence": _MATCH_CONFIDENCE[None],
                    "fields": {k: v for k, v in {**{f: node.get(f) for f in OWNED}, "name": node["name"],
                               "serial": node.get("serial")}.items() if v and not (
                        k == "platform" and node.get("confidence", 0) < min_platform_confidence)},
                })
            continue
        [(device_id, how)] = hits.items()
        device = by_id[device_id]
        seen_ids.add(device_id)
        plan["matched"][node["node_id"]] = device_id
        fields, conflicts = _diff(node, device, min_platform_confidence, (pins_kept or {}).get(device_id, (set(), set())))
        if conflicts:
            plan["conflicts"].append({
                "node_id": node["node_id"], "device_id": device_id, "device_name": device["name"], "conflicts": conflicts})
        if not fields:
            if not conflicts:
                plan["unchanged_seen"].append(device_id)
            continue
        confidence = _MATCH_CONFIDENCE[how]
        if "platform" in fields:
            confidence = min(confidence, node["confidence"])
        plan["update"].append({
            "node_id": node["node_id"], "name": node["name"], "device_id": device_id,
            "device_name": device["name"], "fields": fields, "confidence": confidence,
        })

    if complete:
        newest: dict[str, _dt.datetime] = {}
        for o in observations:
            did = str(o.get("device_id") or "")
            if o.get("source") == SOURCE and did and o["observed_at"] > newest.get(did, _dt.datetime.min.replace(tzinfo=_dt.timezone.utc)):
                newest[did] = o["observed_at"]
        limit = now - _dt.timedelta(days=stale_days)
        plan["stale"] = sorted(i for i, at in newest.items() if at < limit and i not in seen_ids and i in by_id)
    return plan
