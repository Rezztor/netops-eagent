"""`prices.price` against a stand-in for prices.azure.com (fake_prices.py): the filter, the choice of one row, the
seven-day cache, the refusals and the unreachable case. Needs PostgreSQL for the cache."""

import ast
import datetime as dt
from pathlib import Path

import pytest

from helpers import db
from plugins._ea_azure.helpers import prices
from plugins._ea_azure.tests.fake_prices import FakePrices

REGION = "uaenorth"


@pytest.fixture
def fake(database, monkeypatch):
    with FakePrices() as f:
        monkeypatch.setattr(prices, "HOST", f.url)
        monkeypatch.setattr(prices, "_down_until", 0.0)
        yield f


def test_a_vm_price_is_the_linux_or_the_windows_row_never_spot_and_a_month_is_730_hours(fake):
    linux = prices.price(REGION, "Virtual Machines", "Standard_D2s_v5", os="linux")
    assert linux["usd_hour"] == 0.118 and linux["usd_month"] == 86.14 and not linux["cached"]
    assert prices.price(REGION, "Virtual Machines", "Standard_D2s_v5", os="windows")["usd_hour"] == 0.21
    assert prices.price(REGION, "Virtual Machines", "Standard_D2s_v5") is None  # two rows fit: refuse to guess


def test_app_service_linux_plan_and_a_monthly_meter(fake):
    assert prices.price(REGION, "Azure App Service", "P1 v3", os="linux")["usd_hour"] == 0.191
    assert prices.price(REGION, "Azure App Service", "P1 v3", os="windows")["usd_hour"] == 0.351
    disk = prices.price(REGION, "Storage", "P10 LRS", "P10 LRS Disk")
    assert disk["usd_unit"] == 21.504 and disk["usd_hour"] is None and disk["usd_month"] == 21.5 and disk["unit"] == "1/Month"
    assert prices.price(REGION, "Storage", "P10 LRS") is None  # three meters fit


def test_the_second_ask_is_answered_from_the_database_for_seven_days(fake):
    prices.price(REGION, "Virtual Machines", "Standard_D2s_v5", os="linux")
    again = prices.price(REGION, "Virtual Machines", "Standard_D2s_v5", os="linux")
    assert again["cached"] and len(fake.received) == 1
    with db.transaction(db.NO_SCOPE) as conn:
        conn.execute("UPDATE cloud_price SET fetched_at = %s", (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=8),))
    assert not prices.price(REGION, "Virtual Machines", "Standard_D2s_v5", os="linux")["cached"] and len(fake.received) == 2


def test_the_filter_is_built_only_from_values_that_pass_their_pattern(fake):
    prices.price("UAENorth", "Virtual Machines", "Standard_D2s_v5", os="linux")
    sent = fake.received[0]
    assert sent["path"] == "/api/retail/prices" and sent["query"]["api-version"] == "2023-01-01-preview"
    assert sent["query"]["$filter"] == ("armRegionName eq 'uaenorth' and serviceName eq 'Virtual Machines' and "
                                        "skuName eq 'Standard_D2s_v5' and priceType eq 'Consumption'")
    for region, service, sku, meter in [
        ("uae north", "Virtual Machines", "x", ""), ("uaenorth' or 1 eq 1 or armRegionName eq 'x", "Virtual Machines", "x", ""),
        (REGION, "Everything", "x", ""), (REGION, "Virtual Machines", "x' or serviceName eq 'Storage", ""),
        (REGION, "Virtual Machines", "", ""), (REGION, "Virtual Machines", "a" * 65, ""),
        (REGION, "Virtual Machines", "x", "m' or 'a' eq 'a"),
    ]:
        assert prices.price(region, service, sku, meter) is None
    assert prices.price(REGION, "Virtual Machines", "x", os="beos") is None
    assert len(fake.received) == 1  # none of the refused ones was sent


@pytest.mark.parametrize("break_it", ("down", "redirect", "huge"))
def test_none_when_microsoft_cannot_be_reached_and_it_is_not_asked_again_for_ten_minutes(fake, monkeypatch, break_it):
    if break_it == "down":
        fake.status = 500
    elif break_it == "redirect":
        fake.redirect = "http://127.0.0.1:9/elsewhere"
    else:
        fake.extra = b" " * prices.MAX_ANSWER
    assert prices.price(REGION, "Virtual Machines", "Standard_D2s_v5", os="linux") is None
    assert prices.price(REGION, "Azure App Service", "P1 v3", os="linux") is None
    assert len(fake.received) == 1  # the second ask did not leave the building
    monkeypatch.setattr(prices, "_clock", lambda: prices._down_until + 1)
    fake.status, fake.redirect, fake.extra = 200, None, b""
    assert prices.price(REGION, "Virtual Machines", "Standard_D2s_v5", os="linux")["usd_hour"] == 0.118


def test_a_dead_host_gives_none(database, monkeypatch):
    monkeypatch.setattr(prices, "HOST", "http://127.0.0.1:9")
    monkeypatch.setattr(prices, "_down_until", 0.0)
    assert prices.price(REGION, "Virtual Machines", "Standard_D2s_v5", os="linux") is None


def test_the_module_reaches_one_constant_host():
    tree = ast.parse((Path(prices.__file__)).read_text())
    gets = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") in ("get", "post", "put", "patch", "delete", "request")
            and getattr(getattr(n.func, "value", None), "id", "") == "requests"]
    assert len(gets) == 1 and gets[0].func.attr == "get"
    assert prices.HOST == "https://prices.azure.com" and gets[0].keywords[0].arg == "params"
