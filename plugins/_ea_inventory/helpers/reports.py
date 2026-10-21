"""Values computed from a device and the three reports over many devices.

Nothing here is stored (ADR 0045): `computed` is derived each time from what the record
already holds. The functions take plain dicts and a `today`, so they test without a
database; only `settings` and `attach` read configuration and the clock.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from plugins._ea_inventory.helpers.executor_sync import API_PLATFORMS, PLATFORM_VARS
from plugins._ea_inventory.helpers.places import KINDS as LEVELS, ancestors, depth

KINDS = {"warranty": "warranty_expiry", "support": "support_expiry", "eol": "eol_date"}
_WORST = ("none", "ok", "soon", "expired")
_DEFAULT_REQUIRED = ("name", "device_type", "role", "vendor", "model", "serial", "mgmt_ip", "place_id", "status")
_LIMIT = 500


def settings() -> dict[str, Any]:
    """`required_fields`, `required_place_level`, `lifecycle_soon_days`, `backup_stale_days` and the stale window, read like `store.solarwinds_settings`."""
    from helpers import plugins
    from plugins._ea_inventory.helpers import store

    merged = {
        **(plugins.get_default_plugin_config("_ea_inventory") or {}),
        **(plugins.get_plugin_config("_ea_inventory") or {}),
    }
    level = str(merged.get("required_place_level") or "room")
    return {
        "required": list(merged.get("required_fields") or _DEFAULT_REQUIRED),
        "place_level": level if level in LEVELS else "room",
        "soon_days": max(1, int(merged.get("lifecycle_soon_days") or 90)),
        "stale_days": store.solarwinds_settings()["stale_days"],
        "backup_stale_days": max(1, int(merged.get("backup_stale_days") or 7)),
    }


def now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def today() -> _dt.date:
    return now().date()


def _lifecycle(value: Any, today: _dt.date, soon_days: int) -> dict[str, Any]:
    try:
        date = _dt.date.fromisoformat(str(value))
    except ValueError:
        return {"date": None, "days": None, "state": "none"}
    days = (date - today).days
    return {"date": date.isoformat(), "days": days, "state": "expired" if days < 0 else "soon" if days <= soon_days else "ok"}


def completeness(device: dict[str, Any], required: list[str], place_level: str = "room") -> dict[str, Any]:
    """A `place_id` above `place_level` (a building where rooms are required) counts as empty."""
    empty = lambda f: device.get(f) in (None, "") or (f == "device_type" and device[f] == "other") or (  # noqa: E731
        f == "place_id" and depth(device[f]) <= LEVELS.index(place_level))
    missing = [f for f in required if empty(f)]
    return {"percent": round(100 * (len(required) - len(missing)) / len(required)) if required else 100, "missing": missing}


def computed(
    device: dict[str, Any], *, today: _dt.date, required: list[str], stale_days: int, soon_days: int = 90,
    backup: dict[str, Any] | None = None, place_level: str = "room",
) -> dict[str, Any]:
    """`access_method`, `monitored`, `completeness`, `lifecycle`, `lifecycle_state` and `backup`
    (stale_days is the sources' own flag; `backup` is `backups.state`, None when the executor was not asked or silent)."""
    connection = PLATFORM_VARS.get(device.get("platform"), {}).get("ansible_connection", "")
    life = {kind: _lifecycle(device.get(field), today, soon_days) for kind, field in KINDS.items()}
    return {
        "access_method": "" if not connection else "https_api" if device.get("platform") in API_PLATFORMS else "ssh",
        "monitored": any(s["source"] == "solarwinds" and not s["stale"] for s in device.get("sources") or []),
        "completeness": completeness(device, required, place_level),
        "lifecycle": life,
        "lifecycle_state": max((v["state"] for v in life.values()), key=_WORST.index),
        "backup": backup,
    }


def attach(devices: list[dict[str, Any]], found: dict[str, list[dict[str, Any]]] | None = None) -> None:
    """Put `computed` on each device (as `inventory_list` and `inventory_device` return them).

    `found` is `backups.list_all()`, which the async handlers fetch off the event loop; None
    (the executor was silent) gives `backup: None`."""
    from plugins._ea_inventory.helpers import backups

    cfg = settings()
    when = now()
    for d in devices:
        d["computed"] = computed(
            d, today=when.date(), required=cfg["required"], stale_days=cfg["stale_days"], soon_days=cfg["soon_days"],
            place_level=cfg["place_level"],
            backup=None if found is None else backups.state(found.get(d["name"]), when, cfg["backup_stale_days"]),
        )


def _live(devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [d for d in devices if d["status"] != "decommissioned"]


def _site(d: dict[str, Any]) -> str:
    return (d.get("site") or {}).get("name") or ""


def lifecycle(
    devices: list[dict[str, Any]], *, today: _dt.date, within_days: int, kind: str | None = None
) -> dict[str, Any]:
    counts = {k: {"expired": 0, "soon": 0, "ok": 0, "missing": 0} for k in KINDS}
    items = []
    for d in _live(devices):
        for k, field in KINDS.items():
            life = _lifecycle(d.get(field), today, within_days)
            counts[k]["missing" if life["state"] == "none" else life["state"]] += 1
            if life["state"] in ("expired", "soon") and kind in (None, k):
                items.append({
                    "device_id": d["id"], "name": d["name"], "site": _site(d), "vendor": d["vendor"],
                    "model": d["model"], "kind": k, "date": life["date"], "days": life["days"], "state": life["state"],
                })
    items.sort(key=lambda i: (i["days"], i["name"], i["kind"]))
    no_dates = sum(1 for d in _live(devices) if not any(d.get(field) for field in KINDS.values()))
    return {"today": today.isoformat(), "within_days": within_days, "counts": counts, "items": items, "no_dates": no_dates}


def quality(devices: list[dict[str, Any]], required: list[str], place_level: str = "room") -> dict[str, Any]:
    live = _live(devices)
    scored = [(d, completeness(d, required, place_level)) for d in live]
    incomplete = sorted(
        ((d, c) for d, c in scored if c["missing"]), key=lambda x: (x[1]["percent"], x[0]["name"]))
    return {
        "required": required, "total": len(live), "complete": len(live) - len(incomplete),
        "average": round(sum(c["percent"] for _, c in scored) / len(scored)) if scored else 0,
        "by_field": [{"field": f, "missing": sum(f in c["missing"] for _, c in scored)} for f in required],
        "devices": [
            {"device_id": d["id"], "name": d["name"], "site": _site(d), **c} for d, c in incomplete[:_LIMIT]
        ],
    }


def racks(devices: list[dict[str, Any]], places: list[dict[str, Any]], under: str = "") -> dict[str, Any]:
    """One entry per rack in `places` (the active places, `places.list_places(False)`), empty ones included,
    limited to `under` and what is below it when given. `unplaced` counts live devices that are in no rack."""
    names = {p["id"]: p["name"] for p in places}
    live = _live(devices)
    in_rack: dict[str, list[dict[str, Any]]] = {}
    for d in live:
        if d.get("place_id") and depth(d["place_id"]) == len(LEVELS):
            in_rack.setdefault(d["place_id"], []).append(d)
    out = []
    for p in sorted(places, key=lambda p: p["id"]):
        if p["kind"] != "rack" or (under and p["id"] != under and not p["id"].startswith(under + "-")):
            continue
        members = sorted(in_rack.get(p["id"], []), key=lambda d: (d["rack_unit"] is None, -(d["rack_unit"] or 0), d["name"]))
        used = {u for d in members if d["rack_unit"] for u in range(d["rack_unit"], d["rack_unit"] + (d["u_height"] or 1))}
        out.append({
            "place_id": p["id"], **dict(zip(LEVELS, (names.get(a, "") for a in ancestors(p["id"])))), "used_u": len(used),
            "devices": [
                {k: d[k] for k in ("device_type", "model", "status", "rack_unit", "u_height")} | {"device_id": d["id"], "name": d["name"]}
                for d in members
            ],
        })
    return {"racks": out, "unplaced": sum(not (d.get("place_id") and depth(d["place_id"]) == len(LEVELS)) for d in live)}
