"""SolarWinds nodes into inventory proposals (ADR 0042). Read-only, all-or-nothing.

`normalise_node` turns one fetched node into the fields inventory understands;
`run_solarwinds_sync` fetches every node, reconciles, stores what was seen and (unless
a dry run) stages one batch of proposals. It never writes a device and never proposes
removal: a person approves every change.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from typing import Any

from plugins._ea_inventory.helpers import conflicts, proposals, reconcile, store
from plugins._ea_inventory.helpers.normalise import (
    InventoryError, normalise_ip, normalise_name, normalise_serial,
)

SOURCE = "solarwinds"
PAGE_SIZE = 200
# (platform, words in vendor/model/version text, SNMP enterprise number)
_VENDORS = (
    ("fortios", ("fortinet", "fortigate", "fortios"), "12356"),
    ("panos", ("palo alto", "panos", "pan-os"), "25461"),
    ("junos", ("juniper", "junos"), "2636"),
    ("eos", ("arista",), "30065"),
    ("sonic", ("sonic",), ""),
)


def _guess_platform(node: dict[str, Any]) -> tuple[str, float]:
    text = " ".join(str(node.get(k) or "") for k in ("vendor", "machine_type", "software_version")).lower()
    enterprise = str(node.get("sys_object_id") or "").removeprefix(".").removeprefix("1.3.6.1.4.1.").split(".")[0]
    for platform, words, number in _VENDORS:
        if any(w in text for w in words):
            return platform, 0.9
        if number and enterprise == number:
            return platform, 0.7
    if "cisco" in text or enterprise == "9":
        if "nx-os" in text or "nxos" in text or "nexus" in text:
            return "nxos", 0.9
        if "ios xe" in text or "ios-xe" in text or "iosxe" in text:
            return "iosxe", 0.9
        if "ios" in text:
            return "ios", 0.8
        return "ios", 0.4  # a Cisco box with no version text: could be any of three
    return "other", 0.2


def normalise_node(node: dict[str, Any]) -> dict[str, Any]:
    """One fetched node as `{node_id, name, aliases, vendor, model, software_version, mgmt_ip,
    serial, platform, confidence, last_poll}`; raises InventoryError when it cannot be named or addressed."""
    names: list[str] = []
    for key in ("caption", "sys_name", "dns"):
        try:
            name = normalise_name(node.get(key))
        except InventoryError:
            continue
        if name not in names:
            names.append(name)
    if not names:
        raise InventoryError("no usable name (caption, sys name and DNS all empty or invalid).")
    ip = normalise_ip(node.get("ip_address"))  # InventoryError if it is not an address
    if not ip:
        raise InventoryError("no management address.")
    platform, confidence = _guess_platform(node)
    text = lambda key: str(node.get(key) or "").strip()  # noqa: E731
    return {
        "node_id": str(node.get("node_id") or ""), "name": names[0], "aliases": names,
        "vendor": text("vendor"), "model": text("machine_type"), "software_version": text("software_version"),
        "mgmt_ip": ip, "serial": normalise_serial(node.get("serial")),
        "platform": platform, "confidence": confidence, "last_poll": node.get("last_poll"),
    }


def _refuse() -> dict[str, Any]:
    from plugins._ea_solarwinds.helpers import config as sw_config

    settings = store.solarwinds_settings()
    if not settings["enabled"]:
        raise InventoryError("The SolarWinds feed is disabled. An administrator enables it in the Device Inventory plugin config.")
    try:
        configured = sw_config.is_configured()
    except Exception:  # noqa: BLE001
        configured = False
    if not configured:
        raise InventoryError("SolarWinds is not configured. An administrator sets it up in the SolarWinds plugin first.")
    if store.running_since(SOURCE):
        raise InventoryError("A SolarWinds run is already in progress.")
    return settings


def _fetch(max_nodes: int):
    from plugins._ea_solarwinds.helpers import bulk  # lazy: the feed is optional

    return bulk.fetch_nodes(max_nodes=max_nodes, page_size=PAGE_SIZE)


def run_solarwinds_sync(dry_run: bool = False, actor: str = "") -> dict[str, Any]:
    """Fetch, reconcile, store and stage. Refuses (InventoryError) unless enabled and configured;
    a failed fetch is recorded on the run and returns `status: "failed"` with nothing created."""
    settings = _refuse()
    run_id = store.start_run(SOURCE, actor)
    out: dict[str, Any] = {"run_id": run_id, "status": "failed", "counts": {}, "batch_id": None, "needs_review": [], "missing_columns": []}
    try:
        result = _fetch(settings["max_nodes"])
        if not result.complete and not result.truncated:
            raise InventoryError("SolarWinds returned an incomplete node list.")
        out["missing_columns"] = list(result.missing_columns)
        out["truncated"] = bool(result.truncated)
        at = _dt.datetime.now(_dt.timezone.utc)
        observed, skipped, raw = [], [], {}
        for node in result.nodes:
            try:
                n = normalise_node(node)
            except InventoryError as error:
                skipped.append({"node_id": str(node.get("node_id") or ""), "reason": str(error)})
                continue
            observed.append(n)
            raw[n["node_id"]] = node
        plan = reconcile.reconcile(
            observed, store.list_devices(), store.latest_observations(SOURCE), stale_days=settings["stale_days"],
            min_platform_confidence=settings["min_platform_confidence"], now=at, complete=not result.truncated,
            pins_kept=conflicts.load_all(),
        )
        counts = _stage(plan, raw, skipped, actor, at, dry_run, len(result.nodes))
        out.update(counts)
        out["status"] = "dry_run" if dry_run else "succeeded"
        out["needs_review"] = plan["needs_review"]
        out["skipped"] = skipped[:50]
    except Exception as error:  # noqa: BLE001 - any failure: record it, create nothing further
        out["status"], out["error"] = "failed", str(error) or type(error).__name__
    store.finish_run(run_id, out["status"], out["counts"], out.get("error", ""), out["batch_id"])
    proposals._emit("inventory.synced", source=SOURCE, run_id=run_id, status=out["status"], dry_run=dry_run, requested_by=actor, **out["counts"])
    return out


def _stage(plan, raw, skipped, actor, at, dry_run, fetched) -> dict[str, Any]:
    pending = store.pending_node_ids(SOURCE)
    batch_id = None if dry_run else str(uuid.uuid4())
    made = {"create": 0, "update": 0}
    unchanged = len(plan["unchanged_seen"])
    for kind in ("create", "update"):
        for item in plan[kind]:
            if item["node_id"] in pending:
                skipped.append({"node_id": item["node_id"], "reason": "a request for this node is already pending."})
            elif dry_run:
                made[kind] += 1
            else:
                evidence = {
                    "source": SOURCE, "node_id": item["node_id"], "observed_at": at.isoformat(),
                    "batch_id": batch_id, "confidence": item["confidence"],
                }
                try:
                    proposals.propose(kind, item.get("device_id"), item["fields"], SOURCE, evidence, actor)
                    made[kind] += 1
                except InventoryError as error:
                    if str(error).startswith("Nothing would change"):
                        unchanged += 1
                    else:
                        skipped.append({"node_id": item["node_id"], "reason": str(error)})
    if not dry_run:
        for item in plan["conflicts"]:
            conflicts.upsert_conflicts(
                item["device_id"], SOURCE, item["conflicts"],
                {"source": SOURCE, "node_id": item["node_id"], "observed_at": at.isoformat()}, at)
        matched = plan["matched"]
        store.record_sync(SOURCE, [(matched.get(nid), nid, node) for nid, node in raw.items()], sorted(set(matched.values())), at)
    return {
        "counts": {
            "fetched": fetched, "create": made["create"], "update": made["update"], "unchanged": unchanged,
            "skipped": len(skipped), "needs_review": len(plan["needs_review"]), "stale": len(plan["stale"]),
            "conflicts": sum(len(i["conflicts"]) for i in plan["conflicts"]),
        },
        "batch_id": batch_id if sum(made.values()) else None,
    }
