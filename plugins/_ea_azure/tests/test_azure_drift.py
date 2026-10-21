"""The `drifted` attention: change lines on a resource the product made that no apply explains, until an engineer accepts
them. Pulls and applies are real rows in the order they happen; nothing reaches Azure. Needs PostgreSQL."""

import datetime as dt

import pytest

from helpers import db
from plugins._ea_azure.helpers import store
from plugins._ea_azure.tests import azure_read_tenant as t

PLAN = "microsoft.web/serverfarms"
OWN = "microsoft.network/virtualnetworks"


def now():
    return dt.datetime.now(dt.timezone.utc)


def rows(vnet_tags=None, plan_sku="P1v3", extra=()):
    return [t.net("vnet-own", OWN, tags=vnet_tags or {"env": "prod"}),
            t.res("plan-other", PLAN, "rg-app", category="web", sku={"name": plan_sku}), *extra]


@pytest.fixture
def world(database):
    acct = store.account("Tenant-1", "Contoso")
    store.apply_containers(acct, [{"id": t.SUB, "name": "Prod", "state": "Enabled"}], t.groups(t.SUB, ("rg-net", "rg-app")), now())
    t.pull(acct, t.SUB, rows(), now())  # the baseline: no lines
    return acct


def apply(native_id, change_id="chg1"):
    """What `records.record_managed` writes after an apply."""
    with db.transaction(db.NO_SCOPE) as conn:
        conn.execute("INSERT INTO cloud_managed (native_id, workspace, change_id) VALUES (%s, 'ws', %s) "
                     "ON CONFLICT (native_id) DO UPDATE SET change_id = EXCLUDED.change_id, applied_at = now()", (native_id, change_id))


def accept(native_id, through):
    with db.transaction(db.NO_SCOPE) as conn:
        conn.execute("UPDATE cloud_managed SET accepted_at = %s WHERE native_id = %s", (through, native_id))


VNET = t.net("vnet-own", OWN)["native_id"]


def names(**f):
    return {r["name"] for r in store.search({"attention": "drifted", **f}, 100)[0]}


def test_nothing_is_drifted_before_anything_is_managed_and_an_unmanaged_change_is_not_drift(world):
    t.pull(world, t.SUB, rows(vnet_tags={"env": "dev"}, plan_sku="P2v3"), now())
    assert store.search({"attention": "changed"}, 100)[1] == 2 and store.search({"attention": "drifted"})[1] == 0


def test_the_pull_after_an_apply_is_explained_and_a_later_outside_change_is_drift_until_accepted(world):
    apply(VNET)
    t.pull(world, t.SUB, rows(vnet_tags={"env": "prod", "owner": "netops"}), now())  # the pull the apply asked for
    assert names() == set()
    t.pull(world, t.SUB, rows(vnet_tags={"env": "test", "owner": "netops"}, plan_sku="P2v3"), now())  # somebody, in the portal
    assert names() == {"vnet-own"}  # plan-other changed too, but the product did not make it
    [found] = store.drifted()
    assert found["native_id"] == VNET and found["workspace"] == "ws" and found["change_id"] == "chg1"
    assert found["lines"] == [f"{found['through'][:10]}: tags.env was prod, now test"]
    assert store.counts({"attention": "drifted"}, "state")[1] == 1
    accept(VNET, found["through"])
    assert names() == set() and store.drifted() == []
    t.pull(world, t.SUB, rows(vnet_tags={"env": "test"}), now())  # a new change after the acceptance
    [again] = store.drifted()
    assert again["lines"] == [f"{again['through'][:10]}: tags.owner was netops, now null"] and again["through"] > found["through"]


def test_a_later_apply_settles_what_came_before_it_and_explains_only_its_own_pull(world):
    apply(VNET)
    t.pull(world, t.SUB, rows(), now())
    t.pull(world, t.SUB, rows(vnet_tags={"env": "test"}), now())
    assert names() == {"vnet-own"}
    apply(VNET, "chg2")  # the product wrote the resource again, so what was seen before it is history
    assert names() == set()
    t.pull(world, t.SUB, rows(vnet_tags={"env": "test", "a": "b"}), now())  # explained by chg2
    t.pull(world, t.SUB, rows(vnet_tags={"env": "test", "a": "c"}), now())  # not explained
    [found] = store.drifted()
    assert [line[12:] for line in found["lines"]] == ["tags.a was b, now c"] and found["change_id"] == "chg2"


def test_a_managed_resource_that_vanished_is_drift_and_is_listed_though_it_is_removed(world):
    apply(VNET)
    t.pull(world, t.SUB, rows(), now())
    t.pull(world, t.SUB, [r for r in rows() if r["name"] != "vnet-own"], now())
    assert names() == {"vnet-own"} and store.search({"attention": "drifted"}, 100)[0][0]["removed_at"]
    assert store.drifted()[0]["lines"][-1].endswith("no longer in Azure")
    assert store.search({"q": "vnet-own"}, 100)[1] == 0  # an ordinary search still hides it


def test_the_tiles_count_it(world):
    from plugins._ea_azure.helpers import store as s

    apply(VNET)
    t.pull(world, t.SUB, rows(), now())
    t.pull(world, t.SUB, rows(vnet_tags={"env": "x"}), now())
    assert {k: s.counts({"attention": k}, "state")[1] for k in ("drifted", "changed")} == {"drifted": 1, "changed": 1}


def test_the_chat_tool_lists_the_drift_with_its_dated_lines_and_the_note_that_nothing_is_put_back(world):
    import json

    from plugins._ea_azure.tools import azure_inventory

    apply(VNET)
    t.pull(world, t.SUB, rows(), now())
    t.pull(world, t.SUB, rows(vnet_tags={"env": "test"}), now())
    head, body = azure_inventory.answer({"attention": "drifted"}).split("\n\n", 1)
    out = json.loads(body)
    assert head.startswith("Azure inventory as of") and out["total"] == 1
    [one] = out["resources"]
    assert one["name"] == "vnet-own" and one["last_applied_by_change"] == "chg1" and one["changes"][0].endswith("tags.env was prod, now test")
    assert "Nothing is put back" in out["note"] and "drift_accept" in out["note"]
