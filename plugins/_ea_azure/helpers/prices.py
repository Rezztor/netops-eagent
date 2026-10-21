"""Microsoft's public retail price list, one price at a time. Never a token, never the tenant.

One call: `GET https://prices.azure.com/api/retail/prices` with a filter built here from a region, a service name
from `SERVICES` and a SKU (and optionally a meter), each checked against a pattern first, so nothing a model typed
can add a clause to the filter. The host is a constant, redirects are not followed, only the first page is read,
and the answer is capped. A price is kept in `cloud_price` for seven days; `price` returns `None` when it cannot
say (no internet, no such meter, or more than one meter fits and the caller did not name one), and a refused
host is remembered for ten minutes so a product with no internet does not wait on every question.

The prices are list prices in USD, not any customer's: the callers say so. It imports only `requests` and `helpers.db`.
The build contract has the cloud executor copy this file (phase 2); that copy reads and writes `cloud_price` as the
`eagent_cloud` role, which the contract's migration does not grant, so phase 2 must grant it or swap `_cached`/`_store`.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import time
from typing import Any

import requests
from psycopg.rows import dict_row

from helpers import db

HOST = "https://prices.azure.com"  # a test replaces it with its own loopback stand-in; nothing at run time can
PATH = "/api/retail/prices"
API_VERSION = "2023-01-01-preview"
TTL = _dt.timedelta(days=7)
TIMEOUT, MAX_ANSWER, DOWN_FOR = 5, 2 * 1024 * 1024, 600
HOURS_A_MONTH = 730
#: serviceName values this module asks for; Microsoft's own spelling, case-sensitive
SERVICES = ("Virtual Machines", "Azure App Service", "Storage", "Virtual Network", "Load Balancer", "Azure Firewall",
            "VPN Gateway", "Application Gateway", "Azure Bastion", "Azure Database for PostgreSQL", "SQL Database")
_REGION = re.compile(r"[a-z][a-z0-9]{2,31}")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_. +-]{0,63}")  # a SKU or a meter; no quote, so no clause can be added
_clock = time.monotonic
_down_until = 0.0


class Price(dict):
    """`usd_hour` or `usd_unit` (the other is None), `unit`, `usd_month` (None when Microsoft's unit is not hourly or
    monthly), `fetched_at` (ISO), `cached`."""


def _key(region: str, service: str, sku: str, meter: str) -> str:
    return f"{region}|{service}|{sku}|{meter}"


def _month(usd_hour: Any, usd_unit: Any, unit: str) -> float | None:
    if usd_hour is not None:
        return round(float(usd_hour) * HOURS_A_MONTH, 2)
    return round(float(usd_unit), 2) if usd_unit is not None and unit.strip().lower() in ("1/month", "1 month") else None


def _price(row: dict[str, Any], cached: bool) -> Price:
    h, u = row["usd_hour"], row["usd_unit"]
    return Price(usd_hour=None if h is None else float(h), usd_unit=None if u is None else float(u), unit=row["unit"],
                 usd_month=_month(h, u, row["unit"]), fetched_at=row["fetched_at"].isoformat(), cached=cached)


def _cached(key: str) -> Price | None:
    with db.transaction(db.NO_SCOPE) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT usd_hour, usd_unit, unit, fetched_at FROM cloud_price WHERE key = %s AND fetched_at > %s",
                    (key, _dt.datetime.now(_dt.timezone.utc) - TTL))
        row = cur.fetchone()
    return _price(row, True) if row else None


def _store(key: str, usd_hour: float | None, usd_unit: float | None, unit: str) -> Price:
    with db.transaction(db.NO_SCOPE) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "INSERT INTO cloud_price (key, usd_hour, usd_unit, unit, fetched_at) VALUES (%s, %s, %s, %s, now()) "
            "ON CONFLICT (key) DO UPDATE SET usd_hour = EXCLUDED.usd_hour, usd_unit = EXCLUDED.usd_unit, "
            "unit = EXCLUDED.unit, fetched_at = EXCLUDED.fetched_at RETURNING usd_hour, usd_unit, unit, fetched_at",
            (key, usd_hour, usd_unit, unit))
        return _price(cur.fetchone(), False)


def _is_windows(item: dict[str, Any]) -> bool:
    """A Windows price: a VM product says "Windows"; an App Service Linux plan says "Linux" and a Windows one says neither."""
    product = str(item.get("productName") or "").lower()
    if item.get("serviceName") == "Azure App Service":
        return "linux" not in product
    return "windows" in product


def _pick(items: list[Any], os: str) -> dict[str, Any] | None:
    """The one hourly or unit price that fits, or None. Spot, low-priority and tiered rows are never the list price."""
    fit = [i for i in items if isinstance(i, dict) and isinstance(i.get("retailPrice"), (int, float))
           and i.get("type") == "Consumption" and not i.get("tierMinimumUnits")
           and not re.search(r"spot|low priority", f"{i.get('meterName')} {i.get('skuName')}", re.I)
           and (not os or _is_windows(i) == (os == "windows"))]
    if len({(i.get("meterName"), i["retailPrice"]) for i in fit}) != 1:  # two products at one price are not a doubt
        return None
    return fit[0]


def _get(params: dict[str, str]) -> dict[str, Any] | None:
    try:
        with requests.get(HOST + PATH, params=params, timeout=TIMEOUT, allow_redirects=False, stream=True) as r:
            body = r.raw.read(MAX_ANSWER + 1, decode_content=True)
            if r.status_code != 200 or len(body) > MAX_ANSWER:
                return None
        data = json.loads(body)
    except (requests.RequestException, ValueError):
        return None
    return data if isinstance(data, dict) else None


def price(region: str, service: str, sku: str, meter: str = "", os: str = "") -> Price | None:
    """The list price of one SKU (`skuName`, e.g. `Standard_D2s_v5`, `P1 v3`, `P10 LRS`) in a region, optionally one
    `meterName`; `os` is `linux`, `windows` or empty. `None` for an argument that fails its pattern."""
    global _down_until
    region, sku, meter, os = region.strip().lower(), sku.strip(), meter.strip(), os.strip().lower()
    if (not _REGION.fullmatch(region) or service not in SERVICES or not _NAME.fullmatch(sku)
            or (meter and not _NAME.fullmatch(meter)) or os not in ("", "linux", "windows")):
        return None
    key = _key(region, service, sku, f"{meter}|{os}" if os else meter)
    if (hit := _cached(key)) or _clock() < _down_until:
        return hit
    flt = (f"armRegionName eq '{region}' and serviceName eq '{service}' and skuName eq '{sku}' "
           f"and priceType eq 'Consumption'" + (f" and meterName eq '{meter}'" if meter else ""))
    answer = _get({"api-version": API_VERSION, "currencyCode": "USD", "$filter": flt})
    if answer is None:
        _down_until = _clock() + DOWN_FOR
        return None
    item = _pick(answer.get("Items") if isinstance(answer.get("Items"), list) else [], os)
    if item is None:
        return None
    hourly = str(item.get("unitOfMeasure") or "").strip().lower() == "1 hour"
    return _store(key, item["retailPrice"] if hourly else None, None if hourly else item["retailPrice"],
                  str(item.get("unitOfMeasure") or ""))
