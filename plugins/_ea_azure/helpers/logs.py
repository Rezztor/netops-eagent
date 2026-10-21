"""Log Analytics queries: the named KQL reads in `reads.yaml` (`kind: kql`) and the guarded free-form query.

One call, `POST https://api.loganalytics.azure.com/v1/workspaces/{guid}/query`, signed in as the log app (key D, a
certificate of its own, audience `https://api.loganalytics.io`). The workspace comes from `logs.workspaces` in this
plugin's settings and nowhere else; the model names one, never a GUID or a URL. `guard` is the whole defence of a
free-form query and is the only way text reaches Azure: one tabular statement after optional `let`s, tables only from
`logs.tables`, no dot command, no other workspace, cluster, database or external data, no function body, and
`| take 201` after it. It checks tokens, not substrings (a string holding a refused word is a string), and what it
returns is the text it checked with comments removed, so the text Azure runs is the text the guard read.
Loosening it is a security change: extend `tests/test_azure_logs_guard.py` first.

What comes back is cut to 200 rows, 512 characters a cell and 64 KB; identity columns are dropped, query strings are
cut from URLs and every cell passes `shape._safe`. Nothing is stored. A query's time range is always sent
(`timespan`, default PT1H, at most `logs.max_timespan`). Redirects are never followed; one query per chat at a time.

Documented, not run against a tenant (`# unverified:` until test sessions 3 and 4): the request and response shapes
(learn.microsoft.com/azure/azure-monitor/logs/api/request-format, .../response-format, .../timeouts) and the columns of
the Azure Firewall tables (.../reference/tables/azfwnetworkrule, azfwapplicationrule).
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

import requests
import yaml

from plugins._ea_azure.helpers import client, config, credential, live, shape

HOST = "https://api.loganalytics.azure.com"
SCOPE = "https://api.loganalytics.io/.default"
FREE = "kql"  # the `playbook_name` of a free-form query
TAKE = "\n| take 201"
MAX_KQL = 4000
MAX_ROWS = 200
MAX_CELL = 512
MAX_RESULT = 64 * 1024
MAX_ANSWER = 4 * 1024 * 1024
WAIT = 60  # `Prefer: wait=`: the longest Azure may take, in seconds
DEFAULT_TIMESPAN = "PT1H"
DEFAULT_MAX_TIMESPAN = "P7D"
HARD_MAX_TIMESPAN = "P30D"
MAX_PAUSE = 30

#: Tables a query may read until an administrator says otherwise: network and platform diagnostics. Identity tables
#: (SigninLogs, AuditLogs, SecurityEvent, Event) and AzureActivity (who did what) are never on it.
DEFAULT_TABLES = (
    "AzureDiagnostics", "AzureMetrics", "AZFWNetworkRule", "AZFWApplicationRule", "AZFWNatRule", "AZFWDnsQuery",
    "AZFWThreatIntel", "AZFWIdpsSignature", "NTANetAnalytics", "Heartbeat", "Perf",
)
#: Columns that name or locate a person or carry a token: never returned, and a query that names one is refused.
IDENTITY = ("Claims", "HTTPRequest", "Authorization", "CsUsername", "CsUriQuery", "UserAgent", "CallerIpAddress")
#: Columns whose value is a URL: the query string is cut off (it can carry a token or a user's search).
URL_COLUMNS = ("TargetUrl", "CsUriStem")

_WORDS = {"externaldata", "external_table", "evaluate", "invoke", "find", "search", "materialized"}
_CALLS = {"adx", "arg", "workspace", "app", "resource", "cluster", "database", "table"}
# Calls that give a column, or every column, without writing its name: the identity rule reads names.
_COLUMN_CALLS = {"column_ifexists", "columnifexists", "pack_all"}
# The words after which `(Name)` is a query and not an argument: `join (T)`, `x in (T)`, `toscalar(T)`. After any other
# word a bracket is a function call, `count(Computer)`, and its name is a column.
_TABULAR = {"join", "lookup", "union", "on", "with", "fork", "in", "has_any", "has_all", "toscalar", "materialize"}
_GUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_WS_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}")
_TABLE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,99}")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUMBER = re.compile(r"[0-9][A-Za-z0-9_.]*")
_HOLE = re.compile(r"\{([a-z_]+)\}")
_KQL_ARGS = ("ip", "port", "enum", "duration")

_cache: dict = {"key": None, "token": "", "until": 0.0}
_lock = threading.Lock()
_busy_lock = threading.Lock()
_busy: set[str] = set()  # chats with a query in flight
_sleep = time.sleep
_clock = time.monotonic
_now = lambda: _dt.datetime.now(_dt.timezone.utc)  # noqa: E731


# --- settings --------------------------------------------------------------------------------------------------------

def clean(raw: Any, strict: bool = False) -> dict[str, Any]:
    """`logs` as the settings hold it: `client_id`, `workspaces` [{name, id}], `tables`, `max_timespan`.

    Lenient (reading) drops what is wrong and fills defaults, so a damaged setting only narrows what can be asked.
    Strict (saving) raises `ValueError` with the sentence for the administrator."""
    raw = raw if isinstance(raw, dict) else {}

    def bad(message: str) -> None:
        if strict:
            raise ValueError(message)

    client_id = str(raw.get("client_id") or "").strip().lower()
    if client_id and not _GUID.fullmatch(client_id):
        bad("The log application (client) ID must be a GUID.")
        client_id = ""
    workspaces, seen = [], set()
    for item in raw.get("workspaces") or []:
        name = str(item.get("name") or "").strip() if isinstance(item, dict) else ""
        guid = str(item.get("id") or "").strip().lower() if isinstance(item, dict) else ""
        if not _WS_NAME.fullmatch(name) or not _GUID.fullmatch(guid) or name.lower() in seen:
            bad("Each workspace needs a short unique name (letters, digits, . _ -) and its workspace ID (a GUID).")
            continue
        seen.add(name.lower())
        workspaces.append({"name": name, "id": guid})
    tables = DEFAULT_TABLES if "tables" not in raw else raw.get("tables")
    tables = list(dict.fromkeys(str(t).strip() for t in (tables if isinstance(tables, (list, tuple)) else []) if str(t).strip()))
    if any(not _TABLE.fullmatch(t) for t in tables):
        bad("A table name is letters, digits and _, starting with a letter.")
        tables = [t for t in tables if _TABLE.fullmatch(t)]
    span = str(raw.get("max_timespan") or DEFAULT_MAX_TIMESPAN).strip().upper()
    try:
        ok = 3600 <= live._seconds(span) <= live._seconds(HARD_MAX_TIMESPAN)
    except live.Refused:
        ok = False
    if not ok:
        bad(f"The longest time range is between PT1H and {HARD_MAX_TIMESPAN}, for example P7D.")
        span = DEFAULT_MAX_TIMESPAN
    return {"client_id": client_id, "workspaces": workspaces, "tables": tables, "max_timespan": span}


def settings() -> dict[str, Any]:
    return clean(config.get_config().get("logs"))


def is_configured() -> bool:
    s = settings()
    return bool(config.get_config()["tenant_id"] and s["client_id"] and s["workspaces"] and credential.logs_status()["set"])


NOT_SET_UP = ("Log queries are not set up. An administrator must save the log application's ID, its certificate and at least "
              "one workspace in Settings, Azure.")


# --- the guard -------------------------------------------------------------------------------------------------------

class _Bad(Exception):
    """A refusal; the message is the sentence given back."""


Token = tuple[str, str]  # (kind: id | num | str | op, text)


def _string_end(text: str, i: int, verbatim: bool) -> int:
    quote, j = text[i], i + 1
    while j < len(text):
        ch = text[j]
        if ch == "\n":
            break
        if ch == quote:
            if verbatim and text[j + 1:j + 2] == quote:
                j += 2
                continue
            return j + 1
        j += 2 if ch == "\\" and not verbatim else 1
    raise _Bad("A string is not closed on its line. Close the quote, or write the text on one line.")


def _lex(text: str) -> tuple[list[Token], str]:
    """Tokens and the text with comments removed. Strings are kept exactly as written."""
    tokens: list[Token] = []
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            out.append(c)
            i += 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith(("```", "~~~"), i):
            end = text.find(text[i:i + 3], i + 3)
            if end < 0:
                raise _Bad("A multi-line string is not closed.")
            tokens.append(("str", text[i:end + 3]))
            out.append(text[i:end + 3])
            i = end + 3
        elif c in "\"'" or (c == "@" and text[i + 1:i + 2] in ("\"", "'")):
            start = i + 1 if c == "@" else i
            end = _string_end(text, start, verbatim=c == "@")
            tokens.append(("str", text[i:end]))
            out.append(text[i:end])
            i = end
        elif _IDENT.match(c):
            word = _IDENT.match(text, i)[0]
            if word in ("h", "H") and text[i + 1:i + 2] in ("\"", "'"):  # an obfuscated string literal: h'...'
                end = _string_end(text, i + 1, verbatim=False)
                tokens.append(("str", text[i:end]))
                out.append(text[i:end])
                i = end
            else:
                tokens.append(("id", word))
                out.append(word)
                i += len(word)
        elif c.isdigit():
            word = _NUMBER.match(text, i)[0]
            tokens.append(("num", word))
            out.append(word)
            i += len(word)
        elif ord(c) > 126:
            raise _Bad("Only plain ASCII characters may appear outside a string; this query has others.")
        else:
            tokens.append(("op", c))
            out.append(c)
            i += 1
    return tokens, "".join(out)


def _unquote(literal_text: str) -> str:
    body = literal_text[1:] if literal_text[0] in "@hH" else literal_text
    return body[3:-3] if body.startswith(("```", "~~~")) else body[1:-1]


class _Scan:
    """The position rules over one statement's tokens: where a table can be named, it must be an allowed one."""

    def __init__(self, tokens: list[Token], allowed: tuple[str, ...], lets: set[str]):
        self.t, self.allowed, self.lets = tokens, allowed, lets

    def at(self, i: int) -> Token:
        return self.t[i] if 0 <= i < len(self.t) else ("end", "")

    def name(self, i: int) -> tuple[str, int] | None:
        """A table name at `i`: a plain word or `['quoted']`, and the index after it."""
        if self.at(i)[0] == "id":
            return self.t[i][1], i + 1
        if self.at(i) == ("op", "[") and self.at(i + 1)[0] == "str" and self.at(i + 2) == ("op", "]"):
            return _unquote(self.t[i + 1][1]), i + 3
        return None

    def check(self, name: str) -> None:
        if name not in self.allowed and name not in self.lets:
            shown = ", ".join(self.allowed) or "none"
            raise _Bad(f"`{name}` is not one of the tables this product may query ({shown}). Table names are case-sensitive.")

    def parens_end(self, i: int) -> int:
        depth = 0
        for j in range(i, len(self.t)):
            depth += (self.at(j) == ("op", "(")) - (self.at(j) == ("op", ")"))
            if depth == 0:
                return j + 1
        raise _Bad("Parentheses are not balanced.")

    def table_list(self, i: int, union: bool) -> None:
        """What follows `join`, `lookup` or `union`: options, then the table(s). A parenthesised query is checked by `scan`."""
        while self.at(i)[0] == "id" and self.at(i + 1) == ("op", "="):
            i += 3  # kind=inner, withsource=T, isfuzzy=true
        if self.at(i)[1].lower() == "hint" and self.at(i + 1) == ("op", "."):
            i += 5
        while True:
            if self.at(i) == ("op", "("):
                # `join kind=inner (Table)`: the word before the bracket is an option, so `scan` reads it as a call.
                inner = i
                while self.at(inner) == ("op", "("):
                    inner += 1
                got = self.name(inner)
                if got and self.at(got[1]) == ("op", ")"):
                    self.check(got[0])
                i = self.parens_end(i)
            elif self.at(i) == ("op", "*"):
                raise _Bad("A union takes table names, not wildcards.")
            else:
                got = self.name(i)
                if not got:
                    return
                name, i = got
                if self.at(i) == ("op", "*"):
                    raise _Bad("A union takes table names, not wildcards.")
                self.check(name)
            if union and self.at(i) == ("op", ","):
                i += 1
                continue
            return

    def scan(self) -> None:
        for i, (kind, text) in enumerate(self.t):
            low = text.lower()
            if text == "(" and (got := self.name(i + 1)) and self.at(got[1]) == ("op", "|"):
                self.check(got[0])  # (Table | ...): a subquery, wherever it sits
            elif text == "(" and (got := self.name(i + 1)) and self.at(got[1]) == ("op", ")") \
                    and (self.at(i - 1)[0] != "id" or self.at(i - 1)[1].lower() in _TABULAR):
                self.check(got[0])  # (Table): the whole of a query, unless the bracket is a function call's
            elif kind == "id" and low in ("join", "lookup", "union"):
                self.table_list(i + 1, low == "union")


def _guard(kql: Any, allowed: tuple[str, ...]) -> str:
    if not isinstance(kql, str) or len(kql) > MAX_KQL:
        raise _Bad(f"The query is too long: {MAX_KQL} characters at most.")
    if any(ord(c) < 32 and c not in "\t\r\n" for c in kql):
        raise _Bad("The query holds a control character.")
    tokens, cleaned = _lex(kql)
    if not tokens:
        raise _Bad("There is nothing to run.")
    if any(line.lstrip().startswith(".") for line in cleaned.splitlines()):
        raise _Bad("A line that starts with a dot is a management command (a dot command), and none is accepted.")
    identity = {c.lower() for c in IDENTITY}
    for k, (kind, text) in enumerate(tokens):
        low = text.lower()
        if kind == "op" and text in ("{", "}"):
            raise _Bad("Braces are not accepted: a function body or a dynamic literal can hide a table.")
        if kind == "str" and k and tokens[k - 1] == ("op", "["):  # ['a name']: a column or a table, like a plain word
            after = tokens[k + 1] if k + 1 < len(tokens) else ("end", "")
            if after[0] == "str":
                raise _Bad("A name in brackets must be one string, not pieces put together.")
            if after == ("op", "]") and "\\" in _unquote(text):
                raise _Bad("A name in brackets may not hold a backslash: an escape can spell a name that is refused.")
            if after == ("op", "]") and _unquote(text).lower() in identity:
                raise _Bad(f"`{_unquote(text)}` is identity data (who or where from) and is never returned, so a query may not name it.")
        if kind != "id":
            continue
        if low in _WORDS or (low in _CALLS and k + 1 < len(tokens) and tokens[k + 1] == ("op", "(")):
            raise _Bad(f"`{text}` is not accepted: it can read another workspace, cluster, database, external data or every table.")
        if low in _COLUMN_CALLS and k + 1 < len(tokens) and tokens[k + 1] == ("op", "("):
            raise _Bad(f"`{text}` is not accepted: it returns columns without naming them, and some are never returned.")
        if low == "partition":
            raise _Bad("`partition` is not accepted: its subquery follows a column name, so where a table is named in it cannot be told.")
        if low in identity:
            raise _Bad(f"`{text}` is identity data (who or where from) and is never returned, so a query may not name it.")
    statements, current, depth = [], [], 0
    for tok in tokens:
        if tok in (("op", "("), ("op", "[")):
            depth += 1
        elif tok in (("op", ")"), ("op", "]")):
            depth -= 1
        if depth < 0:
            raise _Bad("Parentheses are not balanced.")
        if tok == ("op", ";"):
            if depth:
                raise _Bad("A `;` inside parentheses is not accepted.")
            statements.append(current)
            current = []
        else:
            current.append(tok)
    if depth:
        raise _Bad("Parentheses are not balanced.")
    if current:
        statements.append(current)
    if not statements or not statements[-1] or not all(statements):
        raise _Bad("There is nothing to run, or a statement is empty.")
    lets: set[str] = set()
    for n, st in enumerate(statements):
        last = n == len(statements) - 1
        is_let = st[0][1].lower() == "let" and st[0][0] == "id"
        if is_let == last or (is_let and not (len(st) > 3 and st[1][0] == "id" and st[2] == ("op", "="))):
            raise _Bad("Only `let name = ...` statements may come before the query, and the query must be last.")
        scan = _Scan(st, allowed, lets)
        p = 3 if is_let else 0
        while scan.at(p) == ("op", "("):
            p += 1
        got = scan.name(p)
        if scan.at(p)[1].lower() == "union":
            pass  # the union's tables are checked by `scan`
        elif got and (not is_let or scan.at(got[1]) in (("op", "|"), ("op", ")"), ("end", ""))):
            scan.check(got[0])
        elif not is_let:
            raise _Bad(f"The query must start with one of the tables this product may query: {', '.join(allowed) or 'none'}.")
        scan.scan()
        if is_let:
            lets.add(st[1][1])
    final = cleaned.strip()
    while final.endswith(";"):
        final = final[:-1].rstrip()
    return final if final.endswith(TAKE) else final + TAKE


def guard(kql: Any, tables: Any = None) -> tuple[str, str]:
    """`(text to send, "")` or `("", the sentence that refuses it)`. `tables` defaults to `logs.tables`."""
    allowed = tuple(settings()["tables"] if tables is None else tables)
    try:
        return _guard(kql, allowed), ""
    except _Bad as bad:
        return "", str(bad)


# --- the named reads (`kind: kql` in reads.yaml) ----------------------------------------------------------------------

@dataclass(frozen=True)
class Entry:
    """Duck-types `live.Entry` where the tool and the card read it (`name`, `summary`, `args`, `cost_note`, `role`)."""
    name: str
    summary: str
    tables: tuple[str, ...]
    args: dict[str, dict[str, Any]]
    text: str
    cost_note: str = ""
    role: str = "logs"
    method: str = "POST"
    scope: str = "workspace"
    resource_type: str = "workspace"


FREE_ENTRY = Entry(FREE, "A free-form Log Analytics query", (), {}, "",
                   "Queries are billed by Log Analytics only on Basic or Auxiliary tables and in Sentinel; check the workspace.")


def literal(value: Any) -> str:
    """A KQL string literal. Typed arguments cannot hold a quote; this escapes anyway."""
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def render(entry: Entry, args: dict[str, str]) -> str:
    return _HOLE.sub(lambda m: literal(args.get(m[1], "")), entry.text).strip()


def _entry(raw: dict) -> Entry:
    name = str(raw.get("name") or "")
    if not live._NAME.fullmatch(name):
        raise live.ReadsInvalid(f"`{name}` is not a read name (lower-case letters, digits and _).")
    text, tables = str(raw.get("text") or "").strip(), tuple(str(t) for t in raw.get("tables") or ())
    if not str(raw.get("summary") or "").strip() or not text or not tables or not all(_TABLE.fullmatch(t) for t in tables):
        raise live.ReadsInvalid(f"{name}: a KQL read needs summary, text and tables.")
    args = raw.get("args") or {}
    for arg, spec in args.items():
        if not isinstance(spec, dict) or spec.get("type") not in _KQL_ARGS or (spec["type"] == "enum" and not spec.get("values")):
            raise live.ReadsInvalid(f"{name}: argument `{arg}` needs a type from {', '.join(_KQL_ARGS)}.")
    if sum(s["type"] == "duration" for s in args.values()) > 1:
        raise live.ReadsInvalid(f"{name}: only one duration argument.")
    holes = set(_HOLE.findall(text))
    used = {a for a, s in args.items() if s["type"] != "duration"}
    if holes != used:
        raise live.ReadsInvalid(f"{name}: the text uses {sorted(holes)} but the arguments are {sorted(used)}.")
    entry = Entry(name, str(raw["summary"]).strip(), tables, args, text, str(raw.get("cost_note") or "").strip())
    final, refusal = guard(render(entry, {}), tables)
    if refusal:
        raise live.ReadsInvalid(f"{name}: the text does not pass the guard: {refusal}")
    return entry


def parse(text: str) -> dict[str, Entry]:
    try:
        rows = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise live.ReadsInvalid(f"reads.yaml does not parse: {error}") from None
    out: dict[str, Entry] = {}
    for raw in rows if isinstance(rows, list) else []:
        if isinstance(raw, dict) and raw.get("kind") == "kql":
            entry = _entry(raw)
            if entry.name in out or entry.name == FREE:
                raise live.ReadsInvalid(f"{entry.name} is listed twice.")
            out[entry.name] = entry
    return out


def load() -> dict[str, Entry]:
    entries = parse(live.READS_FILE.read_text(encoding="utf-8"))
    clash = sorted(set(entries) & set(live.load()))
    if clash:
        raise live.ReadsInvalid(f"{', '.join(clash)} is both a Resource Manager read and a log query.")
    return entries


def known(name: str) -> bool:
    return name == FREE or name in load()


def entry_for(name: str) -> Entry | None:
    return FREE_ENTRY if name == FREE else load().get(name)


def catalogue() -> str:
    lines = [f"- {e.name} (log query; {', '.join(e.tables)}): {e.summary}" + (f" Arguments: {', '.join(e.args)}." if e.args else "")
             for e in sorted(load().values(), key=lambda e: e.name)]
    return "\n".join(lines)


# --- what a request records -------------------------------------------------------------------------------------------

def _timespan(value: Any) -> str:
    text = str(value or "").strip() or DEFAULT_TIMESPAN
    cap = settings()["max_timespan"]
    if not 60 <= live._seconds(text) <= live._seconds(cap):
        raise live.Refused(f"The time range must be between PT1M and {cap}, for example PT30M, PT6H or P1D.")
    return text.upper()


def workspace_for(name: Any) -> dict[str, str]:
    """The configured workspace named `name` (the only one, when `name` is empty), or `Refused` with the sentence."""
    known_ws = settings()["workspaces"]
    if not known_ws:
        raise live.Refused(NOT_SET_UP)
    names = ", ".join(w["name"] for w in known_ws)
    text = str(name or "").strip()
    if not text and len(known_ws) == 1:
        return known_ws[0]
    found = next((w for w in known_ws if w["name"].lower() == text.lower()), None)
    if found is None:
        raise live.Refused(f"{'`' + text + '` is not a workspace this product may query. ' if text else 'Say which workspace. '}Workspaces: {names}.")
    return found


def prepare(read: str, kql: str, workspace: Any, supplied: dict[str, Any]) -> dict[str, Any]:
    """What `azure_read request` records for a log query: `entry`, `variables`, `limit` (the workspace name), `commands`
    (the KQL, one line each, the text that will be sent). `Refused` with the sentence when it may not be asked."""
    if read and kql:
        raise live.Refused("Give either a named read or `kql`, not both.")
    if not is_configured():
        raise live.Refused(NOT_SET_UP)
    ws = workspace_for(workspace)
    if kql:
        entry, typed, span = FREE_ENTRY, {}, _timespan(supplied.get("timespan"))
        extra = sorted(set(supplied) - {"timespan"})
        text = kql
    else:
        entry = load()[read]
        typed = live.check_args(entry, supplied)
        span = _timespan(typed.get("duration"))
        extra, text = [], render(entry, typed)
    if extra:
        raise live.Refused(f"A free-form query takes `kql`, `workspace` and `timespan`, not `{', '.join(extra)}`.")
    final, refusal = guard(text, settings()["tables"])
    if refusal:
        raise live.Refused(refusal)
    return {"entry": entry, "variables": {**typed, "workspace": ws["name"], "timespan": span}, "limit": ws["name"],
            "commands": final.splitlines()}


# --- sending ---------------------------------------------------------------------------------------------------------

def _token() -> str:
    cfg, own = config.get_config(), settings()
    material = credential._logs_material()
    if not (cfg["tenant_id"] and own["client_id"] and material):
        raise client.NotConfigured(NOT_SET_UP)
    url = f"{client._cloud().login}/{cfg['tenant_id']}/oauth2/v2.0/token"
    key = (url, own["client_id"], hash(material))
    with _lock:
        if _cache["key"] == key and _clock() < _cache["until"]:
            return _cache["token"]
        form = {"grant_type": "client_credentials", "client_id": own["client_id"], "scope": SCOPE,
                "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                "client_assertion": client._assertion(material, url, own["client_id"])}
        try:
            response = client._post(url, data=form)
        except requests.RequestException:
            raise client.SignInFailed("network", "Microsoft sign-in could not be reached.") from None
        if response.status_code != 200:
            raise client._sign_in_failure(response)
        try:
            body = response.json()
            issued, lifetime = str(body["access_token"]), int(body.get("expires_in") or 3600)
        except (ValueError, TypeError, KeyError):
            raise client.SignInFailed("other", "Azure answered the sign-in with something this plugin cannot read.") from None
        _cache.update(key=key, token=issued, until=_clock() + max(lifetime - client._TOKEN_MARGIN, 0))
        return issued


def reset() -> None:
    with _lock:
        _cache.update(key=None, token="", until=0.0)


def _host() -> str:
    return client.TEST_ENDPOINTS.arm if client.TEST_ENDPOINTS else HOST  # the stand-in, only ever loopback


def _failure(response: requests.Response) -> live.ReadUnavailable:
    status = response.status_code
    if status == 400:
        try:
            error = response.json()["error"]
            said = str((error.get("innererror") or {}).get("message") or error.get("message") or "")
        except (ValueError, KeyError, TypeError, AttributeError):
            said = ""
        hint = f" It said: {shape._safe(said)[:300]}" if said else ""
        return live.ReadUnavailable(f"Log Analytics did not accept the query (HTTP 400).{hint}", status)
    messages = {
        401: "Azure did not accept the log application's sign-in (HTTP 401).",
        403: "The log application lacks the Log Analytics Reader role on this workspace (HTTP 403). Role changes can take up to an hour to apply.",
        404: "Log Analytics has no such workspace at this address (HTTP 404). Check the workspace ID in Settings.",
        504: f"The query ran longer than {WAIT} seconds (HTTP 504). Ask for a shorter time range, fewer tables or a summary.",
    }
    return live.ReadUnavailable(messages.get(status) or (f"Log Analytics had a problem answering (HTTP {status}). Try again in a minute."
                                                          if status >= 500 else f"Log Analytics could not answer the query (HTTP {status})."), status)


def _send(url: str, body: dict) -> requests.Response:
    """One authorised POST: signed in again once on a 401, one 429 waited out (up to 30 seconds)."""
    for attempt in range(2):
        try:
            response = requests.post(url, json=body, headers={"Authorization": f"Bearer {_token()}", "Prefer": f"wait={WAIT}"},
                                     timeout=WAIT + 15, allow_redirects=False)
        except client.AzureError as error:
            raise live.ReadUnavailable(str(error)) from None
        except requests.RequestException:
            raise live.ReadUnavailable("Log Analytics could not be reached.") from None
        if response.status_code == 401 and attempt == 0:
            reset()
        elif response.status_code == 429:
            if attempt:
                raise live.ReadUnavailable("Log Analytics is limiting requests (HTTP 429). Try again in a minute.", 429)
            _sleep(min(client._wait(response), MAX_PAUSE))
        else:
            return response
    return response  # a second 401: the caller turns it into a sentence


def _cell(column: str, value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))
    if any(c.lower() in column.lower() for c in URL_COLUMNS) or text[:8].lower().startswith(("http://", "https://")):
        text = text.split("?", 1)[0]
    return shape._safe(text)[:MAX_CELL]


def reduce(body: Any) -> dict[str, Any]:
    """The first table of an answer, without identity columns, every cell masked, at most 200 rows and 64 KB."""
    tables = body.get("tables") if isinstance(body, dict) else None
    table = tables[0] if isinstance(tables, list) and tables and isinstance(tables[0], dict) else None
    if table is None or not isinstance(table.get("columns"), list) or not isinstance(table.get("rows"), list):
        raise live.ReadUnavailable("Log Analytics answered with something this plugin cannot read.", 200)
    # By what the name holds, not what it is: Azure renames a column itself (`CallerIpAddress1` on the right of a
    # join, `Claims_string` in a union of two types, `any_UserAgent` from an aggregate).
    drop = [c.lower() for c in IDENTITY]
    names = [str(c.get("name") or "") if isinstance(c, dict) else "" for c in table["columns"]]
    keep = [i for i, n in enumerate(names) if not any(d in n.lower() for d in drop)]
    rows, cut = [], len(table["rows"]) > MAX_ROWS
    for raw in table["rows"][:MAX_ROWS]:
        if isinstance(raw, list):
            rows.append([_cell(names[i], raw[i] if i < len(raw) else None) for i in keep])
    result = {"columns": [names[i] for i in keep], "rows": rows}
    while rows and len(json.dumps(result)) > MAX_RESULT:
        rows.pop()
        cut = True
    partial = isinstance(body.get("error"), dict)  # a 200 with a PartialError: some data was not read
    return {"result": result, "truncated": cut or partial, "partial": partial, "rows": len(rows)}


def query(workspace: str, text: str, timespan: str, chat: str = "") -> dict[str, Any]:
    """Run one query now. The text is guarded again here, whoever approved it. `Refused` for what is not allowed,
    `ReadUnavailable` when Azure cannot answer."""
    ws = workspace_for(workspace)
    final, refusal = guard(text, settings()["tables"])
    if refusal:
        raise live.Refused(refusal)
    span = _timespan(timespan)
    with _busy_lock:
        if chat in _busy:
            raise live.Refused("A log query is already running in this chat. Wait for it to finish.")
        _busy.add(chat)
    try:
        response = _send(f"{_host()}/v1/workspaces/{ws['id']}/query", {"query": final, "timespan": span})
    finally:
        with _busy_lock:
            _busy.discard(chat)
    status = response.status_code
    if 300 <= status < 400:
        raise live.ReadUnavailable(f"Azure answered with a redirect (HTTP {status}) to another address; it was not followed.", status)
    if status == 204:
        return {"result": {"columns": [], "rows": []}, "truncated": False, "partial": False, "rows": 0, "status": 204,
                "at": _now().isoformat(timespec="seconds")}
    if status != 200:
        raise _failure(response)
    if len(response.content) > MAX_ANSWER:
        raise live.ReadUnavailable("The answer is too large to read; ask for a shorter time range or a summary.", 200)
    try:
        body = response.json()
    except ValueError:
        body = None
    return {**reduce(body), "status": 200, "at": _now().isoformat(timespec="seconds")}
