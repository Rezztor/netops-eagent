"""Where Azure's own findings live: the only module that touches `cloud_signal` (`helpers/schema/0019_cloud_signal.sql`).

Advisor recommendations, policy non-compliance, Azure's 14-day change records and Resource Health, one row each,
about one resource. A pull replaces them: `apply` makes one subscription's rows of each kind match one complete read.
Instance-wide data with no row-level security, so transactions use `db.NO_SCOPE` (through `inv.transaction`). Nothing
here reaches the network, and every filter value is a bound parameter.

A kind that was read and found nothing still leaves one marker row (`target_native_id = ''`), so "Azure reported
nothing" can be told from "this was never read" (a pull from before this table existed).
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from psycopg.types.json import Jsonb

from plugins._ea_inventory.helpers import store as inv

KINDS = ("advisor", "policy", "change", "health")
#: what `search` can list, and the word the tool and the screen use for each
SHOWN = {"advisor": "advisor", "policy": "policy", "health": "health", "azure_changes": "change"}
_IMPACT = "CASE lower(s.data->>'impact') WHEN 'high' THEN 0 WHEN 'medium' THEN 1 WHEN 'low' THEN 2 ELSE 3 END"
_FROM = (
    "FROM cloud_signal s JOIN cloud_container c ON c.id = s.subscription_id "
    "LEFT JOIN cloud_resource r ON r.account_id = c.account_id AND r.native_id = s.target_native_id "
    "AND r.removed_at IS NULL LEFT JOIN cloud_container g ON g.id = r.container_id"
)


def _id(value: Any) -> str:
    return str(value or "").strip().lower()


def apply(account_id: str, subscription_native_id: str, found: dict[str, list[dict[str, Any]]], now: _dt.datetime) -> dict[str, int]:
    """Replace one subscription's rows of each kind in `found` (`shape.signal` output) and return the row counts.

    A kind that is not a key of `found` is left as last seen. The subscription row must exist (`apply_containers`
    makes it before any read of its resources).
    """
    sub = _id(subscription_native_id).rsplit("/", 1)[-1]
    counts: dict[str, int] = {}
    with inv.transaction() as conn:
        owner = inv.rows(conn, "SELECT id FROM cloud_container WHERE account_id = %s AND native_id = %s AND kind = 'subscription'",
                         (account_id, sub))[0]["id"]
        for kind, items in found.items():
            conn.execute("DELETE FROM cloud_signal WHERE subscription_id = %s AND kind = %s", (owner, kind))
            rows = [(owner, kind, i["target"], i["at"], Jsonb(i["data"]), now) for i in items]
            rows.append((owner, kind, "", None, Jsonb({}), now))  # the marker
            with conn.cursor() as cur:
                cur.executemany("INSERT INTO cloud_signal (subscription_id, kind, target_native_id, at, data, fetched_at) "
                                "VALUES (%s,%s,%s,%s,%s,%s)", rows)
            counts[kind] = len(items)
    return counts


def _item(row: dict[str, Any]) -> dict[str, Any]:
    return {**row["data"], "at": inv._plain(row["at"])}


def of(native_id: str) -> dict[str, Any]:
    """Everything stored about one resource: `{read_at, advisor, policy, change, health}`, each a list newest first.
    `read_at` is when its subscription's rows were last replaced (the oldest of the four kinds), None when a kind
    was never read."""
    target = _id(native_id)
    with inv.transaction() as conn:
        found = inv.rows(conn, "SELECT kind, at, data FROM cloud_signal WHERE target_native_id = %s "
                               "ORDER BY at DESC NULLS LAST, id", (target,))
        read = {r["kind"]: r["t"] for r in inv.rows(
            conn, "SELECT s.kind, max(s.fetched_at) AS t FROM cloud_signal s JOIN cloud_resource r "
                  "ON r.subscription_id = s.subscription_id WHERE s.target_native_id = '' AND r.native_id = %s "
                  "GROUP BY s.kind", (target,))}
    out: dict[str, Any] = {k: [_item(r) for r in found if r["kind"] == k] for k in KINDS}
    out["read_at"] = inv._plain(min(read.values())) if len(read) == len(KINDS) else None
    return out


def read_at(kind: str) -> str | None:
    """When Azure's rows of this kind were last replaced, over the subscriptions that have been read; None when none was."""
    with inv.transaction() as conn:
        found = inv.rows(conn, "SELECT min(t) AS t FROM (SELECT max(fetched_at) AS t FROM cloud_signal "
                               "WHERE kind = %s AND target_native_id = '' GROUP BY subscription_id) x", (SHOWN.get(kind, kind),))
    return inv._plain(found[0]["t"]) if found else None


def search(show: str, filters: dict[str, Any], limit: int = 25) -> tuple[list[dict[str, Any]], int]:
    """Stored rows of one kind across the estate, each with the resource it names (None when that resource is not
    stored). Advisor comes highest impact first, health only for what is not Available, the rest newest first."""
    kind = SHOWN[show]
    where, args = ["s.kind = %s", "s.target_native_id <> ''"], [kind]
    for key, alias in (("subscription", "c"), ("resource_group", "g")):
        if filters.get(key):
            where.append(f"(lower({alias}.name) = lower(%s) OR {alias}.native_id = lower(%s))")
            args += [str(filters[key])] * 2
    if filters.get("q"):
        where.append("(r.name ILIKE %s OR s.target_native_id ILIKE %s)")
        args += ["%" + str(filters["q"]).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"] * 2
    if kind == "health":
        where.append("coalesce(lower(s.data->>'state'), '') <> 'available'")
    order = f"{_IMPACT}, s.at DESC NULLS LAST, s.id" if kind == "advisor" else "s.at DESC NULLS LAST, s.id"
    clause = " WHERE " + " AND ".join(where)
    with inv.transaction() as conn:
        total = inv.rows(conn, f"SELECT count(*) AS n {_FROM}{clause}", args)[0]["n"]
        found = inv.rows(
            conn, "SELECT s.target_native_id, s.at, s.data, r.name, r.type, r.kind AS rkind, c.name AS sub_name, "
                  f"g.name AS rg_name {_FROM}{clause} ORDER BY {order} LIMIT %s", [*args, max(1, min(int(limit or 25), 200))])
    return [{**_item(r), "resource": r["name"] or r["target_native_id"].rsplit("/", 1)[-1], "type": r["type"],
             "subscription": r["sub_name"], "resource_group": r["rg_name"], "target": r["target_native_id"]}
            for r in found], total
