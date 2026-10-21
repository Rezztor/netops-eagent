"""What the tenant pays, from Azure Cost Management. A separate read path like `prices.py` (ADR 0056 amendment, 0060):
the client stays three calls, and this module is the only other one that signs in with the read credential.

Five reads, all with the Reader role the plugin already has, all to the same Resource Manager host the client uses
(`client._cloud().arm`) and nowhere else, and every address is a stored subscription GUID (and an optional resource
group name that passed a pattern) plus a leaf from `LEAVES`: `POST .../query` (actual cost by service, resource
group, resource, tag or day), `POST .../forecast`, `GET .../budgets`, `GET .../alerts`, and one billing read for the
agreement type. No function takes a URL or a method from a caller, redirects are not followed, a `nextLink` is
followed only on the same host, and a test scans this file's source for anything else.

Cost Management is metered (12 query units per 10 seconds, 600 an hour per tenant, a month of data queried being
one unit) and slow to change (data refreshes about every four hours), so an answer is kept in `cloud_cost_cache` and
a question asked again within `TTL` is answered from there; a read that Azure cannot answer is served stale, marked
with its age. The throttling and quota headers are honoured: a 429 or an empty quota pauses every live read until
Azure says it may go on. Nothing Azure sent beyond the cost fields shown is kept (a budget's contact e-mails and an
alert's user names are dropped).

The numbers are Azure's. This module adds rows of the same currency to make a total and nothing else: no conversion,
no estimate. A forecast is labelled as one. Every answer says its period, its currency, how late the data can be,
whether the scope is complete, and that credits are not deducted (`notes`).

Response shapes are the plugin's reading of Microsoft's documentation; each is marked `# unverified:` until a real
tenant has been read (build contract phase 6, test session 5).
"""

from __future__ import annotations

import collections
import datetime as _dt
import math
import re
import time
from typing import Any, Callable, NamedTuple

import requests
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from helpers import db
from plugins._ea_azure.helpers import client, config, store
from plugins._ea_inventory.helpers import redact

# unverified: api-version and every path below are from Microsoft's REST reference (research R3 2.1), not yet called.
API = "2026-06-01"
BILLING_API = "2024-04-01"
PROVIDER = "/providers/Microsoft.CostManagement"
BILLING_PATH = "/providers/Microsoft.Billing/billingAccounts"
#: the only leaves this module reaches under a scope, with the verb each takes
LEAVES = {"query": "POST", "forecast": "POST", "budgets": "GET", "alerts": "GET"}

TTL = _dt.timedelta(hours=4)  # Azure refreshes cost about every four hours; asking more often only spends quota
AGREEMENT_TTL = _dt.timedelta(hours=24)
KEEP = _dt.timedelta(days=7)  # a row older than this is deleted by the next write; stale answers are never older
LIVE_PER_HOUR = 30  # this product's own ceiling on calls to Azure, far under the tenant's 600 query units
MAX_PAGES, MAX_ANSWER, TOP = 5, 4 * 1024 * 1024, 30
FORECAST_HISTORY_DAYS = 90  # Microsoft recommends 90 days of history for a forecast

TIMEFRAMES = ("month_to_date", "last_month", "last_3_months")
QUESTIONS = ("spend", "forecast", "budgets", "item")
#: `group_by` values and the Cost Management dimension each asks for; `none` and `day` ask for no dimension
GROUPS = {"service": "ServiceName", "resource_group": "ResourceGroupName", "resource": "ResourceId", "none": "", "day": ""}
GROUP_HELP = "service, resource_group, resource, day, none, or tag:<name>"

_GUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_GROUP = re.compile(r"[A-Za-z0-9_.()-]{1,90}")  # a resource group name; no slash, so no path can be added
_TAG = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
_WORD = re.compile(r"[A-Za-z]{1,48}")
_RESOURCE_ID = re.compile(r"/subscriptions/([0-9a-f-]{36})/[A-Za-z0-9_./()-]{1,900}")
_COST_COLUMNS = ("Cost", "PreTaxCost")
_STALE = "stale: Azure could not be asked, so this is the last answer it gave"

LATE = ("Cost data can be up to 72 hours late (8 to 24 hours on an Enterprise or Microsoft Customer Agreement) and "
        "later still on a free trial. The current month is an estimate until the invoice and can change until the "
        "fifth day after the period ends.")
CREDITS = ("Free and prepaid credits (a free trial's credit, for example) are not deducted here: these figures are "
           "what the usage costs before tax, not what was charged.")
CSP = "Under a partner (CSP) agreement these are pay-as-you-go rates, not your partner's price."
FORECAST = ("A forecast is a statistical projection, not a charge. It can be wrong in either direction, and Microsoft "
           "recommends 90 days of history for it.")
BUDGET = ("A budget only alerts: it never stops spending. Azure evaluates budgets about every 24 hours on actual "
          "cost, so an alert can arrive a day late.")
QUOTE = "Quote these figures as given: do not add, convert or round them yourself."

_clock = time.monotonic
_blocked_until = 0.0  # a monotonic time before which no live read is made
_recent: collections.deque = collections.deque(maxlen=LIVE_PER_HOUR)  # when the last live reads were made


class Refused(ValueError):
    """An argument that is not allowed; the message is the sentence to give back."""


class CostUnavailable(Exception):
    """Azure could not be asked or would not answer; the message is this plugin's own sentence, never Azure's text."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class Scope(NamedTuple):
    path: str
    subscription: str  # the GUID
    group: str  # a lower-cased resource group name, or ""
    name: str  # the subscription's stored name


class Got(NamedTuple):
    body: Any
    at: _dt.datetime
    source: str  # live | cache | stale


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


# --- periods ------------------------------------------------------------------------------------------------------------

def _month_back(day: _dt.date, back: int) -> _dt.date:
    index = day.year * 12 + day.month - 1 - back
    return _dt.date(index // 12, index % 12 + 1, 1)


def period(timeframe: str, now: _dt.datetime) -> tuple[_dt.date, _dt.date]:
    """The first and last UTC day a timeframe covers. The period is always sent as dates, so what the answer says
    is what was asked, whatever Azure's own named timeframes mean."""
    today = now.date()
    first = today.replace(day=1)
    if timeframe == "month_to_date":
        return first, today
    last_end = first - _dt.timedelta(days=1)
    if timeframe == "last_month":
        return last_end.replace(day=1), last_end
    if timeframe == "last_3_months":
        return _month_back(first, 3), last_end
    raise Refused(f"timeframe must be one of: {', '.join(TIMEFRAMES)}.")


def _month_end(day: _dt.date) -> _dt.date:
    return _month_back(day.replace(day=1), -1) - _dt.timedelta(days=1)


def _span(start: _dt.date, end: _dt.date) -> dict[str, dict[str, str]]:
    return {"timePeriod": {"from": f"{start}T00:00:00Z", "to": f"{end}T23:59:59Z"}}


# --- the cache ------------------------------------------------------------------------------------------------------------

def _read(key: str) -> dict[str, Any] | None:
    with db.transaction(db.NO_SCOPE) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT body, fetched_at FROM cloud_cost_cache WHERE key = %s", (key,))
        return cur.fetchone()


def _write(key: str, body: Any, at: _dt.datetime) -> None:
    with db.transaction(db.NO_SCOPE) as conn:
        conn.execute("INSERT INTO cloud_cost_cache (key, body, fetched_at) VALUES (%s, %s, %s) ON CONFLICT (key) "
                     "DO UPDATE SET body = EXCLUDED.body, fetched_at = EXCLUDED.fetched_at", (key, Jsonb(body), at))
        conn.execute("DELETE FROM cloud_cost_cache WHERE fetched_at < %s", (at - KEEP,))


def _fetch(key: str, ttl: _dt.timedelta, live: Callable[[], Any]) -> Got:
    """The cached answer if it is younger than `ttl`; else ask Azure and keep what it says; else the old one, marked."""
    row, now = _read(key), _now()
    if row and now - row["fetched_at"] < ttl:
        return Got(row["body"], row["fetched_at"], "cache")
    try:
        body = live()
    except CostUnavailable:
        if row:
            return Got(row["body"], row["fetched_at"], "stale")
        raise
    _write(key, body, now)
    return Got(body, now, "live")


# --- the one place that calls Azure -----------------------------------------------------------------------------------------

def _block(seconds: float) -> None:
    global _blocked_until
    _blocked_until = max(_blocked_until, _clock() + min(seconds, 3600))


def _gate() -> None:
    """Refuse a live read while Azure has asked us to wait, or when this product has made too many in the hour."""
    now = _clock()
    if now < _blocked_until:
        raise CostUnavailable(f"Azure asked this product to wait about {math.ceil(_blocked_until - now)} seconds before "
                              "the next cost read.", 429)
    if len(_recent) == _recent.maxlen and now - _recent[0] < 3600:
        raise CostUnavailable(f"This product has already made {LIVE_PER_HOUR} cost reads in the last hour and stops "
                              "there to leave Azure's quota to others. Try again later.", 429)
    _recent.append(now)


def _note_limits(response: requests.Response) -> None:
    """Honour Azure's own pause: `Retry-After` or a Cost Management retry-after header after a 429, and an empty quota.

    unverified: the header names are from Microsoft's "manage cost data in automation" page; their value formats are not
    confirmed, so an unreadable value is ignored rather than guessed.
    """
    waits = [client._seconds(v) for k, v in response.headers.items() if k.lower() == "retry-after" or (
        k.lower().startswith("x-ms-ratelimit-microsoft.costmanagement-") and k.lower().endswith("retry-after"))]
    wait = max((w for w in waits if w), default=None)
    if response.status_code == 429:
        _block(wait or 60)
        return
    left = response.headers.get("x-ms-ratelimit-microsoft.costmanagement-qpu-remaining")
    numbers = re.findall(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])", left or "")
    if numbers and min(map(float, numbers)) < 1:
        _block(wait or 10)


def _refused(status: int) -> CostUnavailable:
    if status == 403:
        return CostUnavailable("The application's Azure role does not allow reading cost data here (HTTP 403). The "
                               "Reader role normally does.", status)
    if status in (400, 404):
        return CostUnavailable(f"Azure has no cost data for this scope (HTTP {status}): an offer Cost Management does not "
                               "support, a subscription made in the last 48 hours, or a question it did not accept.", status)
    if status == 429:
        return CostUnavailable("Azure is limiting cost reads. Try again in a few minutes.", status)
    if status == 401:
        return CostUnavailable("Azure did not accept the sign-in for cost data (HTTP 401).", status)
    return CostUnavailable(f"Azure could not answer the cost read (HTTP {status}).", status)


def _call(method: str, url: str, body: dict | None = None) -> dict | None:
    """One authorised call; the parsed JSON object, or None for Azure's 204 (no data). Never Azure's error text."""
    _gate()
    for attempt in (0, 1):
        try:
            headers = {"Authorization": f"Bearer {client.token()}"}
            timeout = config.get_config()["timeout_seconds"]
            if method == "POST":
                response = requests.post(url, json=body, headers=headers, timeout=timeout, allow_redirects=False)
            else:
                response = requests.get(url, headers=headers, timeout=timeout, allow_redirects=False)
        except client.AzureError as e:
            raise CostUnavailable(str(e)) from None
        except requests.RequestException:
            raise CostUnavailable("Azure could not be reached.") from None
        _note_limits(response)
        if response.status_code == 401 and attempt == 0:
            client.reset()
            continue
        break
    if response.status_code == 204:
        return None
    if response.status_code != 200:
        raise _refused(response.status_code)
    if len(response.content) > MAX_ANSWER:
        raise CostUnavailable("Azure's cost answer is too large to read; ask about one subscription or a coarser grouping.")
    try:
        data = response.json()
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise CostUnavailable("Azure answered the cost read with something this plugin cannot read.", 200)
    return data


def _pages(method: str, leaf_url: str, body: dict | None = None) -> tuple[list[dict], bool]:
    """Every page (at most `MAX_PAGES`) of one read, and whether Azure had more. A `nextLink` is followed only on the
    Resource Manager host; unverified: a query's `nextLink` is posted again with the same body."""
    arm = client._cloud().arm
    url, pages = leaf_url, []
    while url and len(pages) < MAX_PAGES:
        data = _call(method, url, body)
        if data is None:
            break
        pages.append(data)
        url = data.get("nextLink") or (data.get("properties") or {}).get("nextLink") or ""
        if url and not (isinstance(url, str) and url.startswith(f"{arm}/")):
            raise CostUnavailable("Azure pointed the cost read at another address; it was not followed.")
    return pages, bool(url)


def _leaf(scope: Scope, leaf: str) -> str:
    assert leaf in LEAVES  # the address is a stored GUID, a checked group name and one of four constants
    return f"{client._cloud().arm}{scope.path}{PROVIDER}/{leaf}?api-version={API}"


# --- reading Azure's tables -----------------------------------------------------------------------------------------------

def _num(value: Any) -> float | None:
    return round(float(value), 4) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _day(value: Any) -> _dt.date | None:
    """`UsageDate` as Azure sends it: 20261003, or an ISO date or date-time."""
    text = str(value or "")
    try:
        return _dt.date(int(text[:4]), int(text[4:6]), int(text[6:8])) if re.fullmatch(r"\d{8}", text) else _dt.date.fromisoformat(text[:10])
    except ValueError:
        return None


def _columns(properties: dict) -> list[str]:
    cols = properties.get("columns")
    if not isinstance(cols, list) or not all(isinstance(c, dict) for c in cols):
        raise CostUnavailable("Azure answered the cost read with something this plugin cannot read.", 200)
    return [str(c.get("name")) for c in cols]


def _table(pages: list[dict]) -> tuple[list[str], list[list]]:
    cols, rows = [], []
    for page in pages:
        props = page.get("properties")
        if not isinstance(props, dict) or not isinstance(props.get("rows"), list):
            raise CostUnavailable("Azure answered the cost read with something this plugin cannot read.", 200)
        cols = cols or _columns(props)
        rows += props["rows"]
    if cols and any(not isinstance(r, list) or len(r) != len(cols) for r in rows):
        raise CostUnavailable("Azure answered the cost read with rows this plugin cannot read.", 200)
    return cols, rows


def _cost_index(cols: list[str]) -> int:
    for name in _COST_COLUMNS:
        if name in cols:
            return cols.index(name)
    raise CostUnavailable("Azure's cost answer has no cost column this plugin knows.", 200)


def _label(cols: list[str], row: list, skip: set[int]) -> str:
    parts = []
    for i, col in enumerate(cols):
        if i in skip or (col == "TagKey" and "TagValue" in cols):
            continue
        value = row[i]
        if col == "UsageDate":
            value = _day(value) or value
        parts.append(str(value) if value not in (None, "") else ("(no tag)" if col == "TagValue" else "(none)"))
    return " / ".join(parts) or "total"


def _rows(pages: list[dict]) -> list[list]:
    """`[label, cost, currency]` for each row of a query; a number that is not a number is an unreadable answer."""
    cols, rows = _table(pages)
    if not rows:
        return []
    cost = _cost_index(cols)
    cur = cols.index("Currency") if "Currency" in cols else None
    skip = {cost} | ({cur} if cur is not None else set())
    out = []
    for row in rows:
        if _num(row[cost]) is None:
            raise CostUnavailable("Azure's cost answer has a cost this plugin cannot read.", 200)
        out.append([_label(cols, row, skip), _num(row[cost]), str(row[cur] or "")[:8] if cur is not None else ""])
    return out


# --- the five reads -------------------------------------------------------------------------------------------------------------

def _query(scope: Scope, start: _dt.date, end: _dt.date, group_by: str, resource: str) -> dict:
    dataset: dict[str, Any] = {"granularity": "Daily" if group_by == "day" else "None",
                               "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}}}
    if group_by.startswith("tag:"):
        dataset["grouping"] = [{"type": "TagKey", "name": group_by[4:]}]
    elif GROUPS.get(group_by):
        dataset["grouping"] = [{"type": "Dimension", "name": GROUPS[group_by]}]
    if resource:
        dataset["filter"] = {"dimensions": {"name": "ResourceId", "operator": "In", "values": [resource]}}
    body = {"type": "ActualCost", "timeframe": "Custom", **_span(start, end), "dataset": dataset}
    pages, more = _pages("POST", _leaf(scope, "query"), body)
    return {"rows": _rows(pages), "truncated": more}


def _forecast(scope: Scope, start: _dt.date, end: _dt.date) -> dict:
    body = {"type": "ActualCost", "timeframe": "Custom", **_span(start, end), "includeActualCost": True,
            "includeFreshPartialCost": False,
            "dataset": {"granularity": "Daily", "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}}}}
    pages, _ = _pages("POST", _leaf(scope, "forecast"), body)  # a 204 is "no forecast": no pages
    cols, rows = _table(pages)
    if not rows:
        return {"days": []}
    cost = _cost_index(cols)
    want = [c for c in ("UsageDate", "CostStatus") if c in cols]
    if len(want) < 2:
        raise CostUnavailable("Azure's forecast answer lacks its dates or its actual-or-forecast marker.", 200)
    cur = cols.index("Currency") if "Currency" in cols else None
    days = []
    for r in rows:
        day = _day(r[cols.index("UsageDate")])
        if day is None or _num(r[cost]) is None:
            raise CostUnavailable("Azure's forecast answer has a row this plugin cannot read.", 200)
        days.append([day.isoformat(), _num(r[cost]), str(r[cols.index("CostStatus")]).lower(),
                     str(r[cur] or "")[:8] if cur is not None else ""])
    return {"days": days}


def _budget(item: Any) -> dict:
    """One budget, cut to its figures. Contact e-mails and action groups are dropped here, never stored."""
    p = item.get("properties") if isinstance(item, dict) and isinstance(item.get("properties"), dict) else {}
    spent, ahead = (p.get("currentSpend") or {}), (p.get("forecastSpend") or {})
    sent = [n for n in (p.get("notifications") or {}).values() if isinstance(n, dict)]
    window = p.get("timePeriod") or {}
    return {"name": str(item.get("name") or "")[:128] if isinstance(item, dict) else "", "amount": _num(p.get("amount")),
            "time_grain": str(p.get("timeGrain") or "")[:24], "category": str(p.get("category") or "")[:24],
            "start": str(window.get("startDate") or "")[:10], "end": str(window.get("endDate") or "")[:10],
            "current_spend": _num(spent.get("amount")), "forecast_spend": _num(ahead.get("amount")),
            "currency": str(spent.get("unit") or ahead.get("unit") or "")[:8],
            "notifications": [{"operator": str(n.get("operator") or "")[:24], "threshold": _num(n.get("threshold")),
                               "threshold_type": str(n.get("thresholdType") or "")[:24], "enabled": n.get("enabled")}
                              for n in sent[:10]]}


def _alert(item: Any) -> dict:
    """One cost alert, cut to its figures. Contact lists and the name of who dismissed it are dropped."""
    p = item.get("properties") if isinstance(item, dict) and isinstance(item.get("properties"), dict) else {}
    d, kind = (p.get("details") or {}), (p.get("definition") or {})
    return {"type": str(kind.get("type") or "")[:32], "category": str(kind.get("category") or "")[:32],
            "status": str(p.get("status") or "")[:24], "created": str(p.get("creationTime") or "")[:25],
            "period_start": str(d.get("periodStartDate") or "")[:10], "operator": str(d.get("operator") or "")[:24],
            "threshold": _num(d.get("threshold")), "amount": _num(d.get("amount")), "unit": str(d.get("unit") or "")[:8],
            "current_spend": _num(d.get("currentSpend")), "description": str(p.get("description") or "")[:200]}


def _budgets(scope: Scope) -> dict:
    found, more = _pages("GET", _leaf(scope, "budgets"))
    alerts, more_alerts = _pages("GET", _leaf(scope, "alerts"))
    pick = lambda pages: [i for p in pages for i in (p.get("value") if isinstance(p.get("value"), list) else [])]  # noqa: E731
    return {"budgets": [_budget(i) for i in pick(found)][:50],
            "alerts": sorted((_alert(i) for i in pick(alerts)), key=lambda a: a["created"], reverse=True)[:25],
            "truncated": more or more_alerts}


def _agreement_live() -> dict:
    """The agreement types of the billing accounts the application can see. Billing account names and IDs are not
    kept. Unverified: what a service principal sees on a trial or pay-as-you-go account (empty is expected)."""
    try:
        pages, _ = _pages("GET", f"{client._cloud().arm}{BILLING_PATH}?api-version={BILLING_API}")
    except CostUnavailable as e:
        if e.status in (400, 403, 404):
            return {"types": []}  # not visible to this application: a normal answer, kept for the day
        raise
    types = {str(((i.get("properties") or {}) if isinstance(i, dict) else {}).get("agreementType") or "")
             for p in pages for i in (p.get("value") if isinstance(p.get("value"), list) else [])}
    return {"types": sorted(t for t in types if _WORD.fullmatch(t))}


def agreement() -> list[str]:
    """The agreement type(s) of the visible billing account(s), e.g. `MicrosoftCustomerAgreement`; empty when not visible."""
    try:
        return _fetch("agreement", AGREEMENT_TTL, _agreement_live).body["types"]
    except CostUnavailable:
        return []


# --- scope ----------------------------------------------------------------------------------------------------------------------

def _scopes(subscription: str, group: str) -> tuple[list[Scope], list[dict], int]:
    """The scopes to ask, the stored subscriptions that cannot be asked (with why), and how many are stored."""
    subs = store.subscriptions()
    if not subs:
        raise Refused("No Azure inventory has been pulled yet, so there is no subscription to ask Cost Management about.")
    group = group.strip()
    if group and not _GROUP.fullmatch(group):
        raise Refused("resource_group must be a resource group name (letters, digits, . _ - and parentheses).")
    want = subscription.strip().lower()
    chosen = [s for s in subs if not want or want in (s["native_id"].lower(), s["name"].lower())]
    if not chosen:
        raise Refused(f"No stored subscription is called {subscription!r}. Stored: {', '.join(s['name'] for s in subs)}.")
    ok, skipped = [], []
    for s in chosen:
        guid = s["native_id"].lower()
        if not s["readable"] or not _GUID.fullmatch(guid):
            skipped.append({"subscription": s["name"], "reason": "it was not readable at the last pull"})
        else:
            ok.append(Scope(f"/subscriptions/{guid}" + (f"/resourceGroups/{group}" if group else ""), guid, group.lower(), s["name"]))
    return ok, skipped, len(subs)


def _each(scopes: list[Scope], skipped: list[dict], one: Callable[[Scope], Got]) -> tuple[list[tuple[Scope, Got]], dict]:
    """Ask each scope; one that fails is listed with its reason, and the answer says the scope is not complete."""
    got, out = [], list(skipped)
    for scope in scopes:
        try:
            got.append((scope, one(scope)))
        except CostUnavailable as e:
            out.append({"subscription": scope.name, "reason": str(e)})
    if not got:
        raise CostUnavailable(out[0]["reason"] if out else "There is no subscription to ask.")
    return got, {"not_included": out}


def _scope_report(got: list[tuple[Scope, Got]], missing: dict, stored: int, group: str, truncated: bool = False) -> dict:
    complete = not missing["not_included"] and not truncated
    return {"included": [{"subscription": s.name, "resource_group": group or None, "data": g.source if g.source != "stale" else _STALE,
                          "fetched_at": g.at.isoformat(timespec="seconds")} for s, g in got],
            **missing, "complete": complete,
            "covers": f"{len(got)} of {stored} stored subscription{'' if stored == 1 else 's'}"
                      + (f", resource group {group} only" if group else "")
                      + ("" if not truncated else "; Azure had more rows than this plugin reads")}


def _notes(*lead: str, types: list[str]) -> list[str]:
    return [*lead, LATE, CREDITS, *([CSP] if "MicrosoftPartnerAgreement" in types else []), QUOTE]


def _totals(rows: list[list]) -> list[dict]:
    sums: dict[str, float] = {}
    for _, cost, currency in rows:
        sums[currency] = sums.get(currency, 0.0) + cost
    return [{"currency": c or "not reported", "cost": round(v, 2)} for c, v in sorted(sums.items())]


# --- the questions ---------------------------------------------------------------------------------------------------------------

def spend(timeframe: str = "month_to_date", group_by: str = "service", subscription: str = "", resource_group: str = "",
          resource: str = "") -> dict:
    """Actual cost over a timeframe, grouped. Rows of the same name and currency from different subscriptions are added."""
    if timeframe not in TIMEFRAMES:
        raise Refused(f"timeframe must be one of: {', '.join(TIMEFRAMES)}.")
    if group_by not in GROUPS and not (group_by.startswith("tag:") and _TAG.fullmatch(group_by[4:])):
        raise Refused(f"group_by must be {GROUP_HELP}.")
    start, end = period(timeframe, _now())
    scopes, skipped, stored = _scopes(subscription, resource_group)
    got, missing = _each(scopes, skipped, lambda s: _fetch(
        _key("query", s, start, end, group_by, resource), TTL, lambda: _query(s, start, end, group_by, resource)))
    merged: dict[tuple[str, str], float] = {}
    for _, g in got:
        for label, cost, currency in g.body["rows"]:
            name = (label.lower() if group_by == "resource" else label, currency)
            merged[name] = merged.get(name, 0.0) + cost
    rows = [[k[0], round(v, 2), k[1]] for k, v in merged.items()]
    totals = _totals(rows)
    rows.sort(key=(lambda r: r[0]) if group_by == "day" else (lambda r: -r[1]))
    shown = rows if group_by == "day" else rows[:TOP]
    types, truncated = agreement(), any(g.body["truncated"] for _, g in got)
    out = {"question": "spend", "cost_type": "ActualCost, before tax", "timeframe": timeframe,
           "period": {"from": start.isoformat(), "to": end.isoformat(), "timezone": "UTC"}, "group_by": group_by,
           "totals": totals, "rows": [{"name": n, "cost": c, "currency": cur or "not reported"} for n, c, cur in shown],
           "rows_not_shown": len(rows) - len(shown),
           "scope": _scope_report(got, missing, stored, resource_group, truncated),
           "agreement": ", ".join(types) or "not visible to this application",
           "fetched_at": min(g.at for _, g in got).isoformat(timespec="seconds")}
    if group_by == "day":
        out["latest_usage_date"] = max((r[0] for r in rows if r[1] > 0), default=None)
    out["notes"] = _notes(types=types)
    out["headline"] = (f"Azure cost {start} to {end} (UTC), actual cost before tax, "
                       f"{' and '.join(t['currency'] for t in totals) or 'no currency reported'}; fetched {out['fetched_at']}"
                       + ("" if out["scope"]["complete"] else "; NOT the whole estate: " + out["scope"]["covers"]) + ".")
    return out


def item(resource: str, timeframe: str = "last_month") -> dict:
    """What one inventory resource cost: the resource is looked up in the stored inventory by name or resource ID."""
    try:
        found = store.get(resource)
    except store.Ambiguous as e:
        raise Refused("Several resources have that name; use the resource ID of one of: "
                      + ", ".join(c["native_id"] for c in e.candidates)) from None
    match = _RESOURCE_ID.fullmatch(found["native_id"]) if found else None
    if not match:
        raise Refused(f"No stored resource matches {resource!r}. Look it up with azure_inventory first.")
    out = spend(timeframe, "resource", subscription=match.group(1), resource=found["native_id"])
    out["question"], out["resource"] = "item", found["name"]
    if not out["rows"]:
        out["notes"].insert(0, "No cost row came back for this resource in that period. It may have cost nothing, or "
                               "the data may not have arrived yet (it can take up to 72 hours).")
    return out


def forecast(subscription: str = "", resource_group: str = "") -> dict:
    """The month's actual cost so far and Azure's forecast for the rest of it. Never presented as a fact."""
    now = _now()
    start, end = now.date() - _dt.timedelta(days=FORECAST_HISTORY_DAYS), _month_end(now.date())
    month = now.date().strftime("%Y-%m")
    scopes, skipped, stored = _scopes(subscription, resource_group)
    got, missing = _each(scopes, skipped, lambda s: _fetch(
        _key("forecast", s, start, end), TTL, lambda: _forecast(s, start, end)))
    sums: dict[str, dict[str, float]] = {}
    history, included, none = [], [], []
    for scope, g in got:
        days = g.body["days"]
        if not any(d[2] == "forecast" for d in days):
            none.append({"subscription": scope.name, "reason": "Azure returned no forecast (usually too little history)"})
            continue
        included.append((scope, g))
        history.append(sum(1 for d in days if d[2] == "actual"))
        for day, cost, status, currency in days:
            if day.startswith(month):
                sums.setdefault(currency, {"actual": 0.0, "forecast": 0.0})["forecast" if status == "forecast" else "actual"] += cost
    if not included:
        raise CostUnavailable("; ".join(n["reason"] for n in none) or "Azure returned no forecast.")
    missing["not_included"] += none
    types = agreement()
    totals = [{"currency": c or "not reported", "actual_to_date": round(v["actual"], 2),
               "forecast_rest_of_month": round(v["forecast"], 2), "projected_month_total": round(v["actual"] + v["forecast"], 2)}
              for c, v in sorted(sums.items())]
    weak = min(history) < FORECAST_HISTORY_DAYS
    out = {"question": "forecast", "kind": "FORECAST, a projection and not a charge", "month": month, "totals": totals,
           "history_days": min(history), "weak": weak,
           "scope": _scope_report(included, missing, stored, resource_group),
           "agreement": ", ".join(types) or "not visible to this application",
           "fetched_at": min(g.at for _, g in included).isoformat(timespec="seconds")}
    out["notes"] = _notes(FORECAST + (f" It rests on {out['history_days']} days of history, which is weak (under "
                                      f"{FORECAST_HISTORY_DAYS})." if weak else f" It rests on {out['history_days']} days of history."),
                          types=types)
    out["headline"] = (f"FORECAST for {month} (UTC), actual cost before tax, "
                       f"{' and '.join(t['currency'] for t in totals)}; a projection, not a charge; fetched {out['fetched_at']}.")
    return out


def budgets(subscription: str = "", resource_group: str = "") -> dict:
    """Budgets and cost alerts of the scope, and which subscriptions have no budget."""
    scopes, skipped, stored = _scopes(subscription, resource_group)
    got, missing = _each(scopes, skipped, lambda s: _fetch(_key("budgets", s), TTL, lambda: _budgets(s)))
    types = agreement()
    out = {"question": "budgets", "subscriptions": [
        {"subscription": s.name, "budgets": g.body["budgets"], "alerts": g.body["alerts"], "data": g.source if g.source != "stale" else _STALE,
         "fetched_at": g.at.isoformat(timespec="seconds")} for s, g in got],
        "without_budget": [s.name for s, g in got if not g.body["budgets"]],
        "scope": _scope_report(got, missing, stored, resource_group, any(g.body["truncated"] for _, g in got)),
        "agreement": ", ".join(types) or "not visible to this application",
        "fetched_at": min(g.at for _, g in got).isoformat(timespec="seconds")}
    out["notes"] = _notes(BUDGET, types=types)
    out["headline"] = f"Azure budgets and cost alerts, fetched {out['fetched_at']}."
    return out


def _key(kind: str, scope: Scope, *parts: Any) -> str:
    return "|".join([kind, scope.subscription, scope.group, *map(str, parts)])


def ask(question: str, *, scope: str = "", resource_group: str = "", timeframe: str = "", group_by: str = "",
        resource: str = "") -> dict:
    """The answer the tool and the screen both give. Raises `Refused` (a bad argument) or `CostUnavailable`."""
    if question not in QUESTIONS:
        raise Refused(f"question must be one of: {', '.join(QUESTIONS)}.")
    if question == "spend":
        out = spend(timeframe or "month_to_date", group_by or "service", scope, resource_group)
    elif question == "item":
        if not resource.strip():
            raise Refused("resource is needed: a resource name or ID from the inventory.")
        out = item(resource.strip(), timeframe or "last_month")
    elif question == "forecast":
        out = forecast(scope, resource_group)
    else:
        out = budgets(scope, resource_group)
    return _clean(out)


def _clean(value: Any) -> Any:
    """Names, resource groups, tag values and descriptions came from Azure: the redactor sees every string."""
    if isinstance(value, str):
        return redact.redact_text(value)[0]
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


# --- the link from a waste finding ---------------------------------------------------------------------------------------------------

def annotate(findings: list[dict]) -> None:
    """Put `cost_last_month` (`cost`, `currency`, `period`, `fetched_at`) on each finding whose resource has a row in an
    answer already kept for last month. Reads the cache only: a waste list never makes Azure a call."""
    ids = {f["native_id"] for f in findings if _RESOURCE_ID.fullmatch(str(f.get("native_id") or ""))}
    if not ids:
        return
    start, end = period("last_month", _now())
    seen: dict[str, dict] = {}
    with db.transaction(db.NO_SCOPE) as conn, conn.cursor(row_factory=dict_row) as cur:
        for sub in sorted({i.split("/")[2] for i in ids}):
            cur.execute("SELECT body, fetched_at FROM cloud_cost_cache WHERE key LIKE %s ORDER BY fetched_at",
                        (f"query|{sub}|%|{start}|{end}|resource|%",))
            for row in cur.fetchall():
                for label, cost, currency in row["body"].get("rows") or []:
                    seen[label.lower()] = {"cost": cost, "currency": currency or "not reported", "fetched_at": row["fetched_at"].isoformat(timespec="seconds")}
    for f in findings:
        hit = seen.get(str(f.get("native_id") or "").lower())
        if hit:
            f["cost_last_month"] = {**hit, "period": f"{start} to {end} (UTC), actual cost before tax; credits not deducted"}
