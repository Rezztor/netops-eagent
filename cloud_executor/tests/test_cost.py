"""The monthly cost of what a plan creates, and the ceilings (D9). Prices come from a stand-in lookup."""
import pytest

from cloud_executor import cost, rails
from cloud_executor.tests.support import plan_fixture

RAILS = rails.defaults()


def vm_plan():
    return plan_fixture("linux_vm")


def test_a_resource_group_is_free_and_a_usage_resource_has_no_fixed_price():
    assert cost.estimate(plan_fixture("resource_group"), lambda *a, **k: None) == {
        "monthly_usd": 0.0, "items": [], "unknown": [], "priced_at": cost.estimate({})["priced_at"]}
    est = cost.estimate(plan_fixture("storage_account"), lambda *a, **k: None)
    assert est["monthly_usd"] == 0 and est["items"][0]["note"].startswith("billed by use") and est["unknown"] == []


def test_a_vm_is_priced_from_its_size_and_unknown_when_the_price_list_cannot_say():
    asked = []

    def lookup(region, service, sku, meter="", os=""):
        asked.append((region, service, sku, os))
        return {"usd_month": 140.16}

    est = cost.estimate(vm_plan(), lookup)
    assert est["monthly_usd"] == 140.16 and est["unknown"] == []
    assert asked and asked[0][1] == "Virtual Machines" and asked[0][2] == "B2s"
    unknown = cost.estimate(vm_plan(), lambda *a, **k: None)
    assert unknown["monthly_usd"] == 0 and unknown["unknown"]
    raising = cost.estimate(vm_plan(), lambda *a, **k: 1 / 0)
    assert raising["unknown"] == unknown["unknown"]


def test_a_type_nobody_priced_is_unknown_not_free():
    plan = plan_fixture("resource_group")
    plan["resource_changes"][0]["type"] = "azurerm_mystery"
    assert cost.estimate(plan, lambda *a, **k: None)["unknown"] == ["azurerm_resource_group.this"]


def test_an_update_is_not_priced():
    assert cost.estimate(plan_fixture("resource_group", update=True), lambda *a, **k: 1 / 0)["items"] == []


@pytest.mark.parametrize("monthly, unknown, committed, allow, refused", [
    (10, [], 0, False, False), (251, [], 0, False, True), (250, [], 0, False, False), (10, ["a"], 0, False, True),
    (10, ["a"], 0, True, False), (100, [], 950, False, True), (100, [], 900, False, False),
])
def test_the_ceilings(monthly, unknown, committed, allow, refused):
    checks, refusals = cost.policy({"monthly_usd": monthly, "unknown": unknown}, {**RAILS, "allow_unknown_price": allow}, committed)
    assert bool(refusals) is refused and checks[0]["ok"] is (not refused)
