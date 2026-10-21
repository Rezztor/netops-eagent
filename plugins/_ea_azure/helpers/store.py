"""Where the Azure inventory lives: the only module that touches the `cloud_*` tables.

Instance-wide data with no row-level security (see `helpers/schema/0016_cloud_inventory.sql`), so a
transaction here is opened with `db.NO_SCOPE`. A pull writes through `account`, `apply_containers` and
`apply_subscription`; nothing here is written by a person, and nothing reaches the network. Every filter
value is a bound parameter; `by` is checked against a fixed tuple and the only text formatted into SQL
comes from the constants in this file.
"""

from __future__ import annotations

import datetime as _dt
import json
import uuid
from typing import Any

from psycopg.types.json import Jsonb

from plugins._ea_inventory.helpers import store as inv

SOURCE = "azure"
BY = ("subscription", "resource_group", "category", "type", "region", "state")
WITHOUT = ("route_table", "security_group", "tags", "tag:<name>")
ATTENTION = ("stopped_failed", "no_tags", "exposed", "changed", "waste", "drifted")
#: What a changed resource is compared on; `sku`, `tags` and `props` are compared key by key.
_COMPARED = ("name", "kind", "region", "state", "state_detail", "sku", "tags", "props")
_DICTS = ("sku", "tags", "props")
_SUBNET = "microsoft.network/virtualnetworks/subnets"
_ACCEPTED = "status IN ('succeeded','partial')"


class BadFilter(ValueError):
    """A filter value that is not one of the allowed ones; the message names them."""


class Ambiguous(Exception):
    """A name that matches several live resources; `candidates` holds up to ten."""

    def __init__(self, candidates: list[dict[str, Any]]):
        super().__init__("several resources have that name")
        self.candidates = candidates


def _id(value: Any) -> str:
    return str(value or "").strip().lower()


def _sub_id(value: Any) -> str:
    """A subscription's native id is its GUID, whether it arrives bare or as `/subscriptions/<guid>`."""
    v = _id(value)
    return v.split("/subscriptions/", 1)[1].split("/", 1)[0] if "/subscriptions/" in v else v


def _json(value: Any) -> Any:
    """A value as the database hands it back, so a fresh one compares equal to a stored one."""
    return json.loads(json.dumps(value if value is not None else {}, default=str))


def _label(type_: str, kind: str) -> str:
    from plugins._ea_azure.helpers import categories

    return categories.label_of(type_, kind)


# --- writes ----------------------------------------------------------------------

def account(tenant_id: str, name: str = "") -> str:
    with inv.transaction() as conn:
        return str(inv.rows(
            conn,
            "INSERT INTO cloud_account (provider, native_id, name) VALUES (%s, %s, %s) "
            "ON CONFLICT (provider, native_id) DO UPDATE SET name = CASE WHEN EXCLUDED.name <> '' "
            "THEN EXCLUDED.name ELSE cloud_account.name END RETURNING id", (SOURCE, _id(tenant_id), name),
        )[0]["id"])


def apply_containers(account_id: str, subscriptions: list[dict], groups: list[dict], now: _dt.datetime) -> None:
    """Upsert the subscriptions in scope and their resource groups.

    A subscription is `{id (guid), name, state, region?, tags?, props?}` and a group is `{id (its resource id),
    subscription (guid), name, region?, tags?, props?}`. A stored subscription that is not in `subscriptions`
    gets `readable = false` and nothing else, so its resources stay as last seen. A stored group of a listed
    subscription that is not in `groups` is marked removed.
    """
    with inv.transaction() as conn:
        by_sub: dict[str, str] = {}
        for s in subscriptions:
            native = _sub_id(s["id"])
            by_sub[native] = str(inv.rows(
                conn,
                "INSERT INTO cloud_container (account_id, kind, native_id, name, state, region, tags, props, "
                "readable, last_seen_at) VALUES (%s, 'subscription', %s, %s, %s, %s, %s, %s, true, %s) "
                "ON CONFLICT (account_id, native_id) DO UPDATE SET name = EXCLUDED.name, state = EXCLUDED.state, "
                "region = EXCLUDED.region, tags = EXCLUDED.tags, props = EXCLUDED.props, readable = true, "
                "last_seen_at = EXCLUDED.last_seen_at, removed_at = NULL RETURNING id",
                (account_id, native, s.get("name") or native, s.get("state") or "", s.get("region") or "",
                 Jsonb(s.get("tags") or {}), Jsonb(s.get("props") or {}), now),
            )[0]["id"])
        conn.execute(
            "UPDATE cloud_container SET readable = false WHERE account_id = %s AND kind = 'subscription' "
            "AND NOT (id = ANY(%s::uuid[]))", (account_id, list(by_sub.values())))
        kept: list[str] = []
        for g in groups:
            parent = by_sub.get(_sub_id(g["subscription"]))
            if not parent:
                continue
            native = _id(g["id"])
            kept.append(native)
            conn.execute(
                "INSERT INTO cloud_container (account_id, parent_id, kind, native_id, name, region, tags, props, "
                "last_seen_at) VALUES (%s, %s, 'resource_group', %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (account_id, native_id) DO UPDATE SET parent_id = EXCLUDED.parent_id, "
                "name = EXCLUDED.name, region = EXCLUDED.region, tags = EXCLUDED.tags, props = EXCLUDED.props, "
                "last_seen_at = EXCLUDED.last_seen_at, removed_at = NULL",
                (account_id, parent, native, g.get("name") or native, g.get("region") or "",
                 Jsonb(g.get("tags") or {}), Jsonb(g.get("props") or {}), now))
        conn.execute(
            "UPDATE cloud_container SET removed_at = %s WHERE account_id = %s AND kind = 'resource_group' "
            "AND removed_at IS NULL AND parent_id = ANY(%s::uuid[]) AND NOT (native_id = ANY(%s))",
            (now, account_id, list(by_sub.values()), kept))


def _relation_value(rels: list[dict]) -> list:
    """A kind's relations as the change log shows them: ids, with the props of a peering kept beside its id."""
    out = [{"id": r["to_native_id"], **r["props"]} if r["props"] else r["to_native_id"] for r in rels]
    return sorted(out, key=lambda x: json.dumps(x, sort_keys=True))


def _diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for field in _COMPARED:
        if field in _DICTS:
            for key in sorted({*old[field], *new[field]}):
                if old[field].get(key) != new[field].get(key):
                    out[f"{field}.{key}"] = {"old": old[field].get(key), "new": new[field].get(key)}
        elif old[field] != new[field]:
            out[field] = {"old": old[field], "new": new[field]}
    for kind in sorted({*old["relations"], *new["relations"]}):
        a, b = _relation_value(old["relations"].get(kind, [])), _relation_value(new["relations"].get(kind, []))
        if a != b:
            out[f"relations.{kind}"] = {"old": a, "new": b}
    return out


def _by_kind(relations: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in relations:
        out.setdefault(r["kind"], []).append({"to_native_id": _id(r["to_native_id"]), "props": _json(r.get("props"))})
    return out


def _flatten(shaped: list[dict]) -> dict[str, dict]:
    """Every resource and its children by native id, a parent before its children."""
    out: dict[str, dict] = {}
    for item in shaped:
        out[_id(item["native_id"])] = {**item, "parent": None}
        for child in item.get("children") or []:
            out[_id(child["native_id"])] = {**child, "parent": _id(item["native_id"])}
    return out


def apply_subscription(account_id: str, subscription_native_id: str, shaped: list[dict], *, run_id: str,
                       now: _dt.datetime) -> dict[str, int]:
    """Make one subscription's stored resources match one complete read of it, in one transaction.

    `shaped` is `shape.reduce` output. Resources missing from it are marked removed, a removed one that
    is back is cleared, relations of the resources written are replaced, and a `cloud_change` row records
    each added, changed, removed and returned resource. A subscription's first read is a baseline: it writes
    no `added` rows and reports `added: 0`. The caller never calls this for a subscription it failed to read.
    """
    sub = _sub_id(subscription_native_id)
    items = _flatten(shaped)
    counts = {"seen": len(items), "added": 0, "changed": 0, "removed": 0, "returned": 0}
    with inv.transaction() as conn:
        s = inv.rows(
            conn,
            "INSERT INTO cloud_container (account_id, kind, native_id, name) VALUES (%s, 'subscription', %s, %s) "
            "ON CONFLICT (account_id, native_id) DO UPDATE SET last_seen_at = EXCLUDED.last_seen_at "
            "RETURNING id, last_read_at", (account_id, sub, sub))[0]
        sub_id, baseline = s["id"], s["last_read_at"] is None
        groups = {r["native_id"]: r["id"] for r in inv.rows(
            conn, "SELECT id, native_id FROM cloud_container WHERE parent_id = %s", (sub_id,))}
        old = {r["native_id"]: r for r in inv.rows(
            conn, "SELECT * FROM cloud_resource WHERE subscription_id = %s", (sub_id,))}
        old_rel: dict[Any, dict[str, list[dict]]] = {}
        for r in inv.rows(conn, "SELECT f.id AS from_id, r.kind, r.to_native_id, r.props FROM cloud_relation r "
                                "JOIN cloud_resource f ON f.id = r.from_id WHERE f.subscription_id = %s", (sub_id,)):
            old_rel.setdefault(r["from_id"], {}).setdefault(r["kind"], []).append(
                {"to_native_id": r["to_native_id"], "props": r["props"]})

        ids: dict[str, Any] = {n: o["id"] for n, o in old.items()}
        for native in items:
            ids.setdefault(native, uuid.uuid4())
        inserts, updates, touched, changes, relations = [], [], [], [], []
        replace: list[Any] = []
        for native, it in items.items():
            rid = ids[native]
            rg = _id(f"/subscriptions/{sub}/resourcegroups/{it.get('resource_group') or ''}")
            if rg not in groups:  # a group the container read did not list: keep the resource, make the group
                groups[rg] = inv.rows(
                    conn,
                    "INSERT INTO cloud_container (account_id, parent_id, kind, native_id, name) "
                    "VALUES (%s, %s, 'resource_group', %s, %s) ON CONFLICT (account_id, native_id) "
                    "DO UPDATE SET removed_at = NULL RETURNING id",
                    (account_id, sub_id, rg, it.get("resource_group") or ""))[0]["id"]
            new = {f: _json(it.get(f)) for f in _COMPARED}
            for f in ("name", "kind", "region", "state", "state_detail"):
                new[f] = str(it.get(f) or "")
            new["relations"] = _by_kind(it.get("relations") or [])
            parent = ids[it["parent"]] if it["parent"] else None
            fields = (new["kind"], it["category"], new["region"], new["state"], new["state_detail"],
                      Jsonb(new["sku"]), Jsonb(new["tags"]), Jsonb(new["props"]))
            if native not in old:
                inserts.append((rid, account_id, sub_id, groups[rg], parent, native, new["name"], _id(it["type"]),
                                *fields, now, now))
                replace.append(rid)
                if not baseline:
                    counts["added"] += 1
                    changes.append((run_id, rid, now, "added", Jsonb({})))
            else:
                before = {**{f: old[native][f] for f in _COMPARED}, "relations": old_rel.get(rid, {})}
                diff = _diff(before, new)
                returned = old[native]["removed_at"] is not None
                if diff or returned:
                    updates.append((new["name"], *fields, groups[rg], parent, now, now, rid))
                    kind = "returned" if returned else "changed"
                    counts[kind] += 1
                    changes.append((run_id, rid, now, kind, Jsonb(diff)))
                    if any(k.startswith("relations.") for k in diff):
                        replace.append(rid)
                else:
                    touched.append(rid)
            for kind, rels in new["relations"].items():
                relations += [(rid, kind, r["to_native_id"], Jsonb(r["props"])) for r in rels]
        gone = [(run_id, o["id"], now, "removed", Jsonb({})) for n, o in old.items()
                if n not in items and o["removed_at"] is None]
        counts["removed"] = len(gone)

        with conn.cursor() as cur:
            if inserts:
                cur.executemany(
                    "INSERT INTO cloud_resource (id, account_id, subscription_id, container_id, parent_id, native_id, "
                    "name, type, kind, category, region, state, state_detail, sku, tags, props, first_seen_at, "
                    "last_seen_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    inserts)
            if updates:
                cur.executemany(
                    "UPDATE cloud_resource SET name=%s, kind=%s, category=%s, region=%s, state=%s, state_detail=%s, "
                    "sku=%s, tags=%s, props=%s, container_id=%s, parent_id=%s, last_seen_at=%s, changed_at=%s, "
                    "removed_at=NULL WHERE id=%s", updates)
            if gone:
                cur.executemany(
                    "UPDATE cloud_resource SET removed_at = %s WHERE id = %s", [(now, g[1]) for g in gone])
            if changes or gone:
                cur.executemany(
                    "INSERT INTO cloud_change (run_id, resource_id, at, change, fields) VALUES (%s,%s,%s,%s,%s)",
                    [*changes, *gone])
            if replace:
                cur.execute("DELETE FROM cloud_relation WHERE from_id = ANY(%s::uuid[])", (replace,))
                wanted = set(replace)
                cur.executemany(
                    "INSERT INTO cloud_relation (from_id, kind, to_native_id, props) VALUES (%s,%s,%s,%s) "
                    "ON CONFLICT DO NOTHING", [r for r in relations if r[0] in wanted])
        if touched:
            conn.execute("UPDATE cloud_resource SET last_seen_at = %s WHERE id = ANY(%s::uuid[])", (now, touched))
        conn.execute(  # a relation whose target arrived later, or whose target is in another subscription
            "UPDATE cloud_relation r SET to_id = t.id FROM cloud_resource f, cloud_resource t "
            "WHERE r.to_id IS NULL AND f.id = r.from_id AND t.account_id = f.account_id AND t.native_id = r.to_native_id")
        conn.execute("UPDATE cloud_container SET last_read_at = %s, last_seen_at = %s WHERE id = %s", (now, now, sub_id))
    return counts


def prune(keep_removed_days: int, keep_history_days: int) -> dict[str, int]:
    """Delete what is past its keep time: removed resources (with their relations and changes), old change
    rows, and removed resource groups that hold nothing live."""
    removed = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=int(keep_removed_days))
    history = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=int(keep_history_days))
    with inv.transaction() as conn:
        out = {}
        for name, sql, cutoff in (
            ("resources", "DELETE FROM cloud_resource WHERE removed_at < %s", removed),
            ("changes", "DELETE FROM cloud_change WHERE at < %s", history),
            ("containers", "DELETE FROM cloud_container c WHERE kind = 'resource_group' AND removed_at < %s "
                           "AND NOT EXISTS (SELECT 1 FROM cloud_resource r WHERE r.container_id = c.id)", removed),
        ):
            with conn.cursor() as cur:
                cur.execute(sql, (cutoff,))
                out[name] = cur.rowcount
    return out


# --- reads -----------------------------------------------------------------------

def _run_json(row: dict[str, Any]) -> dict[str, Any]:
    return {k: inv._plain(row[k]) for k in (*inv._RUN_KEYS, "requested_by")}


def _last_pull(conn: Any) -> dict[str, Any] | None:
    """The newest pull that read something: the one the stored data is "as of"."""
    found = inv.rows(
        conn, f"SELECT * FROM inventory_source_run WHERE source = %s AND {_ACCEPTED} "
              "ORDER BY started_at DESC LIMIT 1", (SOURCE,))
    return _run_json(found[0]) if found else None


def as_of() -> dict[str, Any]:
    with inv.transaction() as conn:
        subs = inv.rows(conn, "SELECT id, name, last_read_at, readable FROM cloud_container "
                              "WHERE kind = 'subscription' ORDER BY lower(name)")
        return {"last_pull": _last_pull(conn), "subscriptions": [
            {"id": str(s["id"]), "name": s["name"], "last_read_at": inv._plain(s["last_read_at"]),
             "readable": s["readable"]} for s in subs]}


def subscriptions() -> list[dict[str, Any]]:
    """Every stored subscription: `{native_id (its GUID), name, readable}`. `cost.py` asks Cost Management per GUID."""
    with inv.transaction() as conn:
        return [{"native_id": s["native_id"], "name": s["name"], "readable": s["readable"]} for s in inv.rows(
            conn, "SELECT native_id, name, readable FROM cloud_container WHERE kind = 'subscription' "
                  "AND removed_at IS NULL ORDER BY lower(name)")]


def has_data() -> bool:
    with inv.transaction() as conn:
        return bool(inv.rows(conn, "SELECT 1 FROM cloud_container LIMIT 1"))


def runs(limit: int = 50) -> list[dict[str, Any]]:
    with inv.transaction() as conn:
        return [_run_json(r) for r in inv.rows(
            conn, "SELECT * FROM inventory_source_run WHERE source = %s ORDER BY started_at DESC LIMIT %s",
            (SOURCE, max(1, min(int(limit or 50), 200))))]


def tree() -> dict[str, Any]:
    """The newest tenant's subscriptions and resource groups with counts of live resources at every level.

    ponytail: one tenant per instance in phase 1; the other reads do not filter by account.
    """
    with inv.transaction() as conn:
        acct = inv.rows(conn, "SELECT id, native_id, name FROM cloud_account ORDER BY created_at DESC LIMIT 1")
        if not acct:
            return {"account": None, "subscriptions": []}
        containers = inv.rows(
            conn, "SELECT * FROM cloud_container WHERE account_id = %s AND removed_at IS NULL "
                  "ORDER BY lower(name)", (acct[0]["id"],))
        per: dict[Any, dict[str, int]] = {}
        for r in inv.rows(conn, "SELECT subscription_id, container_id, category, count(*) AS n FROM cloud_resource "
                                "WHERE removed_at IS NULL GROUP BY 1, 2, 3"):
            for key in (r["subscription_id"], r["container_id"]):
                cats = per.setdefault(key, {})
                cats[r["category"]] = cats.get(r["category"], 0) + r["n"]
    def node(c: dict[str, Any]) -> dict[str, Any]:
        cats = per.get(c["id"], {})
        return {"id": str(c["id"]), "native_id": c["native_id"], "name": c["name"], "state": c["state"],
                "region": c["region"], "tags": c["tags"], "props": c["props"], "readable": c["readable"],
                "last_read_at": inv._plain(c["last_read_at"]), "count": sum(cats.values()), "categories": cats}
    subs = [{**node(c), "groups": []} for c in containers if c["kind"] == "subscription"]
    by_id = {s["id"]: s for s in subs}
    for c in containers:
        if c["kind"] == "resource_group" and str(c["parent_id"]) in by_id:
            by_id[str(c["parent_id"])]["groups"].append(node(c))
    a = acct[0]
    return {"account": {"id": str(a["id"]), "native_id": a["native_id"], "name": a["name"]}, "subscriptions": subs}


_FROM = (
    "FROM cloud_resource r JOIN cloud_container s ON s.id = r.subscription_id "
    "JOIN cloud_container g ON g.id = r.container_id LEFT JOIN cloud_resource p ON p.id = r.parent_id"
)
_SELECT = "SELECT r.*, s.name AS sub_name, g.name AS rg_name, p.name AS parent_name "
#: the types that can have each relation, so "without a route table" does not list every resource
_NEEDS = {"route_table": f"'{_SUBNET}'", "nsg": f"'{_SUBNET}', 'microsoft.network/networkinterfaces'"}
_BY_SQL = {
    "subscription": "s.name", "resource_group": "g.name || ' (' || s.name || ')'", "category": "r.category",
    "type": "r.type", "region": "r.region", "state": "r.state",
}


def _resource_json(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(r["id"]), "native_id": r["native_id"], "name": r["name"], "type": r["type"], "kind": r["kind"],
        "label": _label(r["type"], r["kind"]), "category": r["category"],
        "subscription": {"id": str(r["subscription_id"]), "name": r["sub_name"]},
        "resource_group": {"id": str(r["container_id"]), "name": r["rg_name"]},
        "parent": {"id": str(r["parent_id"]), "name": r["parent_name"]} if r["parent_id"] else None,
        "region": r["region"], "state": r["state"], "state_detail": r["state_detail"], "sku": r["sku"],
        "tags": r["tags"], "props": r["props"], "first_seen_at": inv._plain(r["first_seen_at"]),
        "last_seen_at": inv._plain(r["last_seen_at"]), "changed_at": inv._plain(r["changed_at"]),
        "removed_at": inv._plain(r["removed_at"]),
    }


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _truthy(value: Any) -> bool:
    return value is True or str(value).strip().lower() in ("1", "true", "yes")


def _exposed_ids(conn: Any) -> list[str]:
    """Ids of live resources `exposure.reasons` flags. A scan in Python, like the rules themselves."""
    from plugins._ea_azure.helpers import exposure

    found = inv.rows(conn, _SELECT + _FROM + " WHERE r.removed_at IS NULL")
    links: dict[str, list[dict[str, Any]]] = {}
    for link in _links(conn, "out", [r["id"] for r in found]):
        links.setdefault(link["owner"], []).append({k: v for k, v in link.items() if k != "owner"})
    # A machine is exposed through its network card, so a `nic` link carries that card's own links
    # (the same shape `exposure.of` builds for one record). Without it the tile misses every such machine.
    for own in links.values():
        for link in own:
            if link.get("kind") == "nic" and link.get("id"):
                link["links_out"] = links.get(str(link["id"]), [])
    return [str(r["id"]) for r in found if exposure.reasons(_resource_json(r), links.get(str(r["id"]), []))]


def _graph(conn: Any) -> tuple[list[dict[str, Any]], dict[str, list], dict[str, list]]:
    """Every live resource and the links out of and into each, by row id: what the waste rules read.

    ponytail: a scan in Python over the live rows, like `_exposed_ids`; move the rules into SQL past 50,000 rows.
    """
    rows = [_resource_json(r) for r in inv.rows(conn, _SELECT + _FROM + " WHERE r.removed_at IS NULL")]
    ids = [r["id"] for r in rows]
    out: dict[str, list] = {}
    inn: dict[str, list] = {}
    for into, direction in ((out, "out"), (inn, "in")):
        for link in _links(conn, direction, ids):
            into.setdefault(link["owner"], []).append({k: v for k, v in link.items() if k != "owner"})
    return rows, out, inn


def _waste(conn: Any) -> list[dict[str, Any]]:
    from plugins._ea_azure.helpers import waste as rules

    last = _last_pull(conn)
    return rules.findings(*_graph(conn), last["started_at"] if last else None)


#: Change lines on a resource the product made (`cloud_managed`) that no apply explains. A line is explained when it is in the
#: first accepted pull that started after the apply (the pull the apply asked for); a later pull's `changed` or `removed`
#: line is drift until an engineer accepts it (`accepted_at`, the newest line they saw). Nothing here is stored per resource.
#: ponytail: an apply that lands in the middle of a pull explains that pull and the next; rows older than the history
#: keep (`prune`) are forgotten, so a drift nobody accepted for that long stops showing.
_DRIFT = (
    "SELECT r.id, m.workspace, m.change_id, c.at, c.change, c.fields FROM cloud_managed m "
    "JOIN cloud_resource r ON r.native_id = m.native_id JOIN cloud_change c ON c.resource_id = r.id "
    "JOIN inventory_source_run u ON u.id = c.run_id "
    "WHERE c.change IN ('changed','removed') AND COALESCE(u.finished_at, 'infinity') >= m.applied_at "
    "AND (m.accepted_at IS NULL OR c.at > m.accepted_at) "
    "AND EXISTS (SELECT 1 FROM inventory_source_run p WHERE p.source = %s AND p.status IN ('succeeded','partial') "
    "AND p.started_at >= m.applied_at AND p.started_at < u.started_at) ORDER BY c.at, c.id"
)


def _drift(conn: Any) -> dict[str, dict[str, Any]]:
    """`{resource row id: {workspace, change_id, through, lines: [str]}}` for every drifted resource."""
    out: dict[str, dict[str, Any]] = {}
    for row in inv.rows(conn, _DRIFT, (SOURCE,)):
        one = out.setdefault(str(row["id"]), {"workspace": row["workspace"], "change_id": row["change_id"], "lines": []})
        one["through"] = inv._plain(row["at"])  # newest line seen: what an acceptance covers
        day = str(inv._plain(row["at"]))[:10]
        if row["change"] == "removed":
            one["lines"].append(f"{day}: no longer in Azure")
        else:
            one["lines"] += [f"{day}: {k} was {_short(v.get('old'))}, now {_short(v.get('new'))}" for k, v in (row["fields"] or {}).items()]
    return out


def _short(value: Any, limit: int = 60) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def drifted(filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The resources the product made that changed in Azure outside it, each with what changed (`lines`, newest last),
    its workspace, the change that last applied it and `through` (the newest change line, what accepting covers).
    `removed` ones are listed too: a managed resource that vanished is the largest drift there is."""
    with inv.transaction() as conn:
        clause, args = _where(conn, {**(filters or {}), "attention": "", "include_removed": "yes"})
        found = {str(r["id"]): r for r in inv.rows(conn, f"{_SELECT}{_FROM}{clause}", args)}
        return [{**{k: v for k, v in _resource_json(found[i]).items() if k in ("id", "native_id", "name", "type", "label")},
                 "subscription": found[i]["sub_name"], "resource_group": found[i]["rg_name"], **d}
                for i, d in _drift(conn).items() if i in found]


def waste(filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The waste findings (`waste.findings`, no prices) for the live resources `filters` select. A finding needs the
    whole estate (a disk's owner, a gateway's connections), so the rules run over all of it and the filter only picks."""
    with inv.transaction() as conn:
        clause, args = _where(conn, {**(filters or {}), "attention": ""})
        keep = {str(r["id"]) for r in inv.rows(conn, f"SELECT r.id {_FROM}{clause}", args)}
        return [f for f in _waste(conn) if f["id"] in keep]


def _where(conn: Any, f: dict[str, Any]) -> tuple[str, list[Any]]:
    where: list[str] = []
    args: list[Any] = []

    def val(key: str) -> str:
        return str(f.get(key) or "").strip()

    if not _truthy(f.get("include_removed")) and f.get("attention") != "drifted":  # a vanished managed resource is drift
        where.append("r.removed_at IS NULL")
    if val("q"):
        cols = ("r.name", "r.native_id", "r.type", "r.kind", "r.region", "r.state_detail")
        where.append("(" + " OR ".join(f"{c} ILIKE %s" for c in cols) + ")")
        args += [_like(val("q"))] * len(cols)
    for key, alias in (("subscription", "s"), ("resource_group", "g")):
        if val(key):
            where.append(f"(lower({alias}.name) = lower(%s) OR {alias}.native_id = lower(%s) OR {alias}.id::text = %s)")
            args += [val(key)] * 3
    for key, expr in (("category", "r.category = %s"), ("state", "r.state = %s"), ("type", "r.type = lower(%s)"),
                      ("region", "lower(r.region) = lower(%s)")):
        if val(key):
            where.append(expr)
            args.append(val(key))
    if val("tag"):
        name, _, value = val("tag").partition("=")
        where.append("EXISTS (SELECT 1 FROM jsonb_each_text(r.tags) t WHERE lower(t.key) = lower(%s)"
                     + (" AND t.value = %s)" if "=" in val("tag") else ")"))
        args += [name, value] if "=" in val("tag") else [name]
    if val("without"):
        tag = val("without")[4:].strip() if val("without").lower().startswith("tag:") else ""
        if val("without") not in WITHOUT and not tag:
            raise BadFilter(f"without must be one of {', '.join(WITHOUT)}")
        if tag:  # a subnet cannot carry a tag, so it is never "missing" one
            where.append("r.type <> %s AND NOT EXISTS (SELECT 1 FROM jsonb_each_text(r.tags) t WHERE lower(t.key) = lower(%s))")
            args += [_SUBNET, tag]
        elif val("without") == "tags":
            where.append("r.tags = '{}'::jsonb")
        else:
            kind = "nsg" if val("without") == "security_group" else "route_table"
            where.append(f"r.type IN ({_NEEDS[kind]}) AND NOT EXISTS "
                         "(SELECT 1 FROM cloud_relation x WHERE x.from_id = r.id AND x.kind = %s)")
            args.append(kind)
    attention = val("attention")
    if attention and attention not in ATTENTION:
        raise BadFilter(f"attention must be one of {', '.join(ATTENTION)}")
    since = "last_pull" if attention == "changed" else val("changed_since")
    if since == "last_pull":
        pull = _last_pull(conn)
        where.append("EXISTS (SELECT 1 FROM cloud_change c WHERE c.resource_id = r.id AND c.run_id = %s)"
                     if pull else "false")
        args += [pull["id"]] if pull else []
    elif since:
        try:
            at = _dt.datetime.fromisoformat(since)
        except ValueError:
            raise BadFilter("changed_since must be an ISO date or last_pull") from None
        where.append("EXISTS (SELECT 1 FROM cloud_change c WHERE c.resource_id = r.id AND c.at >= %s)")
        args.append(at if at.tzinfo else at.replace(tzinfo=_dt.timezone.utc))
    if attention == "stopped_failed":
        where.append("r.state IN ('stopped','failed')")
    elif attention == "no_tags":
        where.append("r.tags = '{}'::jsonb")
    elif attention == "exposed":
        where.append("r.id = ANY(%s::uuid[])")
        args.append(_exposed_ids(conn))
    elif attention == "waste":
        where.append("r.id = ANY(%s::uuid[])")
        args.append([f["id"] for f in _waste(conn)])
    elif attention == "drifted":
        where.append("r.id = ANY(%s::uuid[])")
        args.append(list(_drift(conn)))
    return (" WHERE " + " AND ".join(where)) if where else "", args


def search(filters: dict[str, Any], limit: int = 100, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
    with inv.transaction() as conn:
        clause, args = _where(conn, filters or {})
        total = inv.rows(conn, f"SELECT count(*) AS n {_FROM}{clause}", args)[0]["n"]
        found = inv.rows(
            conn, f"{_SELECT}{_FROM}{clause} ORDER BY lower(r.name), r.native_id LIMIT %s OFFSET %s",
            [*args, max(1, min(int(limit or 100), 500)), max(0, int(offset or 0))])
    return [_resource_json(r) for r in found], total


def counts(filters: dict[str, Any], by: str) -> tuple[list[dict[str, Any]], int]:
    if by not in BY:
        raise BadFilter(f"by must be one of {', '.join(BY)}")
    with inv.transaction() as conn:
        clause, args = _where(conn, filters or {})
        found = inv.rows(
            conn, f"SELECT {_BY_SQL[by]} AS key, count(*) AS n {_FROM}{clause} GROUP BY 1 ORDER BY 2 DESC, 1", args)
    return [{"key": r["key"], "count": r["n"]} for r in found], sum(r["n"] for r in found)


def _links(conn: Any, direction: str, ids: Any) -> list[dict[str, Any]]:
    """Relations out of (or into) one resource id or a list of them; each carries the other end."""
    many = isinstance(ids, (list, tuple))
    ids = list(ids) if many else [ids]
    if direction == "out":
        sql = ("SELECT x.from_id AS owner, x.kind, x.to_native_id, x.props, t.id, t.name, t.type, t.kind AS rkind, "
               "t.state FROM cloud_relation x LEFT JOIN cloud_resource t ON t.id = x.to_id WHERE x.from_id = ANY(%s::uuid[]) "
               "ORDER BY x.kind, x.to_native_id")
    else:
        sql = ("SELECT x.to_id AS owner, x.kind, f.native_id AS to_native_id, x.props, f.id, f.name, f.type, f.kind AS rkind, "
               "f.state FROM cloud_relation x JOIN cloud_resource f ON f.id = x.from_id WHERE x.to_id = ANY(%s::uuid[]) "
               "AND f.removed_at IS NULL ORDER BY x.kind, f.name")
    out = [{"owner": str(r["owner"]), "kind": r["kind"], "to_native_id": r["to_native_id"], "props": r["props"],
            "id": str(r["id"]) if r["id"] else None, "name": r["name"] or r["to_native_id"].rsplit("/", 1)[-1],
            "type": r["type"], "label": _label(r["type"], r["rkind"]) if r["type"] else "", "state": r["state"]}
           for r in inv.rows(conn, sql, (ids,))]
    if many:
        return out
    return [{k: v for k, v in o.items() if k != "owner"} for o in out]


def get(ref: str) -> dict[str, Any] | None:
    """One resource by row id, native id, or a name that matches exactly one live resource."""
    ref = str(ref or "").strip()
    with inv.transaction() as conn:
        try:
            found = inv.rows(conn, f"{_SELECT}{_FROM} WHERE r.id = %s", (str(uuid.UUID(ref)),))
        except ValueError:
            if ref.startswith("/"):
                found = inv.rows(conn, f"{_SELECT}{_FROM} WHERE r.native_id = %s", (ref.lower(),))
            else:
                found = inv.rows(conn, f"{_SELECT}{_FROM} WHERE lower(r.name) = lower(%s) AND r.removed_at IS NULL "
                                       "ORDER BY r.native_id LIMIT 11", (ref,))
        if len(found) > 1:
            raise Ambiguous([{**{k: v for k, v in _resource_json(r).items() if k in ("id", "native_id", "name", "type", "label")},
                              "subscription": r["sub_name"], "resource_group": r["rg_name"]} for r in found[:10]])
        if not found:
            return None
        res = _resource_json(found[0])
        kids = [_resource_json(r) for r in inv.rows(
            conn, f"{_SELECT}{_FROM} WHERE r.parent_id = %s AND r.removed_at IS NULL ORDER BY lower(r.name)", (res["id"],))]
        kid_links = _links(conn, "out", [k["id"] for k in kids]) if kids else []
        for k in kids:
            k["links_out"] = [{x: v for x, v in link.items() if x != "owner"} for link in kid_links if link["owner"] == k["id"]]
        changes = inv.rows(conn, "SELECT id, run_id, at, change, fields FROM cloud_change WHERE resource_id = %s "
                                 "ORDER BY at DESC, id LIMIT 10", (res["id"],))
        return {**res, "links_out": _links(conn, "out", res["id"]), "links_in": _links(conn, "in", res["id"]),
                "children": kids, "changes": [
                    {"id": str(c["id"]), "run_id": str(c["run_id"]), "at": inv._plain(c["at"]), "change": c["change"],
                     "fields": c["fields"]} for c in changes]}


def changes(run_id: str | None = None, since: str | None = None, limit: int = 100, offset: int = 0
            ) -> tuple[list[dict[str, Any]], int]:
    where, args = [], []
    if run_id:
        where.append("c.run_id::text = %s")
        args.append(str(run_id))
    if since:
        try:
            at = _dt.datetime.fromisoformat(str(since))
        except ValueError:
            raise BadFilter("since must be an ISO date") from None
        where.append("c.at >= %s")
        args.append(at if at.tzinfo else at.replace(tzinfo=_dt.timezone.utc))
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    join = ("FROM cloud_change c JOIN cloud_resource r ON r.id = c.resource_id "
            "JOIN cloud_container s ON s.id = r.subscription_id JOIN cloud_container g ON g.id = r.container_id")
    with inv.transaction() as conn:
        total = inv.rows(conn, f"SELECT count(*) AS n {join}{clause}", args)[0]["n"]
        found = inv.rows(
            conn, "SELECT c.id, c.run_id, c.at, c.change, c.fields, r.id AS rid, r.name, r.type, r.kind, r.category, "
                  f"r.removed_at, s.name AS sub_name, g.name AS rg_name {join}{clause} ORDER BY c.at DESC, r.name, c.id "
                  "LIMIT %s OFFSET %s", [*args, max(1, min(int(limit or 100), 500)), max(0, int(offset or 0))])
    return [{
        "id": str(c["id"]), "run_id": str(c["run_id"]), "at": inv._plain(c["at"]), "change": c["change"],
        "fields": c["fields"], "resource": {
            "id": str(c["rid"]), "name": c["name"], "type": c["type"], "label": _label(c["type"], c["kind"]),
            "category": c["category"], "subscription": c["sub_name"], "resource_group": c["rg_name"],
            "removed_at": inv._plain(c["removed_at"])},
    } for c in found], total
