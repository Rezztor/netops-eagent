"""Config backups as Inventory sees them: dates, sizes, hashes and a redacted view.

The text is encrypted on the executor and never leaves it in the clear (ADR 0047): this
module can ask for a redacted view or diff and nothing else, and there is no agent tool.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any

from plugins._ea_inventory.helpers.executor_sync import NAME
from plugins._ea_inventory.helpers.normalise import InventoryError

CAP = 10
_ID = re.compile(r"^[0-9a-f]{32}$")


def _client() -> Any:
    from plugins._ea_ansible.helpers import executor_client

    return executor_client


def _ask(call: str, *args: Any) -> Any:
    client = _client()
    try:
        return getattr(client, call)(None, *args)
    except client.ExecutorError as error:
        raise InventoryError(str(error)) from error


def _host(device: dict[str, Any]) -> str:
    if not NAME.match(str(device["name"])):
        raise InventoryError(f"'{device['name']}' is not a usable executor host name.")
    return str(device["name"])


def _id(value: Any) -> str:
    if not _ID.match(str(value)):
        raise InventoryError("There is no such backup.")
    return str(value)


def listing(device: dict[str, Any]) -> list[dict[str, Any]]:
    host = _host(device)
    return _ask("backup_list", host).get(host, [])


def list_all() -> dict[str, list[dict[str, Any]]] | None:
    """Every host's backups in one call, or None when the executor cannot be reached."""
    try:
        return _ask("backup_list")
    except InventoryError:
        return None


def unknown_reason() -> str:
    """Why `list_all` gave nothing: the executor is not set up (its own words, so the page can say so), or silent."""
    return _client().setup_problem() or "The executor could not be reached, so backups are unknown."


def run(devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Back up up to ten devices; one result each, in the order given."""
    if not devices or len(devices) > CAP:
        raise InventoryError(f"Back up one to {CAP} devices at a time.")
    hosts = {d["id"]: d["name"] for d in devices if NAME.match(str(d["name"]))}
    got = _ask("backup_run", sorted(set(hosts.values()))) if hosts else {}
    out = []
    for d in devices:
        r = got.get(d["name"]) if d["id"] in hosts else {"error": "not a usable executor host name."}
        r = r or {"error": "the executor returned nothing for it."}
        out.append({
            "device_id": d["id"], "name": d["name"], "ok": bool(r.get("ok")), "id": r.get("id"),
            "changed": r.get("changed"), "error": r.get("error"),
        })
    return out


def view(device: dict[str, Any], backup_id: str) -> str:
    return _ask("backup_text", _host(device), _id(backup_id))


def diff(device: dict[str, Any], old: str, new: str) -> dict[str, Any]:
    return _ask("backup_diff", _host(device), _id(old), _id(new))


def state(rows: list[dict[str, Any]] | None, now: _dt.datetime, stale_days: int) -> dict[str, Any]:
    """`none` with no backup, `stale` when the newest was last checked more than `stale_days` ago.

    Age counts from `checked_at`: a config checked yesterday and found unchanged is not stale.
    """
    if not rows:
        return {"taken_at": None, "checked_at": None, "age_days": None, "state": "none"}
    new = rows[0]
    checked = _dt.datetime.fromisoformat(new["checked_at"].replace("Z", "+00:00"))
    age = max(0, (now - checked).days)
    return {
        "taken_at": new["taken_at"], "checked_at": new["checked_at"], "age_days": age,
        "state": "stale" if age > stale_days else "ok",
    }


def report(devices: list[dict[str, Any]], backups: dict[str, list[dict[str, Any]]], now: _dt.datetime, stale_days: int) -> list[dict[str, Any]]:
    """Active devices with no backup or a stale one, never-backed-up first."""
    items = []
    for d in devices:
        if d["status"] == "active":
            s = state(backups.get(d["name"]), now, stale_days)
            if s["state"] != "ok":
                items.append({"device_id": d["id"], "name": d["name"], "state": s["state"], "taken_at": s["taken_at"], "age_days": s["age_days"]})
    items.sort(key=lambda i: (i["state"] != "none", -(i["age_days"] or 0), i["name"]))
    return items


#: The executor stamps backups and the application stamps applies, on two clocks; a change this close to the window's
#: edge still counts as inside it.
SLACK = _dt.timedelta(minutes=5)


def _at(value: str) -> _dt.datetime:
    return _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def drift(devices: list[dict[str, Any]], backups: dict[str, list[dict[str, Any]]], applied: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Devices whose newest backup differs from the one before, each marked explained or not by an applied change.

    The executor writes a new backup only when the configuration changed (`executor/backups.py`), so a device with two
    or more changed after the older one was last checked and before the newer one was taken. An applied change
    (`{"id", "devices": set of names, "applied_at": aware datetime, ...}`) explains it when it names the device and was
    applied in that window, `SLACK` either side. Explained means an approved change ran there then, not that it is
    the whole difference: the diff says that. Unexplained first, then the most recent change first.
    """
    items = []
    for d in devices:
        rows = backups.get(d["name"]) or []
        if len(rows) < 2:
            continue
        new, old = rows[0], rows[1]
        start, end = _at(old.get("checked_at") or old["taken_at"]) - SLACK, _at(new["taken_at"]) + SLACK
        found = [c for c in applied if d["name"] in c["devices"] and start <= c["applied_at"] <= end]
        items.append({
            "device_id": d["id"], "name": d["name"], "from": old["id"], "to": new["id"],
            "since": old.get("checked_at") or old["taken_at"], "changed_at": new["taken_at"],
            "explained": bool(found),
            "changes": [{k: v for k, v in c.items() if k != "devices"} | {"applied_at": c["applied_at"].isoformat()} for c in found],
        })
    items.sort(key=lambda i: i["changed_at"], reverse=True)
    items.sort(key=lambda i: i["explained"])
    return items
