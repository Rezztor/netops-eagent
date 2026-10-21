"""Cost Management reads against the stand-in (fake_azure.py): what is asked, what is said, the four-hour cache, the
throttling headers, scope completeness, the forecast wording, budgets without their contacts, the link from a waste
finding, and the proof that the module has no other address. Needs PostgreSQL. No test reaches Microsoft; every
response shape here is the plugin's reading of Microsoft's reference, not a recording."""

import asyncio
import datetime as dt
import json
import re
from pathlib import Path

import pytest

from helpers import api_policy, db, rbac
from plugins._ea_azure.api.azure_cost import AzureCost
from plugins._ea_azure.helpers import client, config, cost, credential, store
from plugins._ea_azure.tests import azure_read_tenant as t
from plugins._ea_azure.tests.fake_azure import CLIENT_ID, TENANT_ID, FakeAzure, hand_tenant
from plugins._ea_azure.tests.fixtures.env import install
from plugins._ea_azure.tools import azure_cost

NOW = dt.datetime(2026, 10, 15, 12, 0, tzinfo=dt.timezone.utc)
VM1 = "/subscriptions/{}/resourcegroups/rg-vm/providers/microsoft.compute/virtualmachines/vm1".format(t.SUB)
APP1 = "/subscriptions/{}/resourcegroups/rg-app/providers/microsoft.web/sites/app1".format(t.SUB)
STOR = "/subscriptions/{}/resourcegroups/rg-app/providers/microsoft.storage/storageaccounts/stor1".format(t.SUB)
VM3 = "/subscriptions/{}/resourcegroups/rg-dev/providers/microsoft.compute/virtualmachines/vm3".format(t.SUB2)
PROD_LAST_MONTH, DEV_LAST_MONTH = 100 + 50 + 30 + 5, 20


def line(sub, rg, service, resource, day, amount, currency="USD", **tags):
    return {"sub": sub, "rg": rg, "service": service, "resource": resource, "date": dt.date.fromisoformat(day),
            "cost": amount, "currency": currency, "tags": tags}


LINES = [
    line(t.SUB, "rg-vm", "Virtual Machines", VM1, "2026-09-10", 100.0, owner="netops"),
    line(t.SUB, "rg-vm", "Virtual Machines", VM1, "2026-09-20", 50.0, owner="netops"),
    line(t.SUB, "rg-vm", "Virtual Machines", VM1, "2026-10-05", 40.0, owner="netops"),
    line(t.SUB, "rg-app", "Azure App Service", APP1, "2026-09-12", 30.0),
    line(t.SUB, "rg-app", "Storage", STOR, "2026-09-15", 5.0, owner="netops"),
    line(t.SUB2, "rg-dev", "Virtual Machines", VM3, "2026-09-11", 20.0, owner="dev"),
]


@pytest.fixture
def azure(database, monkeypatch, tmp_path):
    install(monkeypatch, tmp_path)
    config.save({"tenant_id": TENANT_ID, "client_id": CLIENT_ID})
    credential.set_secret("fake-client-secret", "")
    t.load()  # Prod and Dev are stored and readable
    client.reset()
    monkeypatch.setattr(cost, "_now", lambda: NOW)
    monkeypatch.setattr(cost, "_blocked_until", 0.0)
    cost._recent.clear()
    with FakeAzure(hand_tenant()) as fake:
        monkeypatch.setattr(client, "TEST_ENDPOINTS", fake.cloud)
        fake.cost_lines = list(LINES)
        fake.agreements = ["MicrosoftCustomerAgreement"]
        yield fake
    client.reset()


def sent(fake, leaf="query"):
    return [r for r in fake.received if r["path"].endswith(f"/providers/Microsoft.CostManagement/{leaf}")]


def names(out):
    return {r["name"]: r["cost"] for r in out["rows"]}


def test_spend_by_service_adds_the_subscriptions_and_says_period_currency_lateness_and_scope(azure):
    out = cost.ask("spend", timeframe="last_month", group_by="service")
    assert names(out) == {"Virtual Machines": 170.0, "Azure App Service": 30.0, "Storage": 5.0}
    assert [r["name"] for r in out["rows"]] == ["Virtual Machines", "Azure App Service", "Storage"]  # biggest first
    assert out["totals"] == [{"currency": "USD", "cost": PROD_LAST_MONTH + DEV_LAST_MONTH}]
    assert out["period"] == {"from": "2026-09-01", "to": "2026-09-30", "timezone": "UTC"}
    assert out["scope"]["complete"] and out["scope"]["covers"] == "2 of 2 stored subscriptions"
    assert out["agreement"] == "MicrosoftCustomerAgreement"
    notes = " ".join(out["notes"])
    for must in ("72 hours", "free trial", "credits", "estimate", "do not add"):
        assert must in notes
    assert "2026-09-01 to 2026-09-30 (UTC)" in out["headline"] and "USD" in out["headline"] and "before tax" in out["headline"]
    first = json.loads(sent(azure)[0]["raw"])  # what was asked: actual cost, a custom period in dates, the service dimension
    assert first["type"] == "ActualCost" and first["timeframe"] == "Custom"
    assert first["timePeriod"] == {"from": "2026-09-01T00:00:00Z", "to": "2026-09-30T23:59:59Z"}
    assert first["dataset"]["grouping"] == [{"type": "Dimension", "name": "ServiceName"}]
    assert sent(azure)[0]["auth"].startswith("Bearer fake-token-") and cost.API == "2026-06-01"


def test_month_to_date_and_the_three_month_window(azure):
    mtd = cost.ask("spend", group_by="none")
    assert mtd["period"]["from"] == "2026-10-01" and mtd["period"]["to"] == "2026-10-15" and mtd["totals"][0]["cost"] == 40.0
    three = cost.ask("spend", timeframe="last_3_months", group_by="none")
    assert three["period"]["from"] == "2026-07-01" and three["period"]["to"] == "2026-09-30"
    assert cost.period("last_month", dt.datetime(2026, 1, 3, tzinfo=dt.timezone.utc))[0] == dt.date(2025, 12, 1)


def test_the_other_groupings_and_the_latest_usage_date(azure):
    by = lambda g, **kw: cost.ask("spend", timeframe="last_month", group_by=g, **kw)  # noqa: E731
    assert names(by("resource_group")) == {"rg-vm": 150.0, "rg-app": 35.0, "rg-dev": 20.0}
    assert names(by("resource"))[VM1] == 150.0
    assert names(by("tag:owner")) == {"netops": 155.0, "dev": 20.0, "(no tag)": 30.0}
    days = by("day")
    assert days["latest_usage_date"] == "2026-09-20" and days["rows"][0]["name"] == "2026-09-10"
    assert json.loads(sent(azure)[-1]["raw"])["dataset"]["granularity"] == "Daily"
    assert by("none")["rows"][0]["name"] == "total"


def test_bad_arguments_are_refused_before_anything_is_sent(azure):
    for kw in ({"group_by": "owner"}, {"group_by": "tag:a b|c"}, {"timeframe": "forever"}, {"scope": "Nowhere"},
               {"resource_group": "rg/../x"}, {"resource_group": "rg'x"}):
        with pytest.raises(cost.Refused):
            cost.ask("spend", **kw)
    with pytest.raises(cost.Refused):
        cost.ask("delete")
    with pytest.raises(cost.Refused):
        cost.ask("item")
    assert not [r for r in azure.received if "CostManagement" in r["path"]]


def test_a_second_question_within_four_hours_comes_from_the_database_and_a_later_one_asks_again(azure):
    first = cost.ask("spend", timeframe="last_month")
    again = cost.ask("spend", timeframe="last_month")
    assert len(sent(azure)) == 2 and first["scope"]["included"][0]["data"] == "live"  # one per subscription, once
    assert again["scope"]["included"][0]["data"] == "cache" and again["totals"] == first["totals"]
    cost.ask("spend", timeframe="last_month", group_by="resource_group")
    assert len(sent(azure)) == 4  # a different shape is a different question
    with db.transaction(db.NO_SCOPE) as conn:
        conn.execute("UPDATE cloud_cost_cache SET fetched_at = %s", (NOW - dt.timedelta(hours=5),))
    cost.ask("spend", timeframe="last_month")
    assert len(sent(azure)) == 6


def test_billing_is_read_once_a_day_and_an_unseen_agreement_is_said_so(azure):
    azure.agreements = []
    azure.billing_status = 403
    out = cost.ask("spend", timeframe="last_month")
    cost.ask("spend", timeframe="last_month", group_by="resource_group")
    assert out["agreement"] == "not visible to this application"
    assert len([r for r in azure.received if r["path"].endswith("/billingAccounts")]) == 1  # the "not visible" is kept too
    azure.agreements, azure.billing_status = ["MicrosoftPartnerAgreement"], 200
    with db.transaction(db.NO_SCOPE) as conn:
        conn.execute("DELETE FROM cloud_cost_cache WHERE key = 'agreement'")
    csp = cost.ask("spend", timeframe="last_month")
    assert "partner" in " ".join(csp["notes"]) and csp["agreement"] == "MicrosoftPartnerAgreement"


def test_one_subscription_or_one_resource_group_is_one_scope(azure):
    prod = cost.ask("spend", scope="Prod", timeframe="last_month")
    assert prod["totals"][0]["cost"] == PROD_LAST_MONTH and prod["scope"]["covers"] == "1 of 2 stored subscriptions"
    assert prod["scope"]["complete"] and {r["path"] for r in sent(azure)} == {
        f"/subscriptions/{t.SUB}/providers/Microsoft.CostManagement/query"}
    group = cost.ask("spend", scope=t.SUB, resource_group="RG-APP", timeframe="last_month")
    assert group["totals"][0]["cost"] == 35.0 and "resource group RG-APP only" in group["scope"]["covers"]
    assert sent(azure)[-1]["path"] == f"/subscriptions/{t.SUB}/resourceGroups/RG-APP/providers/Microsoft.CostManagement/query"


def test_a_subscription_that_cannot_be_asked_makes_the_scope_incomplete_and_says_why(azure):
    store.apply_containers(store.account("Tenant-1"), [{"id": t.SUB, "name": "Prod", "state": "Enabled"}], t.groups(t.SUB, ("rg-vm",)), NOW)
    out = cost.ask("spend", timeframe="last_month")  # Dev is no longer readable
    assert not out["scope"]["complete"] and out["scope"]["not_included"] == [{"subscription": "Dev", "reason": "it was not readable at the last pull"}]
    assert "NOT the whole estate" in out["headline"] and out["totals"][0]["cost"] == PROD_LAST_MONTH


def test_a_failed_subscription_is_listed_and_a_total_failure_is_a_sentence(azure):
    azure.cost_fail["query"] = 403
    with pytest.raises(cost.CostUnavailable) as e:
        cost.ask("spend", timeframe="last_month")
    assert "does not allow reading cost data" in str(e.value) and "Simulated" not in str(e.value)
    azure.cost_fail["query"] = 400
    with pytest.raises(cost.CostUnavailable, match="no cost data for this scope"):
        cost.ask("spend", timeframe="last_month")


def test_a_throttled_read_pauses_every_live_read_until_azure_says_go(azure, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(cost, "_clock", lambda: now[0])
    azure.cost_throttle = 1
    with pytest.raises(cost.CostUnavailable, match="limiting"):
        cost.ask("spend", scope="Prod", timeframe="last_month")
    calls = len(azure.received)
    with pytest.raises(cost.CostUnavailable, match="asked this product to wait about 30 seconds"):
        cost.ask("spend", scope="Prod", timeframe="last_month")
    assert len(azure.received) == calls  # not even a sign-in: it did not ask
    now[0] += 31
    assert cost.ask("spend", scope="Prod", timeframe="last_month")["scope"]["included"][0]["data"] == "live"


def test_an_empty_quota_header_pauses_the_next_read(azure, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(cost, "_clock", lambda: now[0])
    azure.cost_quota = "0"
    cost.ask("spend", scope="Prod", timeframe="last_month")
    with pytest.raises(cost.CostUnavailable, match="wait"):
        cost.ask("spend", scope="Prod", timeframe="last_month", group_by="resource")
    now[0] += 11
    azure.cost_quota = "11"
    assert cost.ask("spend", scope="Prod", timeframe="last_month", group_by="resource")["rows"]


def test_when_azure_cannot_be_asked_the_old_answer_is_served_and_marked(azure):
    cost.ask("spend", scope="Prod", timeframe="last_month")
    with db.transaction(db.NO_SCOPE) as conn:
        conn.execute("UPDATE cloud_cost_cache SET fetched_at = %s", (NOW - dt.timedelta(hours=9),))
    azure.cost_fail["query"] = 500
    out = cost.ask("spend", scope="Prod", timeframe="last_month")
    assert out["scope"]["included"][0]["data"].startswith("stale") and out["totals"][0]["cost"] == PROD_LAST_MONTH
    assert out["fetched_at"] == (NOW - dt.timedelta(hours=9)).isoformat(timespec="seconds")


def test_this_product_stops_at_thirty_live_reads_an_hour(azure, monkeypatch):
    monkeypatch.setattr(cost, "_clock", lambda: 5000.0)
    cost._recent.extend([4999.0] * cost.LIVE_PER_HOUR)
    with pytest.raises(cost.CostUnavailable, match="already made 30 cost reads"):
        cost.ask("spend", scope="Prod", timeframe="last_month")
    assert not sent(azure)


def test_pages_are_followed_on_the_same_host_only_and_a_cut_off_answer_is_not_complete(azure):
    azure.cost_page = 1
    out = cost.ask("spend", scope="Prod", timeframe="last_month", group_by="resource")
    assert names(out) == {VM1: 150.0, APP1: 30.0, STOR: 5.0} and len(sent(azure)) == 3 and out["scope"]["complete"]
    azure.cost_lines += [line(t.SUB, "rg-x", "Storage", f"/subscriptions/{t.SUB}/resourcegroups/rg-x/providers/x/y/r{n}", "2026-09-01", 1.0)
                         for n in range(6)]
    big = cost.ask("spend", scope="Prod", timeframe="last_3_months", group_by="resource")
    assert not big["scope"]["complete"] and "more rows than this plugin reads" in big["scope"]["covers"]
    azure.cost_evil_next_link = True
    with pytest.raises(cost.CostUnavailable, match="another address"):
        cost.ask("spend", scope="Prod", timeframe="last_month", group_by="resource_group")


def test_the_forecast_is_labelled_a_projection_with_its_history_and_never_a_fact(azure):
    azure.forecast_days = {t.SUB: [(dt.date(2026, 10, d), 10.0) for d in range(16, 32)]}
    out = cost.ask("forecast", scope="Prod")
    assert out["totals"] == [{"currency": "USD", "actual_to_date": 40.0, "forecast_rest_of_month": 160.0, "projected_month_total": 200.0}]
    assert out["kind"].startswith("FORECAST") and out["headline"].startswith("FORECAST") and "not a charge" in out["headline"]
    assert out["history_days"] == 5 and out["weak"] is True
    assert "weak" in " ".join(out["notes"]) and "90 days" in " ".join(out["notes"])
    body = json.loads(sent(azure, "forecast")[0]["raw"])
    assert body["includeActualCost"] is True and body["timePeriod"]["from"].startswith("2026-07-17")
    assert body["timePeriod"]["to"].startswith("2026-10-31")
    both = cost.ask("forecast")  # Dev has no forecast
    assert not both["scope"]["complete"] and "no forecast" in both["scope"]["not_included"][0]["reason"]


def test_no_forecast_at_all_is_said_not_invented(azure):
    azure.cost_lines = []
    with pytest.raises(cost.CostUnavailable, match="no forecast"):
        cost.ask("forecast", scope="Prod")


def test_budgets_and_alerts_are_cut_to_figures_and_the_contacts_are_dropped(azure):
    azure.budgets[t.SUB] = [{"name": "monthly", "properties": {
        "amount": 500, "timeGrain": "Monthly", "category": "Cost", "timePeriod": {"startDate": "2026-10-01T00:00:00Z", "endDate": "2030-01-01T00:00:00Z"},
        "currentSpend": {"amount": 120.5, "unit": "USD"},
        "notifications": {"n1": {"enabled": True, "operator": "GreaterThan", "threshold": 80, "thresholdType": "Actual",
                                 "contactEmails": ["secret.person@example.com"], "contactGroups": ["/subscriptions/x/ag"]}}}}]
    azure.cost_alerts[t.SUB] = [{"name": "a1", "properties": {
        "definition": {"type": "Budget", "category": "Cost"}, "status": "Active", "creationTime": "2026-10-10T00:00:00Z",
        "description": "Budget alert", "statusModificationUserName": "someone@example.com",
        "details": {"threshold": 0.8, "operator": "GreaterThan", "amount": 500, "unit": "USD", "currentSpend": 410,
                    "contactEmails": ["secret.person@example.com"]}}}]
    out = cost.ask("budgets")
    text = json.dumps(out)
    assert "@" not in text and "contactGroups" not in text and "someone" not in text
    prod = next(s for s in out["subscriptions"] if s["subscription"] == "Prod")
    assert prod["budgets"][0]["amount"] == 500 and prod["budgets"][0]["current_spend"] == 120.5 and prod["budgets"][0]["currency"] == "USD"
    assert prod["budgets"][0]["notifications"] == [{"operator": "GreaterThan", "threshold": 80, "threshold_type": "Actual", "enabled": True}]
    assert prod["alerts"][0]["status"] == "Active" and prod["alerts"][0]["current_spend"] == 410
    assert out["without_budget"] == ["Dev"] and "never stops spending" in " ".join(out["notes"])


def test_what_one_resource_cost_is_found_through_the_inventory_and_feeds_the_waste_link(azure):
    out = cost.ask("item", resource="vm1")
    assert out["question"] == "item" and out["resource"] == "vm1" and out["rows"] == [{"name": VM1, "cost": 150.0, "currency": "USD"}]
    assert sent(azure)[0]["path"].startswith(f"/subscriptions/{t.SUB}/")
    assert json.loads(sent(azure)[0]["raw"])["dataset"]["filter"] == {"dimensions": {"name": "ResourceId", "operator": "In", "values": [VM1]}}
    calls = len(azure.received)
    findings = [{"native_id": VM1, "name": "vm1"}, {"native_id": APP1, "name": "app1"}, {"native_id": "", "name": "?"}]
    cost.annotate(findings)
    assert findings[0]["cost_last_month"]["cost"] == 150.0 and findings[0]["cost_last_month"]["currency"] == "USD"
    assert "2026-09-01 to 2026-09-30" in findings[0]["cost_last_month"]["period"] and "credits not deducted" in findings[0]["cost_last_month"]["period"]
    assert "cost_last_month" not in findings[1] and "cost_last_month" not in findings[2]
    assert len(azure.received) == calls  # reading the cache never asks Azure
    with pytest.raises(cost.Refused, match="No stored resource"):
        cost.ask("item", resource="nothing-by-this-name")


def test_the_waste_list_carries_last_months_cost_when_it_is_kept_and_asks_nobody(azure):
    from plugins._ea_azure.helpers import waste

    cost.ask("item", resource="vm1")
    calls = len(azure.received)
    out = waste.priced([{"native_id": VM1, "region": "uaenorth"}, {"native_id": APP1, "region": "uaenorth"}])
    assert out["findings"][0]["cost_last_month"]["cost"] == 150.0 and "cost_last_month" not in out["findings"][1]
    assert len(azure.received) == calls


def test_a_whole_estate_resource_answer_also_feeds_the_waste_link(azure):
    cost.ask("spend", timeframe="last_month", group_by="resource")
    found = [{"native_id": APP1}, {"native_id": VM3}]
    cost.annotate(found)
    assert found[0]["cost_last_month"]["cost"] == 30.0 and found[1]["cost_last_month"]["cost"] == 20.0


def test_a_cost_row_that_looks_like_a_secret_is_redacted_before_it_leaves(azure):
    azure.cost_lines.append(line(t.SUB, "rg-app", "Storage", STOR, "2026-09-16", 1.0, owner="Password=abc123secret"))
    out = cost.ask("spend", scope="Prod", timeframe="last_month", group_by="tag:owner")
    assert "abc123secret" not in json.dumps(out)


def test_the_tool_answers_with_the_headline_first_and_a_sentence_for_a_refusal(azure):
    said = azure_cost.answer({"question": "spend", "timeframe": "last_month"})
    assert said.startswith("Azure cost 2026-09-01 to 2026-09-30 (UTC)") and '"totals"' in said and "headline" not in said.split("\n\n", 1)[1]
    assert azure_cost.answer({"question": "spend", "group_by": "nope"}).startswith("group_by must be")
    azure.cost_fail["query"] = 403
    azure.cost_lines = []
    with db.transaction(db.NO_SCOPE) as conn:
        conn.execute("DELETE FROM cloud_cost_cache")
    assert azure_cost.answer({"question": "spend", "timeframe": "last_month"}).startswith("Azure cost data is not available:")


def test_the_endpoint_is_gated_and_gives_the_tools_answer(azure, monkeypatch):
    assert api_policy.required_permission_for("plugins/_ea_azure/azure_cost") == rbac.PERM_INVENTORY_VIEW
    call = lambda **i: asyncio.new_event_loop().run_until_complete(AzureCost(app=None, thread_lock=None).process(i, None))  # noqa: E731
    monkeypatch.setattr(config, "is_enabled", lambda: False)
    assert call(question="spend")["ok"] is False
    monkeypatch.setattr(config, "is_enabled", lambda: True)
    out = call(question="spend", timeframe="last_month", group_by="service")
    assert out["ok"] and out["totals"][0]["cost"] == PROD_LAST_MONTH + DEV_LAST_MONTH and out["scope"]["complete"]
    assert call(question="spend", timeframe="eons")["ok"] is False


# --- the proof that this module reads and has no other address -----------------------------------------------------------

def test_cost_py_has_two_verbs_four_leaves_one_billing_read_and_no_address_of_its_own():
    source = (Path(cost.__file__)).read_text()
    assert sorted(set(re.findall(r"\brequests\.(\w+)\(", source))) == ["get", "post"]
    assert not re.search(r"\b(put|patch|delete|head|options|request|Session)\b\s*\(", source.replace("def ", ""), re.I)
    assert not re.search(r"""["'](PUT|PATCH|DELETE)["']""", source) and "allow_redirects=True" not in source and "verify=" not in source
    assert source.count("allow_redirects=False") == 2
    assert cost.LEAVES == {"query": "POST", "forecast": "POST", "budgets": "GET", "alerts": "GET"}
    assert not re.findall(r"https?://", source.split('"""', 2)[2])  # the host is the client's constant, never a literal here
    assert re.findall(r"_leaf\(\w+, \"(\w+)\"\)", source) == ["query", "forecast", "budgets", "alerts"]
    assert re.findall(r"_pages\(\"(GET|POST)\"", source) == ["POST", "POST", "GET", "GET", "GET"]
    assert "/billingAccounts" in cost.BILLING_PATH
