"""Live Azure reads: the named reads in `reads.yaml`, and the one place that sends them.

A read is one Azure Resource Manager call on a resource that is in the stored inventory. The address is the
Resource Manager host + the stored resource ID (or its subscription, or the Network Watcher of its region, found in the
inventory) + a constant suffix from the table; the model supplies a read name, a resource and typed arguments, never
a URL, a path, a method or a body. A POST is allowed only for a probe: an entry whose role is `diagnostics` (Network
Watcher checks, effective routes, BGP status), which asks Azure a question and changes nothing. Everything else is a
GET. `NEVER` names the suffixes that are refused whatever the table says (code execution, shared keys, secrets,
publishing files, packet capture, every stop-like call); the loader refuses an entry that matches it and a test
fails too.

What comes back is reduced to the entry's `keep` allow-list (`shape.py`'s path language), every string passes
`shape._safe` (secret-shaped values and the redactor), lists are cut at `MAX_ROWS` and the whole at `MAX_RESULT`.
Nothing is stored: a live answer is shown, not kept. Redirects are never followed; a 202 `Location` is followed (polled)
only on the same host; a 429 is waited out twice and then paused for everyone. Nothing raised here carries the token or
Azure's own error text: a message is this plugin's sentence plus the HTTP status.

This module is a separate path from `client.py` (which stays three calls, ADR 0056) and from `cost.py` and
`prices.py`; it signs in through `client.token()` and uses the host `client._cloud().arm`. Answer shapes are
`# unverified:` until test session 3 (build contract phase 4).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import ipaddress
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import quote, urlencode

import requests
import yaml

from plugins._ea_azure.helpers import client, config, shape, store

READS_FILE = Path(__file__).resolve().parents[1] / "reads.yaml"
READ_APPROVAL = ("every_time", "once_per_chat", "log_only")
WATCHER = "microsoft.network/networkwatchers"

#: Suffixes no entry may carry, compared lower-cased as substrings of the entry's suffix. The first block is the
#: build contract's list; the rest is the same idea made wider (anything that lists secrets, writes, or starts,
#: stops or moves something). Tightening is free; loosening is a security change.
NEVER = (
    "/runCommand", "/runCommands", "/sharedkey", "/listKeys", "/publishxml", "config/appsettings/list",
    "config/connectionstrings/list", "/retrieveBootDiagnosticsData", "/packetCaptures", "/troubleshoot",
    "/vpndeviceconfigurationscript", "/listRadiusSecrets", "/generatevpnprofile",
    "/deallocate", "/powerOff", "/restart", "/stop", "/redeploy", "/reimage", "/reset", "/failover",
    "/list", "/publishingcredentials", "/containerlogs", "/processes", "/logstream", "/serialconsole", "/getvpnclient",
    "/packetCapture", "/start", "/scale", "/swap", "/slotsswap", "/delete", "/write", "/purge", "/export", "/regenerate",
)
#: Words that may not appear in any `keep` path: a secret is never on the allow-list, by name.
FORBIDDEN_KEEP = ("sharedkey", "password", "secret", "connectionstring", "customdata", "osprofile", "accesskey",
                  "primarykey", "secondarykey", "userdata", "environmentvariables", "publishingpassword")

MAX_ANSWER = 4 * 1024 * 1024
MAX_ROWS = 200
MAX_RESULT = 64 * 1024
POLL_SECONDS = 60
MAX_WAIT = 30  # the longest one 429 pause is waited out in a call

_ARG_TYPES = ("ip", "port", "enum", "enum_list", "duration")
_COMPUTED = {"resource", "subscription"}
_TIME_PLACEHOLDERS = {"start", "end", "timespan", "interval"}
_PLACE = re.compile(r"\{([a-z_]+)(:int)?\}")
_NAME = re.compile(r"[a-z][a-z0-9_]{2,40}")
_API = re.compile(r"\d{4}-\d{2}-\d{2}(?:-preview)?")
_SUFFIX = re.compile(r"(?:/[A-Za-z0-9_.]+)*")
_TYPE = re.compile(r"[a-z0-9.]+(?:/[a-z0-9]+)+")
_QUERY_KEY = re.compile(r"\$?[A-Za-z][A-Za-z0-9]*")
_DURATION = re.compile(r"P(?:(\d{1,3})D)?(?:T(?:(\d{1,3})H)?(?:(\d{1,4})M)?)?")
_ID = re.compile(r"/subscriptions/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:/[A-Za-z0-9_.()-]{1,90}){1,40}")

_sleep = time.sleep
_clock = time.monotonic
_now = lambda: _dt.datetime.now(_dt.timezone.utc)  # noqa: E731
_blocked_until = 0.0  # a monotonic time before which no live read is made


class ReadsInvalid(Exception):
    """`reads.yaml` is not usable; the message says which entry and why."""


class Refused(ValueError):
    """A read, resource or argument that is not allowed; the message is the sentence to give back."""


class ReadUnavailable(Exception):
    """Azure could not be asked or would not answer; the message is this plugin's own sentence, never Azure's text."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Entry:
    name: str
    summary: str
    resource_type: str
    scope: str
    method: str
    suffix: str
    api_version: str
    role: str
    requires: str
    args: dict[str, dict[str, Any]]
    query: dict[str, str]
    body: Any
    keep: Any
    cost_note: str


class Target(NamedTuple):
    native_id: str
    name: str
    type: str
    region: str
    group: str
    subscription: str  # the GUID
    base: str  # what the suffix is added to


class Call(NamedTuple):
    method: str
    url: str
    body: Any


# --- the table -------------------------------------------------------------------------------------------------------

def _spec(raw: Any, where: str) -> Any:
    """`keep` as `shape._pick` reads it: a path, a nested mapping, or `(list path, mapping)` for `each:`."""
    if isinstance(raw, str):
        if not raw or any(w in raw.lower() for w in FORBIDDEN_KEEP):
            raise ReadsInvalid(f"{where}: `{raw}` is not a path this table may keep.")
        return raw
    if not isinstance(raw, dict) or not raw:
        raise ReadsInvalid(f"{where}: keep needs paths or mappings.")
    if "each" in raw:
        path, sub = raw.get("each"), raw.get("keep")
        if set(raw) != {"each", "keep"} or not isinstance(path, str) or not isinstance(sub, dict):
            raise ReadsInvalid(f"{where}: `each` takes a list path and a `keep` mapping, nothing else.")
        _spec(path, where)
        return (path if path.endswith("[]") else path + "[]", _spec(sub, where))
    return {str(k): _spec(v, f"{where}.{k}") for k, v in raw.items()}


def _placeholders(node: Any) -> list[tuple[str, bool]]:
    """Every `{name}` in a query or body template, and whether it is the whole string."""
    if isinstance(node, str):
        return [(m[1], bool(_PLACE.fullmatch(node))) for m in _PLACE.finditer(node)]
    if isinstance(node, dict):
        return [p for v in node.values() for p in _placeholders(v)]
    if isinstance(node, list):
        return [p for v in node for p in _placeholders(v)]
    return []


def _entry(raw: Any) -> Entry:
    if not isinstance(raw, dict):
        raise ReadsInvalid("every entry must be a mapping.")
    name = str(raw.get("name") or "")
    if not _NAME.fullmatch(name):
        raise ReadsInvalid(f"`{name}` is not a read name (lower-case letters, digits and _).")

    def bad(why: str) -> ReadsInvalid:
        return ReadsInvalid(f"{name}: {why}")

    method, role, scope = str(raw.get("method") or ""), str(raw.get("role") or ""), str(raw.get("scope") or "resource")
    if (method, role) not in (("GET", "reader"), ("POST", "diagnostics")):
        raise bad("a GET is a `reader` read and a POST is a `diagnostics` probe; nothing else is allowed.")
    if scope not in ("resource", "subscription", "watcher"):
        raise bad("scope is resource, subscription or watcher.")
    suffix = str(raw.get("suffix") if raw.get("suffix") is not None else "x")
    if not _SUFFIX.fullmatch(suffix) or ".." in suffix:
        raise bad(f"suffix `{suffix}` is not a plain path.")
    hit = next((n for n in NEVER if n.lower() in suffix.lower()), "")
    if hit:
        raise bad(f"suffix `{suffix}` matches `{hit}`, which is refused for every read.")
    rtype = str(raw.get("resource_type") or "")
    if rtype != "*" and not _TYPE.fullmatch(rtype):
        raise bad("resource_type is a lower-case Azure type or `*`.")
    if not _API.fullmatch(str(raw.get("api_version") or "")):
        raise bad("api_version is a date such as 2025-09-01 (or a -preview one).")
    if not str(raw.get("requires") or "").strip() or not str(raw.get("summary") or "").strip():
        raise bad("summary and requires are needed: the card and a 403 say them.")
    args = raw.get("args") or {}
    for arg, s in args.items():
        if not _NAME.fullmatch(f"x_{arg}") or not isinstance(s, dict) or s.get("type") not in _ARG_TYPES:
            raise bad(f"argument `{arg}` needs a type from {', '.join(_ARG_TYPES)}.")
        if s["type"] in ("enum", "enum_list") and not s.get("values"):
            raise bad(f"argument `{arg}` needs its `values`.")
    query, body = raw.get("query") or {}, raw.get("body")
    if body is not None and method != "POST":
        raise bad("only a probe has a body.")
    if not all(_QUERY_KEY.fullmatch(str(k)) and isinstance(v, str) for k, v in query.items()):
        raise bad("query keys are plain names and values are strings.")
    seen = {p for p, _ in _placeholders(query) + _placeholders(body)}
    known = set(args) | _COMPUTED | (_TIME_PLACEHOLDERS if any(s["type"] == "duration" for s in args.values()) else set())
    if seen - known:
        raise bad(f"`{', '.join(sorted(seen - known))}` is not an argument or a computed value.")
    for arg, s in args.items():
        used = seen & _TIME_PLACEHOLDERS if s["type"] == "duration" else {arg} & seen
        if not used:
            raise bad(f"argument `{arg}` is not used by the query or the body.")
    optional = {a for a, s in args.items() if s.get("optional") and "default" not in s}
    if any(p in optional and not whole for p, whole in _placeholders(query) + _placeholders(body)):
        raise bad("an optional argument may only be a whole value.")
    return Entry(
        name=name, summary=str(raw["summary"]).strip(), resource_type=rtype, scope=scope, method=method,
        suffix=suffix, api_version=str(raw["api_version"]), role=role, requires=str(raw["requires"]).strip(),
        args=args, query={str(k): v for k, v in query.items()}, body=body,
        keep=_spec(raw.get("keep"), f"{name}.keep"), cost_note=str(raw.get("cost_note") or "").strip(),
    )


def parse(text: str) -> dict[str, Entry]:
    """The table in `text`, checked; `ReadsInvalid` names the first entry that is not allowed."""
    try:
        rows = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise ReadsInvalid(f"reads.yaml does not parse: {error}") from None
    if not isinstance(rows, list) or not rows:
        raise ReadsInvalid("reads.yaml must be a list of reads.")
    out: dict[str, Entry] = {}
    for raw in rows:
        if isinstance(raw, dict) and raw.get("kind") == "kql":
            continue  # a log query: `logs.py` loads those
        entry = _entry(raw)
        if entry.name in out:
            raise ReadsInvalid(f"{entry.name} is listed twice.")
        out[entry.name] = entry
    return out


def load() -> dict[str, Entry]:
    return parse(READS_FILE.read_text(encoding="utf-8"))


def commit() -> str:
    """Which version of the table a request was made against; an approval does not survive an edit of the table."""
    return hashlib.sha256(READS_FILE.read_bytes()).hexdigest()[:40]


def read_approval() -> str:
    """`every_time` (default), `once_per_chat` or `log_only`. An unknown value asks every time: a typo must not loosen it."""
    value = config.get_config().get("read_approval")
    return value if value in READ_APPROVAL else READ_APPROVAL[0]


def catalogue() -> str:
    """One line per read: its name, role, summary and arguments, for the agent when it names one that does not exist."""
    lines = []
    for e in sorted(load().values(), key=lambda e: e.name):
        needs = ", ".join(f"{a}" + ("?" if s.get("optional") or "default" in s else "") for a, s in e.args.items())
        lines.append(f"- {e.name} ({e.resource_type}; {e.role}): {e.summary}" + (f" Arguments: {needs}." if needs else ""))
    return "\n".join(lines)


# --- arguments -------------------------------------------------------------------------------------------------------

def _seconds(text: str) -> int:
    m = _DURATION.fullmatch(text.strip().upper())
    if not m:
        raise Refused(f"`{text}` is not a duration; use an ISO one such as PT30M, PT6H or P1D.")
    d, h, mi = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60


def _check(name: str, value: Any, spec: dict[str, Any]) -> str:
    kind, text = spec["type"], str(value).strip()
    if kind == "ip":
        try:
            return str(ipaddress.ip_address(text))
        except ValueError:
            raise Refused(f"{name} must be an IP address such as 10.0.1.4.") from None
    if kind == "port":
        if not re.fullmatch(r"\d{1,5}", text) or int(text) > 65535:
            raise Refused(f"{name} must be a port from 0 to 65535.")
        return str(int(text))
    if kind in ("enum", "enum_list"):
        allowed = {str(v).lower(): str(v) for v in spec["values"]}
        items = [t.strip() for t in (value if isinstance(value, list) else text.split(",")) if str(t).strip()]
        limit = int(spec.get("max") or 5) if kind == "enum_list" else 1
        picked = list(dict.fromkeys(allowed.get(str(i).lower(), "") for i in items))
        if not items or "" in picked or len(picked) > limit:
            raise Refused(f"{name} must be {'at most ' + str(limit) + ' of' if limit > 1 else 'one of'}: {', '.join(allowed.values())}.")
        return ",".join(picked)
    seconds = _seconds(text)
    cap = _seconds(str(spec.get("max") or "P1D"))
    if not 60 <= seconds <= cap:
        raise Refused(f"{name} must be between PT1M and {spec.get('max') or 'P1D'}.")
    return text.upper()


def check_args(entry: Entry, supplied: dict[str, Any]) -> dict[str, str]:
    """The arguments as they will be sent (checked, canonical), or `Refused` with the sentence to give back."""
    extra = sorted(set(supplied) - set(entry.args))
    shown = ", ".join(entry.args) or "none"
    if extra:
        raise Refused(f"{entry.name} takes no argument `{', '.join(extra)}`. Its arguments: {shown}.")
    out: dict[str, str] = {}
    for name, spec in entry.args.items():
        value = supplied.get(name)
        if value in (None, "", []):
            if "default" in spec:
                value = spec["default"]
            elif spec.get("optional"):
                continue
            else:
                raise Refused(f"{entry.name} needs `{name}`. Its arguments: {shown}.")
        out[name] = _check(name, value, spec)
    return out


# --- the resource and the call -----------------------------------------------------------------------------------------

def resolve(entry: Entry, ref: str) -> Target:
    """The stored resource this read is about (by name, native ID or row ID), or `Refused` with the sentence."""
    try:
        row = store.get(ref)
    except store.Ambiguous as error:
        names = "; ".join(f"{r['name']} ({r['type']}, {r['resource_group']})" for r in error.candidates)
        raise Refused(f"`{ref}` matches more than one stored resource: {names}. Use its full resource ID.") from None
    if not row:
        raise Refused(f"`{ref}` is not in the stored Azure inventory, and a live read only targets what the inventory lists. "
                      "Pull the inventory again if it is new.")
    if row["removed_at"]:
        raise Refused(f"`{row['name']}` was removed from Azure (the inventory last saw it {row['last_seen_at']}), so it cannot be read.")
    if entry.resource_type not in ("*", row["type"]):
        raise Refused(f"{entry.name} reads a {entry.resource_type}, but `{row['name']}` is a {row['type']}.")
    found = _ID.fullmatch(row["native_id"])
    if not found:
        raise Refused(f"`{row['name']}` has an ID with characters this product will not put in an address, so it cannot be read.")
    sub = found[1]
    base = row["native_id"]
    if entry.scope == "subscription":
        base = f"/subscriptions/{sub}"
    elif entry.scope == "watcher":
        watchers = store.search({"type": WATCHER, "region": row["region"], "subscription": sub}, limit=3)[0]
        if not watchers or not _ID.fullmatch(watchers[0]["native_id"]):
            raise Refused(f"There is no Network Watcher in {row['region']} in subscription {sub} in the stored inventory. Azure "
                          "creates one with the first virtual network in a region; pull the inventory again if it exists.")
        base = watchers[0]["native_id"]
    return Target(row["native_id"], row["name"], row["type"], row["region"], row["resource_group"]["name"], sub, base)


def _values(entry: Entry, target: Target, args: dict[str, str], real: bool) -> dict[str, str]:
    values = {**args, "resource": target.native_id, "subscription": target.subscription}
    duration = next((args[a] for a, s in entry.args.items() if s["type"] == "duration"), "")
    if duration:
        seconds = _seconds(duration)
        interval = "PT1M" if seconds <= 3600 else "PT5M" if seconds <= 21600 else "PT15M"
        if real:
            end = _now().replace(microsecond=0)
            start, end = (end - _dt.timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ"), end.strftime("%Y-%m-%dT%H:%M:%SZ")
        else:  # the card: the time is set when it runs, so it cannot be part of what was approved
            start, end = f"<now minus {duration}>", "<now>"
        values.update(start=start, end=end, timespan=f"{start}/{end}", interval=interval)
    return values


_MISSING = object()


def _fill(node: Any, values: dict[str, str]) -> Any:
    if isinstance(node, str):
        whole = _PLACE.fullmatch(node)
        if whole:
            if whole[1] not in values:
                return _MISSING  # an optional argument that was not given
            return int(values[whole[1]]) if whole[2] else values[whole[1]]
        return _PLACE.sub(lambda m: str(values[m[1]]), node)
    if isinstance(node, dict):
        return {k: v for k, v in ((k, _fill(v, values)) for k, v in node.items()) if v is not _MISSING}
    if isinstance(node, list):
        return [v for v in (_fill(v, values) for v in node) if v is not _MISSING]
    return node


def _build(entry: Entry, target: Target, args: dict[str, str], real: bool) -> tuple[str, dict[str, str], Any]:
    values = _values(entry, target, args, real)
    query = {"api-version": entry.api_version, **_fill(entry.query, values)}
    body = _fill(entry.body, values) if entry.body is not None else None
    return f"{client._cloud().arm}{target.base}{entry.suffix}", query, body


def describe(entry: Entry, target: Target, args: dict[str, str]) -> list[str]:
    """The exact request, as lines for the card and the record: method and address, then the body of a probe."""
    path, query, body = _build(entry, target, args, real=False)
    lines = [f"{entry.method} {path}?" + "&".join(f"{k}={v}" for k, v in query.items())]
    if body is not None:
        lines += json.dumps(body, indent=2).splitlines()
    return lines


def build(entry: Entry, target: Target, args: dict[str, str]) -> Call:
    path, query, body = _build(entry, target, args, real=True)
    return Call(entry.method, f"{path}?{urlencode(query, quote_via=quote)}", body)


# --- sending ---------------------------------------------------------------------------------------------------------

def _refused(entry: Entry, status: int) -> ReadUnavailable:
    if status == 403:
        more = " It is part of the Rezztor Diagnostics role." if entry.role == "diagnostics" else " The Reader role normally has it."
        return ReadUnavailable(f"The Azure role lacks {entry.requires} (HTTP 403)." + more, status)
    if status == 404:
        return ReadUnavailable("Azure has no such resource or answer at this address (HTTP 404): it may have been removed since "
                               "the last pull, or this type does not offer this read.", status)
    if status == 400:
        return ReadUnavailable("Azure did not accept the read (HTTP 400): the resource may be in a state that does not allow it "
                               "(a check on a stopped virtual machine, for example) or an argument does not fit it.", status)
    if status == 401:
        return ReadUnavailable("Azure did not accept the sign-in (HTTP 401).", status)
    if status >= 500:
        return ReadUnavailable(f"Azure had a problem answering (HTTP {status}). Try again in a minute.", status)
    return ReadUnavailable(f"Azure could not answer the read (HTTP {status}).", status)


def _http(method: str, url: str, body: Any) -> requests.Response:
    headers = {"Authorization": f"Bearer {client.token()}"}
    timeout = config.get_config()["timeout_seconds"]
    if method == "POST":
        return requests.post(url, json=body, headers=headers, timeout=timeout, allow_redirects=False)
    return requests.get(url, headers=headers, timeout=timeout, allow_redirects=False)


def _exchange(method: str, url: str, body: Any) -> requests.Response:
    """One authorised call: signed in again once on a 401, a 429 waited out twice and then paused for everyone."""
    global _blocked_until
    if _clock() < _blocked_until:
        raise ReadUnavailable(f"Azure asked this product to wait about {int(_blocked_until - _clock()) + 1} seconds before the next live read.", 429)
    for attempt in range(3):
        try:
            response = _http(method, url, body)
        except client.AzureError as error:
            raise ReadUnavailable(str(error)) from None
        except requests.RequestException:
            raise ReadUnavailable("Azure could not be reached.") from None
        if response.status_code == 401 and attempt == 0:
            client.reset()
            continue
        if response.status_code == 429:
            wait = min(client._wait(response), MAX_WAIT)
            if attempt < 2:
                _sleep(wait)
                continue
            _blocked_until = _clock() + wait
            raise ReadUnavailable(f"Azure is limiting requests (HTTP 429) and still refused after three tries. Try again in about {int(wait)} seconds.", 429)
        return response
    raise ReadUnavailable("Azure did not answer the read.")  # unreachable: the last attempt returns or raises


def _answer(entry: Entry, call: Call) -> Any:
    """Send, follow a 202's `Location` on the same host until it answers, and return the parsed object."""
    response = _exchange(call.method, call.url, call.body)
    deadline = _clock() + POLL_SECONDS
    while response.status_code == 202:
        where = response.headers.get("Location") or ""
        if not where:
            raise ReadUnavailable("Azure accepted the read but gave no address to ask for the answer.", 202)
        if not where.startswith(f"{client._cloud().arm}/"):
            raise ReadUnavailable("Azure pointed the answer at another address; it was not followed.", 202)
        wait = min(client._seconds(response.headers.get("Retry-After")) or 2, 10)
        if _clock() + wait > deadline:
            raise ReadUnavailable(f"Azure had not finished after {POLL_SECONDS} seconds. Try again later.", 202)
        _sleep(wait)
        response = _exchange("GET", where, None)
    status = response.status_code
    if 300 <= status < 400:
        raise ReadUnavailable(f"Azure answered with a redirect (HTTP {status}) to another address; it was not followed.", status)
    if status == 204:
        return {}
    if status != 200:
        raise _refused(entry, status)
    if len(response.content) > MAX_ANSWER:
        raise ReadUnavailable("Azure's answer is too large to read; ask for a shorter period or a smaller resource.", 200)
    try:
        body = response.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise ReadUnavailable("Azure answered with something this plugin cannot read.", 200)
    return body


# --- the answer ------------------------------------------------------------------------------------------------------

def _mask(value: Any, cut: list[bool]) -> Any:
    if isinstance(value, str):
        return shape._safe(value)
    if isinstance(value, list):
        if len(value) > MAX_ROWS:
            cut[0] = True
        return [_mask(v, cut) for v in value[:MAX_ROWS]]
    if isinstance(value, dict):
        return {k: _mask(v, cut) for k, v in value.items()}
    return value


def reduce(entry: Entry, body: dict) -> dict[str, Any]:
    """Only what `keep` allows, every string masked, lists cut. `more` says Azure had another page that was not read."""
    cut = [False]
    result = _mask(shape._pick(entry.keep, body, body) or {}, cut)
    if len(json.dumps(result)) > MAX_RESULT:
        raise ReadUnavailable("The answer is larger than 64 KB; ask for a shorter period or fewer metrics.", 200)
    more = any(isinstance(v, str) and v for v in shape._walk(body, ["nextLink"]))
    return {"result": result, "truncated": cut[0] or more}


def rows(result: dict[str, Any]) -> int:
    """How many list items the answer holds, for the audit line (never the answer itself)."""
    return sum(len(v) for v in result.values() if isinstance(v, list))


def read(name: str, native_id: str, args: dict[str, Any]) -> dict[str, Any]:
    """Run one named read on one stored resource now. `Refused` for what is not allowed, `ReadUnavailable` when Azure
    cannot answer. The resource and the arguments are checked again here, whoever approved them."""
    entry = load().get(name)
    if entry is None:
        raise Refused(f"There is no read named `{name}`.")
    target = resolve(entry, native_id)
    call = build(entry, target, check_args(entry, args))
    answer = reduce(entry, _answer(entry, call))
    return {"read": entry.name, "resource": target.native_id, "status": 200, "at": _now().isoformat(timespec="seconds"), **answer,
            "rows": rows(answer["result"])}
