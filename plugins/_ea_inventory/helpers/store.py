"""Where inventory rows live, and the reads over them.

Instance-wide data with no row-level security (see `helpers/schema/0009_inventory.sql`),
so a transaction here is opened with `db.NO_SCOPE`: the application role, no user.
Writes to devices happen only through `proposals.decide`; this module has none
for devices, and nothing in it deletes a device.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from helpers import db

from plugins._ea_inventory.helpers import places
from plugins._ea_inventory.helpers.normalise import DEVICE_TYPES, ROLE_SUGGESTIONS, STATUSES, InventoryError

DEVICE_COLUMNS = (
    "id", "name", "role", "vendor", "platform", "model", "serial", "asset_tag", "status",
    "place_id", "mgmt_ip", "software_version", "ha_group", "tags", "custom", "field_sources",
    "last_seen_at", "version", "created_at", "updated_at",
    "device_type", "part_number", "base_mac", "rack_unit", "u_height",
    "department", "custodian", "support_contact", "support_contract",
    "purchase_date", "warranty_expiry", "support_expiry", "eol_date",
)


def transaction():
    return db.transaction(db.NO_SCOPE)


def rows(conn: Any, sql: str, params: Any = ()) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else []


def _plain(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.isoformat()
    return value


def device_json(row: dict[str, Any]) -> dict[str, Any]:
    """The device with its place keys empty; `attach_place` fills them from `place_id`."""
    d = {k: _plain(row[k]) for k in DEVICE_COLUMNS}
    for field in ("serial", "mgmt_ip"):
        d[field] = d[field] or ""
    d["place"], d["site"] = None, None
    d.update({kind: "" for kind in places.KINDS[1:]})
    d["facts_at"], d["ha_members"] = None, []  # attach_sources fills these
    return d


def attach_place(conn: Any, devices: list[dict[str, Any]]) -> None:
    """`place`, `site` and one name per level from the place tree, in one query for the whole page."""
    ids = {a for d in devices if d["place_id"] for a in places.ancestors(d["place_id"])}
    if not ids:
        return
    by_id = {r["id"]: r for r in rows(
        conn, "SELECT id, kind, code, name, active FROM inventory_place WHERE id = ANY(%s)", (list(ids),))}
    for d in devices:
        if not d["place_id"]:
            continue
        path = [{k: by_id[a][k] for k in ("id", "kind", "code", "name")} for a in places.ancestors(d["place_id"])]
        d["place"] = {
            "id": d["place_id"], "kind": path[-1]["kind"], "active": places.effective_active(d["place_id"], by_id),
            "path": path,
        }
        d["site"] = {"id": path[0]["id"], "name": path[0]["name"]}
        named = {p["kind"]: p["name"] for p in path}
        for kind in places.KINDS[1:]:
            d[kind] = named.get(kind, "")


def proposal_json(row: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "id", "kind", "device_id", "device_name", "base_version", "changes", "source",
        "evidence", "status", "requested_by", "requested_at", "decided_by", "decided_at",
        "decision_note",
    )
    return {k: _plain(row[k]) for k in keys}


def _as_devices(conn: Any, found: list[dict[str, Any]]) -> list[dict[str, Any]]:
    devices = [device_json(r) for r in found]
    attach_place(conn, devices)
    return devices


def search_devices(
    q: str = "", vendor: str = "", platform: str = "", status: str = "",
    tag: str = "", name: str = "", site_name: str = "", limit: int = 100, offset: int = 0,
    stale: bool = False, device_type: str = "", role: str = "", department: str = "", place: str = "",
) -> tuple[list[dict[str, Any]], int]:
    where: list[str] = []
    args: list[Any] = []
    if q:
        like = f"%{q.strip()}%"
        cols = ("d.name", "d.role", "d.vendor", "d.model", "d.serial", "d.asset_tag", "d.mgmt_ip", "d.ha_group",
                "d.part_number", "d.base_mac", "d.department", "d.custodian", "d.support_contract", "d.place_id")
        # the name of the place or of any place above it: the ID is the parent's ID plus the code
        where.append(
            "(" + " OR ".join(f"{c} ILIKE %s" for c in cols) + " OR %s = ANY(d.tags) OR EXISTS (SELECT 1 FROM "
            "inventory_place p WHERE (d.place_id = p.id OR d.place_id LIKE p.id || '-%%') AND p.name ILIKE %s))")
        args += [like] * len(cols) + [q.strip().lower(), like]
    for column, value in (("d.platform", platform), ("d.status", status)):
        if value:
            where.append(f"{column} = %s")
            args.append(value)
    if vendor:
        where.append("lower(d.vendor) = %s")
        args.append(vendor.lower())
    for column, value in (
        ("device_type", device_type), ("role", role), ("department", department),
    ):
        if value:
            where.append(f"lower(d.{column}) = %s")
            args.append(value.strip().lower())
    if tag:
        where.append("%s = ANY(d.tags)")
        args.append(tag.lower())
    if name:
        where.append("lower(d.name) = %s")
        args.append(name.strip().lower())
    if place == "none":
        where.append("d.place_id IS NULL")
    elif place:  # at or below this place; a malformed ID simply matches nothing
        where.append("(d.place_id = %s OR d.place_id LIKE %s)")
        args += places.under(place.strip().upper())
    if site_name:  # a site's name or code
        key = site_name.strip().lower()
        where.append(
            "split_part(d.place_id, '-', 1) IN (SELECT id FROM inventory_place "
            "WHERE parent_id IS NULL AND (lower(name) = %s OR lower(id) = %s))")
        args += [key] * 2
    if stale:  # any source's latest observation older than stale_days
        where.append(
            "d.id IN (SELECT device_id FROM (SELECT DISTINCT ON (device_id, source) device_id, observed_at "
            f"FROM {_OBSERVED} o WHERE device_id IS NOT NULL ORDER BY device_id, source, observed_at DESC) l "
            "WHERE observed_at < %s)")
        args.append(_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=solarwinds_settings()["stale_days"]))
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    limit = max(1, min(int(limit or 100), 500))
    with transaction() as conn:
        total = rows(
            conn,
            f"SELECT count(*) AS n FROM inventory_device d{clause}",
            args,
        )[0]["n"]
        found = rows(
            conn,
            f"SELECT d.* FROM inventory_device d{clause} ORDER BY d.name LIMIT %s OFFSET %s",
            [*args, limit, max(0, int(offset or 0))],
        )
        devices = _as_devices(conn, found)
        attach_sources(conn, devices)
    return devices, total


def list_devices(**filters: Any) -> list[dict[str, Any]]:
    """Every matching device, unpaged. For the executor sync; screens use `search_devices`."""
    found: list[dict[str, Any]] = []
    while True:
        page, total = search_devices(limit=500, offset=len(found), **filters)
        found += page
        if not page or len(found) >= total:
            return found


def facets() -> dict[str, Any]:
    with transaction() as conn:
        def distinct(column: str) -> list[str]:
            return [
                r["v"] for r in rows(
                    conn,
                    f"SELECT DISTINCT {column} AS v FROM inventory_device "
                    f"WHERE {column} <> '' ORDER BY 1",
                )
            ]

        tags = rows(conn, "SELECT DISTINCT unnest(tags) AS v FROM inventory_device ORDER BY 1")
        sites = rows(conn, "SELECT id, name FROM inventory_place WHERE parent_id IS NULL AND active ORDER BY lower(name)")
    return {
        "vendor": distinct("vendor"),
        "platform": distinct("platform"),
        "status": distinct("status"),
        **{c: distinct(c) for c in ("device_type", "role", "department")},
        "device_types": list(DEVICE_TYPES), "role_suggestions": list(ROLE_SUGGESTIONS), "statuses": list(STATUSES),
        "site": sites,
        "tag": [t["v"] for t in tags],
    }


def get_device(device_id: str, conn: Any = None) -> dict[str, Any] | None:
    try:
        uuid.UUID(str(device_id))
    except ValueError:
        return None
    if conn is None:
        with transaction() as conn:
            return get_device(device_id, conn)
    found = rows(conn, "SELECT d.* FROM inventory_device d WHERE d.id = %s", (device_id,))
    return _as_devices(conn, found)[0] if found else None


# Observations with the device an approved request made for the same node: a node seen before
# its device existed has no device_id of its own until the next run matches it.
_OBSERVED = (
    "(SELECT o.source, o.source_id, o.observed_at, COALESCE(o.device_id, p.device_id) AS device_id "
    "FROM inventory_observation o LEFT JOIN LATERAL (SELECT device_id FROM inventory_proposal "
    "WHERE o.device_id IS NULL AND source = o.source AND status = 'approved' AND device_id IS NOT NULL "
    "AND evidence->>'node_id' = o.source_id ORDER BY decided_at DESC LIMIT 1) p ON true)"
)


def solarwinds_settings() -> dict[str, Any]:
    """`solarwinds:` from the plugin config over the shipped defaults (a stored config replaces the file)."""
    from helpers import plugins

    merged = {
        **(plugins.get_default_plugin_config("_ea_inventory") or {}).get("solarwinds", {}),
        **((plugins.get_plugin_config("_ea_inventory") or {}).get("solarwinds") or {}),
    }
    return {
        "enabled": merged.get("enabled") is True, "max_nodes": max(1, int(merged.get("max_nodes") or 2000)),
        "stale_days": max(1, int(merged.get("stale_days") or 7)),
        "min_platform_confidence": float(merged.get("min_platform_confidence") or 0.6),
    }


def attach_sources(conn: Any, devices: list[dict[str, Any]]) -> None:
    """`sources: [{source, source_id, last_seen_at, stale}]` from each device's latest observation per source,
    plus `facts_at` (the latest facts observation) and `ha_members` (other devices sharing `ha_group`)."""
    for d in devices:
        d["sources"] = []
    if not devices:
        return
    limit = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=solarwinds_settings()["stale_days"])
    by_id = {d["id"]: d for d in devices}
    for r in rows(
        conn,
        "SELECT DISTINCT ON (device_id, source) device_id, source, source_id, observed_at "
        f"FROM {_OBSERVED} o WHERE device_id = ANY(%s::uuid[]) "
        "ORDER BY device_id, source, observed_at DESC",
        (list(by_id),),
    ):
        by_id[str(r["device_id"])]["sources"].append({
            "source": r["source"], "source_id": r["source_id"],
            "last_seen_at": _plain(r["observed_at"]), "stale": r["observed_at"] < limit,
        })
        if r["source"] == "facts":
            by_id[str(r["device_id"])]["facts_at"] = _plain(r["observed_at"])
    groups = {d["ha_group"] for d in devices if d["ha_group"]}
    if groups:
        members = rows(
            conn, "SELECT id, name, status, ha_group FROM inventory_device WHERE ha_group = ANY(%s) ORDER BY name",
            (list(groups),))
        for d in devices:
            d["ha_members"] = [
                {"id": str(m["id"]), "name": m["name"], "status": m["status"]}
                for m in members if m["ha_group"] == d["ha_group"] and str(m["id"]) != d["id"]
            ]


def stale_devices(days: int | None = None) -> tuple[list[dict[str, Any]], int]:
    """Active devices no source has seen for `days` (default `stale_days`); never-seen ones come first."""
    days = max(1, int(days or solarwinds_settings()["stale_days"]))
    now = _dt.datetime.now(_dt.timezone.utc)
    out = []
    for d in list_devices(status="active"):
        seen = [_dt.datetime.fromisoformat(x) for x in (d["last_seen_at"], *(s["last_seen_at"] for s in d["sources"])) if x]
        last = max(seen) if seen else None
        if last is None or last < now - _dt.timedelta(days=days):
            out.append({
                "id": d["id"], "name": d["name"], "status": d["status"], "last_seen_at": last.isoformat() if last else None,
                "days_unseen": (now - last).days if last else None, "sources": sorted({s["source"] for s in d["sources"]}),
            })
    out.sort(key=lambda x: (x["last_seen_at"] or "", x["name"]))
    return out, days


def device_observations(device_id: str, limit: int = 50) -> list[dict[str, Any]]:
    with transaction() as conn:
        found = rows(
            conn,
            "SELECT * FROM inventory_observation WHERE device_id = %s "
            "ORDER BY observed_at DESC LIMIT %s",
            (device_id, limit),
        )
    keys = ("id", "device_id", "source", "source_id", "observed_at", "payload")
    return [{k: _plain(r[k]) for k in keys} for r in found]


def add_observation(
    device_id: str | None, source: str, source_id: str = "", payload: dict | None = None,
    observed_at: _dt.datetime | None = None,
) -> str:
    """Record what a source said. Phases 2-4 call this; nothing changes a device from here."""
    with transaction() as conn:
        row = rows(
            conn,
            "INSERT INTO inventory_observation (device_id, source, source_id, observed_at, payload) "
            "VALUES (%s, %s, %s, COALESCE(%s, now()), %s) RETURNING id",
            (device_id, source, source_id, observed_at, Jsonb(payload or {})),
        )[0]
    return str(row["id"])


# --- uniqueness, with the other device named in the error ---------------------

def check_unique(conn: Any, fields: dict[str, Any], status: str, own_id: str | None) -> None:
    """Name, serial and management IP against every other device.

    The database's unique indexes are the backstop; this is for the message, which
    names the device in the way so the person knows what to fix.
    """
    own = own_id or "00000000-0000-0000-0000-000000000000"
    checks = (
        ("name", "lower(name) = lower(%s)", "name"),
        ("serial", "serial = %s", "serial"),
        ("mgmt_ip", "mgmt_ip = %s AND status <> 'decommissioned'", "management IP"),
    )
    for field, test, label in checks:
        value = fields.get(field)
        if not value or (field == "mgmt_ip" and status == "decommissioned"):
            continue
        other = rows(conn, f"SELECT name FROM inventory_device WHERE {test} AND id <> %s LIMIT 1", (value, own))
        if other:
            raise InventoryError(f"{label} {value!r} is already used by device {other[0]['name']!r}.")


# --- source runs and observations (the SolarWinds feed) -------------------------

_RUN_KEYS = ("id", "status", "started_at", "finished_at", "counts", "error", "batch_id")


def start_run(source: str, actor: str) -> str:
    with transaction() as conn:
        return str(rows(
            conn, "INSERT INTO inventory_source_run (source, requested_by) VALUES (%s, %s) RETURNING id",
            (source, actor),
        )[0]["id"])


def running_since(source: str, minutes: int = 15) -> bool:
    with transaction() as conn:
        return bool(rows(
            conn,
            "SELECT 1 FROM inventory_source_run WHERE source = %s AND status = 'running' "
            "AND started_at > now() - make_interval(mins => %s) LIMIT 1", (source, minutes),
        ))


def finish_run(run_id: str, status: str, counts: dict | None = None, error: str = "", batch_id: str | None = None) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE inventory_source_run SET status=%s, finished_at=now(), counts=%s, error=%s, batch_id=%s "
            "WHERE id=%s", (status, Jsonb(counts or {}), error, batch_id, run_id),
        )


def last_run(source: str) -> dict[str, Any] | None:
    with transaction() as conn:
        found = rows(
            conn, "SELECT * FROM inventory_source_run WHERE source = %s ORDER BY started_at DESC LIMIT 1", (source,),
        )
    return {k: _plain(found[0][k]) for k in _RUN_KEYS} if found else None


def latest_observations(source: str) -> list[dict[str, Any]]:
    """The newest observation of each source_id."""
    with transaction() as conn:
        found = rows(
            conn,
            f"SELECT DISTINCT ON (source_id) device_id, source, source_id, observed_at FROM {_OBSERVED} o "
            "WHERE source = %s ORDER BY source_id, observed_at DESC", (source,),
        )
    return [{**r, "device_id": str(r["device_id"]) if r["device_id"] else None} for r in found]


def record_sync(source: str, observed: list[tuple[str | None, str, dict]], seen_device_ids: list[str], at: _dt.datetime) -> None:
    """One observation per node (device_id, source_id, payload) and `last_seen_at` on matched devices,
    in one transaction. `last_seen_at` is observational, not a field a proposal changes."""
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO inventory_observation (device_id, source, source_id, observed_at, payload) "
                "VALUES (%s, %s, %s, %s, %s)",
                [(d, source, sid, at, Jsonb(payload)) for d, sid, payload in observed],
            )
        if seen_device_ids:
            conn.execute(
                "UPDATE inventory_device SET last_seen_at = %s WHERE id = ANY(%s::uuid[])", (at, seen_device_ids)
            )


def pending_node_ids(source: str) -> set[str]:
    with transaction() as conn:
        return {
            r["n"] for r in rows(
                conn, "SELECT DISTINCT evidence->>'node_id' AS n FROM inventory_proposal "
                "WHERE source = %s AND status = 'pending'", (source,))
        }


def source_stats(source: str, stale_days: int) -> dict[str, int]:
    limit = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=stale_days)
    latest = latest_observations(source)
    return {
        "nodes_seen": len(latest),
        "stale_count": len({o["device_id"] for o in latest if o["device_id"] and o["observed_at"] < limit}),
    }
