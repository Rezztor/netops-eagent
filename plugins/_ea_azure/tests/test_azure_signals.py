"""Azure's own findings (Advisor, policy, change records, health): read in the same pull, stored beside the resources,
shown on the resource page and answered by the inventory tool. Against the stand-in Azure; needs PostgreSQL."""

import asyncio
import json

from helpers import db
from plugins._ea_azure.api.azure_resource import AzureResource
from plugins._ea_azure.helpers import collect, signals, store
from plugins._ea_azure.tests.test_azure_collect import pull, sql, world  # noqa: F401  (the fixture and its helpers)
from plugins._ea_azure.tests.test_azure_inventory_tool import ask


def vm2():
    return store.get("vm-prod-2")["native_id"]


def stored():
    return sql("SELECT count(*) FROM cloud_signal WHERE target_native_id <> ''")[0][0]


def test_a_pull_stores_what_azure_says_beside_the_resources_and_counts_it(world):
    assert signals.of("/anything")["read_at"] is None            # never read is not the same as nothing reported
    c = pull("screen")["counts"]
    assert c["signals"] == {"advisor": 3, "policy": 1, "change": 2, "health": 3} and c["signals_capped"] == []
    assert (c["added"], c["changed"]) == (0, 0)
    found = signals.of(vm2())
    assert found["read_at"] and [a["impact"] for a in found["advisor"]] == ["High"]
    assert [h.get("state") or h["annotation"] for h in found["health"]] == ["Unavailable", "VirtualMachineHostRebootedForRepair"]
    assert found["change"][0]["changed_by_type"] == "ServicePrincipal" and found["policy"] == []
    quiet = signals.of(store.get("vnet-prod-hub")["native_id"])
    assert quiet["read_at"] and not any(quiet[k] for k in signals.KINDS)    # read, and Azure had nothing to say
    assert stored() == 3 + 1 + 2 + 3


def test_nothing_planted_in_azures_own_tables_is_stored(world):
    pull()
    dump = " ".join(r[0] for r in sql("SELECT row_to_json(t)::text FROM cloud_signal t"))
    assert "PLANTED" not in dump and "alice@example.com" in dump and "[masked]" in dump


def test_the_next_pull_replaces_the_rows_and_a_moving_recommendation_is_not_a_change(world):
    pull()
    world.tenant["advisor"].pop(0)
    world.tenant["health"][1]["properties"]["availabilityState"] = "Available"
    c = pull()["counts"]
    assert stored() == 2 + 1 + 2 + 3 and (c["changed"], c["added"], c["removed"]) == (0, 0, 0)
    assert signals.of(vm2())["advisor"] == [] and signals.of(vm2())["health"][0]["state"] == "Available"
    assert sql("SELECT count(*) FROM cloud_change") == [(0,)]


def test_a_failed_read_of_one_table_fails_the_subscription_and_keeps_the_old_findings(world):
    pull()
    world.fail_kind = ("advisor", 500)
    out = pull()
    assert out["status"] == "failed" and out["counts"]["subscriptions"]["failed"] == 3
    assert stored() == 9                                         # as last seen
    world.fail_kind = None
    assert pull()["status"] == "succeeded"


def test_more_rows_than_the_cap_keep_the_cap_and_say_so(world, monkeypatch):
    monkeypatch.setattr(collect, "_SIGNAL_CAP", 1)
    c = pull()["counts"]
    assert c["signals"]["advisor"] == 1 and c["signals"]["change"] == 1 and {"advisor", "change"} <= set(c["signals_capped"])
    assert c["signals"]["health"] == 1 and c["signals"]["policy"] == 1


def test_search_orders_advisor_by_impact_and_lists_unhealthy_only(world):
    pull()
    rows, total = signals.search("advisor", {})
    assert total == 3 and [r["impact"] for r in rows] == ["High", "Medium", "Low"] and rows[0]["resource"] == "vm-prod-2"
    assert rows[2]["resource"] == "11111111-1111-4111-8111-111111111111"          # a subscription-level one names its id
    assert signals.search("advisor", {"subscription": "Non-production"})[1] == 0
    assert signals.search("advisor", {"q": "stprod"})[1] == 1 and signals.search("advisor", {"resource_group": "rg-web"})[1] == 1
    health, n = signals.search("health", {})
    assert n == 2 and {r.get("state") for r in health} == {"Unavailable", None}
    assert signals.search("azure_changes", {})[0][0]["resource"] == "app-portal-prod" and signals.read_at("advisor")
    assert signals.read_at("policy") and signals.read_at("azure_changes")


def test_the_resource_page_carries_them(world):
    pull()
    out = asyncio.new_event_loop().run_until_complete(AzureResource(app=None, thread_lock=None).process({"id": "vm-prod-2"}, None))
    assert out["signals"]["advisor"][0]["target_sku"] == "Standard_D2s_v5" and out["signals"]["read_at"]
    assert "signals" not in out["resource"]


def test_the_tool_answers_per_resource_and_across_the_estate(world):
    pull()
    one = ask(resource="vm-prod-2").split("\n\n", 1)[1]
    says = json.loads(one)["azure_says"]
    assert says["advisor"][0]["recommendation"] == "RightSizing" and says["health"][0]["state"] == "Unavailable"
    assert says["azure_change_records"][0]["changes"][0]["new"] == "Standard_D4s_v5" and says["read_at"]
    listed = json.loads(ask(show="advisor").split("\n\n", 1)[1])
    assert listed["total"] == 3 and listed["findings"][0]["impact"] == "High" and "retail rates" in listed["note"]
    assert "target" not in listed["findings"][0]
    assert json.loads(ask(show="health", query="vm-prod").split("\n\n", 1)[1])["total"] == 2
    assert ask(show="nonsense").startswith("show must be one of advisor, policy, health, azure_changes")


def test_a_pull_from_before_the_table_existed_says_not_read_yet(world):
    pull()
    with db.transaction(db.NO_SCOPE) as conn:
        conn.execute("DELETE FROM cloud_signal")
    assert "have not been read yet" in ask(show="advisor")
    assert "have not been read" in json.loads(ask(resource="vm-prod-2").split("\n\n", 1)[1])["azure_says"]["read"]


def test_a_backup_round_trip_keeps_them(world):
    pull()
    dump = db.dump_data()
    db.load_data(dump)
    assert db.dump_data()["tables"]["cloud_signal"]["rows"] == dump["tables"]["cloud_signal"]["rows"] and stored() == 9
