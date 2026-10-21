import datetime as dt
import sys
import types

import pytest

from helpers import db
from plugins._ea_azure.helpers import store

SUB, SUB2 = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"
T = [dt.datetime(2026, 9, 1 + i, 2, tzinfo=dt.timezone.utc) for i in range(6)]
VNET = "microsoft.network/virtualnetworks"


def res(name, type="microsoft.compute/virtualmachines", rg="rg1", sub=SUB, category="compute", **kw):
    return {"native_id": f"/subscriptions/{sub}/resourcegroups/{rg}/providers/{type}/{name}".lower(), "name": name,
            "type": type, "kind": "", "region": "uaenorth", "subscription": sub, "resource_group": rg, "sku": {},
            "tags": {}, "props": {}, "state": "ok", "state_detail": "", "category": category, "relations": [],
            "children": [], **kw}


def rel(kind, target, **props):
    return {"kind": kind, "to_native_id": target, "props": props}


def pull(acct, shaped, n, sub=SUB):
    run = store.inv.start_run("azure", "test")
    counts = store.apply_subscription(acct, sub, shaped, run_id=run, now=T[n])
    store.inv.finish_run(run, "succeeded")
    return counts, run


def groups(sub=SUB, names=("rg1", "rg2")):
    return [{"id": f"/subscriptions/{sub}/resourceGroups/{n}", "subscription": sub, "name": n} for n in names]


@pytest.fixture
def acct(database):
    a = store.account("Tenant-1", "Contoso")
    store.apply_containers(a, [{"id": SUB, "name": "Prod", "state": "Enabled"}], groups(), T[0])
    return a


def hand():
    rt = res("rt1", "microsoft.network/routetables", category="networking", props={"routes": []})
    nsg = res("nsg1", "microsoft.network/networksecuritygroups", category="networking")
    web = res("sn-web", f"{VNET}/subnets", category="networking", props={"address_prefixes": ["10.0.1.0/24"]},
              relations=[rel("route_table", rt["native_id"]), rel("nsg", nsg["native_id"])])
    db_ = res("sn-db", f"{VNET}/subnets", category="networking", relations=[rel("nsg", nsg["native_id"])])
    vnet = res("vnet1", VNET, category="networking", tags={"env": "prod"}, props={"address_space": ["10.0.0.0/16"]},
               relations=[rel("peering", "/subscriptions/other/resourcegroups/x/providers/vnet/far", name="p", state="Connected")],
               children=[web, db_])
    vm = res("vm1", rg="rg2", state="stopped", state_detail="PowerState/deallocated", tags={"env": "prod", "team": "net"})
    vm2 = res("vm2", rg="rg2", state="failed", sku={"name": "D2"})
    return [vnet, rt, nsg, vm, vm2]


def rows(sql, *a):
    with db.transaction(db.NO_SCOPE) as c:
        return c.execute(sql, a).fetchall()


def test_hand_fixture_is_stored(acct):
    counts, _ = pull(acct, hand(), 1)
    assert counts == {"seen": 7, "added": 0, "changed": 0, "removed": 0, "returned": 0}  # a baseline is not a change
    assert rows("SELECT count(*) FROM cloud_change") == [(0,)]
    [sub] = store.tree()["subscriptions"]
    assert (sub["count"], sub["last_read_at"]) == (7, T[1].isoformat())
    assert {g["name"]: g["count"] for g in sub["groups"]} == {"rg1": 5, "rg2": 2}
    assert sub["groups"][0]["categories"] == {"networking": 5}
    net = store.get("vnet1")
    assert [c["name"] for c in net["children"]] == ["sn-db", "sn-web"]
    assert {r["kind"] for r in net["children"][1]["links_out"]} == {"route_table", "nsg"}
    assert net["links_out"][0]["id"] is None and net["links_out"][0]["name"] == "far"  # outside what we can read
    rt = store.get("rt1")
    assert [(r["kind"], r["name"]) for r in rt["links_in"]] == [("route_table", "sn-web")]
    assert store.get("sn-web")["parent"]["name"] == "vnet1"


def test_second_pull_writes_exactly_the_changes(acct):
    pull(acct, hand(), 1)
    shaped = hand()
    vnet, rt, nsg, vm, vm2 = shaped
    web = vnet["children"][0]
    web["props"] = {"address_prefixes": ["10.0.9.0/24"]}          # a prop
    vm["tags"] = {"env": "dev", "team": "net"}                    # a tag
    web["relations"] = [rel("nsg", nsg["native_id"])]             # a relation (the route table is gone)
    new = res("vm3")                                              # added
    shaped = [vnet, rt, nsg, vm, new]                             # vm2 is missing: removed
    counts, run = pull(acct, shaped, 2)
    assert counts == {"seen": 7, "added": 1, "changed": 2, "removed": 1, "returned": 0}
    got = {(c[0], c[1]): c[2] for c in rows(
        "SELECT r.name, c.change, c.fields FROM cloud_change c JOIN cloud_resource r ON r.id = c.resource_id WHERE c.run_id = %s", run)}
    assert got[("vm3", "added")] == {} and got[("vm2", "removed")] == {}
    assert got[("vm1", "changed")] == {"tags.env": {"old": "prod", "new": "dev"}}
    assert got[("sn-web", "changed")]["props.address_prefixes"] == {"old": ["10.0.1.0/24"], "new": ["10.0.9.0/24"]}
    assert got[("sn-web", "changed")]["relations.route_table"]["new"] == []
    assert len(got) == 4
    assert store.get("rt1")["links_in"] == []
    vm1, gone = store.get("vm1"), store.get("/subscriptions/%s/resourcegroups/rg2/providers/microsoft.compute/virtualmachines/vm2" % SUB)
    assert (vm1["changed_at"], vm1["last_seen_at"], vm1["first_seen_at"]) == (T[2].isoformat(), T[2].isoformat(), T[1].isoformat())
    assert (gone["removed_at"], gone["last_seen_at"]) == (T[2].isoformat(), T[1].isoformat())
    assert store.get("rt1")["changed_at"] is None
    # vm2 comes back
    counts, run = pull(acct, [*shaped, vm2], 3)
    assert (counts["returned"], counts["added"], counts["removed"]) == (1, 0, 0)
    back = store.get(gone["id"])
    assert (back["removed_at"], back["first_seen_at"]) == (None, T[1].isoformat())
    assert rows("SELECT change FROM cloud_change WHERE run_id = %s", run) == [("returned",)]
    # nothing differs: nothing is written, last_seen moves
    counts, run = pull(acct, [*shaped, vm2], 4)
    assert counts == {"seen": 8, "added": 0, "changed": 0, "removed": 0, "returned": 0}
    assert store.get("rt1")["last_seen_at"] == T[4].isoformat()
    assert rows("SELECT count(*) FROM cloud_change WHERE run_id = %s", run) == [(0,)]


def test_a_subscription_that_is_not_applied_keeps_its_rows(acct):
    store.apply_containers(acct, [{"id": SUB, "name": "Prod"}, {"id": SUB2, "name": "Dev"}], groups(SUB2, ("rg1",)), T[0])
    pull(acct, hand(), 1)
    pull(acct, [res("other", sub=SUB2)], 1, sub=SUB2)
    store.apply_containers(acct, [{"id": SUB, "name": "Prod"}], groups(), T[2])  # Dev is no longer listed
    got = {s["name"]: s for s in store.as_of()["subscriptions"]}
    assert got["Dev"]["readable"] is False and got["Prod"]["readable"] is True
    assert got["Dev"]["last_read_at"] == T[1].isoformat()
    assert store.get("other")["removed_at"] is None
    pull(acct, [], 3)  # a read that finds nothing removes only that subscription's resources
    assert store.search({}, 10)[1] == 1 and store.get("other")["removed_at"] is None


def test_prune_removes_only_what_is_past_its_keep_time(acct):
    old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=200)
    mid = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)
    run = store.inv.start_run("azure", "t")
    store.apply_subscription(acct, SUB, [res("a"), res("b"), res("c")], run_id=run, now=old)
    store.apply_subscription(acct, SUB, [res("b"), res("c"), res("d")], run_id=run, now=old)   # a removed 200 days ago, d added
    store.apply_subscription(acct, SUB, [res("c"), res("d")], run_id=run, now=mid)             # b removed 30 days ago
    out = store.prune(90, 365)
    assert out == {"resources": 1, "changes": 0, "containers": 0}
    assert {r["name"] for r in store.search({"include_removed": True}, 10)[0]} == {"b", "c", "d"}
    assert store.get("a") is None and store.get("b") is None  # removed rows are not found by name
    assert store.prune(90, 100) == {"resources": 0, "changes": 1, "containers": 0}  # d's 200-day-old 'added' row only


@pytest.fixture
def loaded(acct):
    store.apply_containers(acct, [{"id": SUB, "name": "Prod"}, {"id": SUB2, "name": "Dev"}], groups() + groups(SUB2, ("rg9",)), T[0])
    pull(acct, hand(), 1)
    pull(acct, [res("app9", "microsoft.web/sites", rg="rg9", sub=SUB2, category="web", tags={"env": "dev"})], 1, sub=SUB2)
    shaped = hand()
    shaped[4]["state"] = "running"  # vm2 changed in the last pull; vm1 is removed
    pull(acct, [shaped[0], shaped[1], shaped[2], shaped[4]], 2)
    return acct


def names(filters, **kw):
    return sorted(r["name"] for r in store.search(filters, 100, **kw)[0])


def test_every_filter(loaded):
    assert sorted(names({})) == sorted(["app9", "nsg1", "rt1", "sn-db", "sn-web", "vm2", "vnet1"])
    assert names({"include_removed": "true"}) == sorted(["app9", "nsg1", "rt1", "sn-db", "sn-web", "vm1", "vm2", "vnet1"])
    assert names({"q": "SN-"}) == ["sn-db", "sn-web"]
    assert names({"q": "%"}) == []  # a wildcard is a character, not a pattern
    assert names({"subscription": "dev"}) == ["app9"]
    assert names({"subscription": SUB2}) == ["app9"] and len(names({"subscription": store.tree()["subscriptions"][1]["id"]})) == 6
    assert names({"resource_group": "RG9"}) == ["app9"]
    assert names({"category": "web"}) == ["app9"]
    assert names({"type": "microsoft.web/sites"}) == ["app9"]
    assert names({"region": "UAENORTH", "category": "web"}) == ["app9"]
    assert names({"state": "running"}) == ["vm2"]
    assert names({"tag": "ENV"}) == ["app9", "vnet1"]
    assert names({"tag": "env=dev"}) == ["app9"] and names({"tag": "env=DEV"}) == []
    assert names({"without": "route_table"}) == ["sn-db"]
    assert names({"without": "security_group"}) == []
    assert names({"without": "tags"}) == sorted(["nsg1", "rt1", "sn-db", "sn-web", "vm2"])
    assert names({"changed_since": "last_pull"}) == ["vm2"]
    assert names({"changed_since": T[1].isoformat(), "include_removed": 1}) == ["vm1", "vm2"]
    assert names({"changed_since": "2026-09-30"}) == []
    assert names({"attention": "changed"}) == ["vm2"]
    assert names({"attention": "no_tags"}) == names({"without": "tags"})
    assert names({"attention": "stopped_failed", "include_removed": True}) == ["vm1"]
    assert names({"category": "networking", "tag": "env"}) == ["vnet1"]  # filters combine
    rows_, total = store.search({"category": "networking"}, 2, 1)
    assert (len(rows_), total) == (2, 5)
    assert rows_[0]["subscription"]["name"] == "Prod" and rows_[0]["label"]  # the resource JSON shape
    for bad in ({"without": "x"}, {"attention": "x"}, {"changed_since": "yesterday"}):
        with pytest.raises(store.BadFilter):
            store.search(bad)


def test_exposed_uses_the_exposure_rules(loaded, monkeypatch):
    mod = types.ModuleType("plugins._ea_azure.helpers.exposure")
    mod.reasons = lambda r, links: ["open_rule"] if r["name"] == "nsg1" else (["x"] if links and r["name"] == "sn-web" else [])
    monkeypatch.setitem(sys.modules, mod.__name__, mod)
    # `from package import module` reads the package attribute first, which the real module sets once
    # any earlier test has imported it; without this the stub is ignored and the test depends on order.
    import plugins._ea_azure.helpers as helpers_pkg
    monkeypatch.setattr(helpers_pkg, "exposure", mod, raising=False)
    assert names({"attention": "exposed"}) == ["nsg1", "sn-web"]
    assert store.counts({"attention": "exposed"}, "category") == ([{"key": "networking", "count": 2}], 2)


def test_counts_by(loaded):
    assert store.counts({}, "subscription") == ([{"key": "Prod", "count": 6}, {"key": "Dev", "count": 1}], 7)
    assert store.counts({"subscription": "Prod"}, "resource_group") == (
        [{"key": "rg1 (Prod)", "count": 5}, {"key": "rg2 (Prod)", "count": 1}], 6)
    assert store.counts({}, "category")[0][0] == {"key": "networking", "count": 5}
    assert store.counts({"include_removed": True}, "state")[0][0] == {"key": "ok", "count": 6}
    assert dict((g["key"], g["count"]) for g in store.counts({}, "type")[0])["microsoft.web/sites"] == 1
    assert store.counts({}, "region")[1] == 7
    for by in store.BY:
        store.counts({}, by)
    with pytest.raises(store.BadFilter):
        store.counts({}, "region; DROP TABLE cloud_resource")


def test_get_by_id_native_id_name_and_ambiguous(loaded):
    vnet = store.get("vnet1")
    assert store.get(vnet["id"])["name"] == "vnet1" and store.get(vnet["native_id"].upper())["id"] == vnet["id"]
    assert vnet["resource_group"]["name"] == "rg1" and vnet["subscription"]["name"] == "Prod"
    assert vnet["changes"] == [] and store.get("nope") is None and store.get("not-a-uuid/../") is None
    vm2 = store.get("vm2")
    assert vm2["changes"][0]["change"] == "changed" and vm2["changes"][0]["fields"] == {"state": {"old": "failed", "new": "running"}}
    assert store.get("vm1") is None  # removed: not found by name
    pull(loaded, [res("twin"), res("twin", rg="rg2")], 3)
    with pytest.raises(store.Ambiguous) as e:
        store.get("TWIN")
    assert [c["resource_group"] for c in e.value.candidates] == ["rg1", "rg2"]


def test_injection_shaped_values_are_bound(loaded):
    evil = "'; DROP TABLE cloud_resource; --"
    for key in ("q", "subscription", "resource_group", "category", "type", "region", "state", "tag"):
        assert store.search({key: evil})[1] == 0
    assert store.search({"tag": f"{evil}={evil}"})[1] == 0
    assert store.get(evil) is None and store.get("/" + evil) is None
    assert store.changes(run_id=evil)[1] == 0
    assert store.search({})[1] == 7


def test_history_and_runs(loaded):
    last = store.as_of()["last_pull"]
    assert store.runs(1)[0]["id"] == last["id"]
    got, total = store.changes()
    assert total == 2 and {c["change"] for c in got} == {"changed", "removed"}
    one, total = store.changes(run_id=got[0]["run_id"])
    assert total == 2 and {c["resource"]["name"] for c in one} == {"vm1", "vm2"}
    assert store.changes(since="2026-09-03")[1] == 2 and store.changes(since="2026-09-04")[1] == 0
    assert store.has_data() and store.as_of()["subscriptions"][0]["name"] == "Dev"
