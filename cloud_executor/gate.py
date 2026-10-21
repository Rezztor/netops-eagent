"""The static gate and the plan policy for Azure changes (ADR 0061, 0062, 0065).

Plain Python, standard library only. The cloud executor imports it, and the application imports the same file
(`plugins/_ea_azure_change/helpers/gate.py` re-exports it) so the early answer an engineer sees is the answer the
executor gives. Three public functions:

* `check_code(files)` reads OpenTofu source before anything runs and returns refusals.
* `check_plan(plan, rails, draft, inventory_ids)` reads the plan JSON `tofu show -json` wrote and returns
  `(checks, refusals)`. **Any delete, replace or forget refuses the whole plan.** No rail, argument or confirmation
  changes that: the owner's rule is that the product never removes, stops or swaps anything in Azure.
* `plan_digest(plan, source, workspace)` hashes what the plan changes. It is the artefact an engineer approves.

`draft` is the dict the executor builds from the template or the free code: `template` (name or `"free"`),
`may_change` (attribute paths an update may touch; `None` for free code, which uses `MAY_CHANGE`),
`restarts_existing`, `subscription`, `resource_group`, optionally `location` and `target_ids` (ARM IDs the
variables name, for `restarts_existing`).

Loosening any constant below is a security change: extend the accept and refuse table in
`cloud_executor/tests/test_gate.py` first (ADR 0046 practice).
"""

from __future__ import annotations

import fnmatch
import hashlib
import ipaddress
import json
import re
from typing import Any

# --------------------------------------------------------------------------------------------- the constants

#: The most steps one change set holds (ADR 0073).
SET_MAX_STEPS = 12

#: Block types free code may use. Everything else (terraform, provider, backend, cloud, removed, check, ephemeral,
#: provisioner and connection inside a resource) is refused.
BLOCKS_ALLOWED = frozenset({"resource", "data", "variable", "locals", "output", "import", "moved", "module"})

DATA_ALLOWED = frozenset({
    "azurerm_resource_group", "azurerm_virtual_network", "azurerm_subnet", "azurerm_client_config",
    "azurerm_subscription", "azurerm_network_security_group", "azurerm_route_table",
})

#: Functions that read the filesystem or the clock: they would make the digest differ by itself, or read the workdir.
FUNCTIONS_DENIED = (
    "file", "filebase64", "fileset", "templatefile", "fileexists", "pathexpand", "timestamp", "plantimestamp",
    "uuid", "bcrypt", "filemd5", "filesha1", "filesha256", "filesha512", "filebase64sha256", "filebase64sha512",
)

ATTRS_DENIED = ("custom_data", "user_data", "extension", "run_command", "customdata", "userdata", "extensionprofile")

#: fnmatch patterns, lower case, for an azurerm type name or for an azapi ARM type (without `@api-version`).
TYPES_DENIED = (
    "azurerm_virtual_machine_extension", "azurerm_virtual_machine_run_command",
    "azurerm_resource_group_template_deployment", "*_template_deployment", "azurerm_role_assignment",
    "azurerm_role_definition", "azurerm_policy_*", "azurerm_management_lock", "azurerm_automation_*",
    "azurerm_container_group", "azurerm_monitor_autoscale_setting", "azurerm_storage_management_policy",
    "azapi_resource_action", "azapi_data_plane_resource",
    "microsoft.authorization/*", "microsoft.resources/deploymentscripts", "microsoft.resources/deployments",
    "microsoft.resources/deploymentstacks", "microsoft.insights/autoscalesettings",
    # Stricter than the contract's list: the same reach as the denied azurerm types, by another name.
    "microsoft.compute/*/extensions", "microsoft.compute/*/runcommands", "microsoft.web/sites/extensions",
    "microsoft.automation/*", "microsoft.containerinstance/containergroups",
    "microsoft.storage/storageaccounts/managementpolicies", "microsoft.blueprint/*", "microsoft.subscription/*",
)

#: Attribute paths an update of a free-code resource of this type may touch. No entry: it cannot be updated. Tags and
#: the additions `_judge` can prove are additions, on types whose update does not restart or re-point anything. Types
#: free code cannot create (see `_free_problems`) have no entry.
MAY_CHANGE: dict[str, tuple[str, ...]] = {
    "azurerm_resource_group": ("tags",),
    "azurerm_virtual_network": ("tags", "address_space"),
    "azurerm_public_ip": ("tags",),
    "azurerm_storage_account": ("tags",),
    "azurerm_network_security_group": ("tags",),
    "azurerm_route_table": ("tags",),
    "azurerm_private_dns_zone": ("tags",),
    "azurerm_service_plan": ("tags", "worker_count"),
    "azurerm_network_security_rule": ("source_address_prefixes", "destination_address_prefixes", "destination_port_ranges"),
    "azurerm_log_analytics_workspace": ("tags", "retention_in_days", "daily_quota_gb"),
    "azurerm_virtual_network_peering": ("allow_forwarded_traffic", "allow_gateway_transit"),
}

#: Free code may create an association only onto a subnet the same plan creates: an existing subnet would be re-pointed.
SUBNET_LINKS = frozenset({"azurerm_subnet_network_security_group_association", "azurerm_subnet_route_table_association"})

NEVER_DOWN = frozenset({
    "capacity", "instances", "sku.capacity", "worker_count", "node_count", "retention_in_days", "daily_quota_gb",
    "disk_size_gb", "quota",
})
NEVER_CHANGE = frozenset({"size", "vm_size", "sku_name", "sku", "sku.name", "sku.tier", "sku.family", "sku.size"})
NEVER_OFF = frozenset({
    "enabled", "https_only", "allow_virtual_network_access", "allow_forwarded_traffic", "allow_gateway_transit",
    "use_remote_gateways", "public_network_access_enabled",
})

#: Attributes that hold the Azure ID a resource is made on or in. Each must be in the draft's subscription and in a
#: resource group this product may change, like `resource_group_name`.
SCOPE_IDS = ("parent_id", "resource_id", "target_resource_id", "firewall_policy_id", "private_dns_zone_id")

PROVIDERS = frozenset({"registry.opentofu.org/hashicorp/azurerm", "registry.opentofu.org/azure/azapi"})
ACTIONS_ALLOWED = ({"no-op"}, {"read"}, {"create"}, {"update"})
UNKNOWN = "(known after apply)"
SENSITIVE = "(sensitive)"

_MODULE_SOURCE = re.compile(r"\./modules/[A-Za-z0-9_-]{1,64}")
_FILENAME = re.compile(r"(modules/[A-Za-z0-9_-]{1,64}/)?[A-Za-z0-9_-]{1,64}\.tf")
_SECRETY = re.compile(
    r"password|secret|token|private_key|connection_string|primary_key|secondary_key|access_key|shared_key|sas_|"
    r"custom_data|user_data|certificate", re.I)
_PREFIX_KEY = re.compile(r"(address_prefix(es)?|address_space)$")
_PORT_KEY = re.compile(r"port_ranges?$")
_VCPU = re.compile(r"^Standard_[A-Za-z]+(\d+)")


class ParseError(ValueError):
    """The source could not be read to the end. Always a refusal."""


# ------------------------------------------------------------------------------------------------- the source

_HEREDOC = re.compile(r"<<-?([A-Za-z_][A-Za-z0-9_]*)\r?\n")


def _heredoc(text: str, i: int) -> re.Match[str]:
    """The heredoc that opens at `text[i:]` (`<<`). OpenTofu accepts more markers than this scanner reads (a dash, other
    alphabets); one it cannot read the same way is refused, never guessed at."""
    match = _HEREDOC.match(text, i)
    if not match:
        raise ParseError("a heredoc marker must be plain letters, digits and underscores, with nothing after it")
    return match


def _templates_closed(text: str, i: int, stop: int) -> None:
    """Every `${` and `%{` of a heredoc body closes inside it. OpenTofu does not end a heredoc on a marker line that is
    inside one, so a body where one runs past the marker would be read differently here: refused."""
    while i < stop:
        c = text[i]
        if c in "$%" and text[i + 1:i + 2] == c and text[i + 2:i + 3] == "{":
            i += 3
        elif c in "$%" and text[i + 1:i + 2] == "{":
            i = _scan_braces(text, i + 2)
            if i > stop:
                raise ParseError("a template inside a heredoc is not closed before the heredoc ends")
        else:
            i += 1


def _heredoc_end(text: str, start: int, match: re.Match[str]) -> int:
    """Index just after the heredoc that `match` opens. The closing line is the marker, whitespace aside."""
    marker = match.group(1)
    pos = match.end()
    while pos <= len(text):
        end = text.find("\n", pos)
        line = text[pos:] if end == -1 else text[pos:end]
        if line.strip() == marker:
            _templates_closed(text, match.end(), pos)
            return len(text) if end == -1 else end
        if end == -1:
            break
        pos = end + 1
    raise ParseError("a heredoc is not closed")


def _scan_string(text: str, i: int) -> int:
    """`text[i]` is a quote. Returns the index after the closing quote; `${...}` and `%{...}` are walked."""
    i += 1
    while i < len(text):
        c = text[i]
        if c == "\\":
            i += 2
        elif c == '"':
            return i + 1
        elif c in "$%" and text[i + 1:i + 2] == c and text[i + 2:i + 3] == "{":
            i += 3  # $${ and %%{ are literal text
        elif c in "$%" and text[i + 1:i + 2] == "{":
            i = _scan_braces(text, i + 2)
        elif c == "\n":
            raise ParseError("a string is not closed")
        else:
            i += 1
    raise ParseError("a string is not closed")


def _scan_braces(text: str, i: int) -> int:
    """`i` is just after an opening brace. Returns the index after its match."""
    depth = 1
    while i < len(text):
        c = text[i]
        if c == '"':
            i = _scan_string(text, i)
            continue
        if text.startswith("<<", i):
            i = _heredoc_end(text, i, _heredoc(text, i))
            continue
        if c == "#" or text.startswith(("//", "/*"), i):  # the caller removed comments; one inside a template is not read here
            raise ParseError("a comment inside a template is not accepted")
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ParseError("braces are not balanced")


def _clean(text: str) -> str:
    """The source with comments removed. Strings and heredocs are copied whole, so what is scanned afterwards
    includes every template inside them."""
    out: list[str] = []
    i = 0
    while i < len(text):
        c = text[i]
        if c == '"':
            j = _scan_string(text, i)
            out.append(text[i:j])
            i = j
        elif text.startswith("<<", i):
            j = _heredoc_end(text, i, _heredoc(text, i))
            out.append(text[i:j])
            i = j
        elif c == "#" or text.startswith("//", i):
            while i < len(text) and text[i] != "\n":
                i += 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end == -1:
                raise ParseError("a comment is not closed")
            out.append(" ")
            i = end + 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")


def _blocks(clean: str) -> list[tuple[str, list[str], str]]:
    """Top-level blocks as `(type, labels, body)`. Anything else at the top level is a refusal."""
    found: list[tuple[str, list[str], str]] = []
    i, n = 0, len(clean)
    while True:
        while i < n and clean[i].isspace():
            i += 1
        if i >= n:
            return found
        m = _IDENT.match(clean, i)
        if not m:
            raise ParseError("something that is not a block is at the top level")
        kind, i, labels = m.group(0), m.end(), []
        while True:
            while i < n and clean[i] in " \t":
                i += 1
            if i < n and clean[i] == '"':
                j = _scan_string(clean, i)
                labels.append(clean[i + 1:j - 1])
                i = j
            elif i < n and (lm := _IDENT.match(clean, i)):
                labels.append(lm.group(0))
                i = lm.end()
            else:
                break
        if i >= n or clean[i] != "{":
            raise ParseError(f"'{kind}' is not followed by a block")
        end = _scan_braces(clean, i + 1)
        found.append((kind, labels, clean[i + 1:end - 1]))
        i = end


def _fn_pattern() -> re.Pattern[str]:
    return re.compile(r"(?<![A-Za-z0-9_])(" + "|".join(FUNCTIONS_DENIED) + r")\s*\(")


def _type_denied(name: str) -> bool:
    return any(fnmatch.fnmatchcase(name.lower(), pattern) for pattern in TYPES_DENIED)


def check_code(files: dict[str, str]) -> list[str]:
    """Refusals for a set of `.tf` files (path -> text). Empty list: the code may be planned."""
    refusals: list[str] = []
    if not files:
        return ["There is no code to check."]
    functions, attributes = _fn_pattern(), re.compile(r"(?<![A-Za-z0-9_])(" + "|".join(ATTRS_DENIED) + r")(?![A-Za-z0-9_])", re.I)
    nested = re.compile(r"(?<![A-Za-z0-9_])(provisioner|connection)(?![A-Za-z0-9_])")
    for name, text in sorted(files.items()):
        if not _FILENAME.fullmatch(name) or ".." in name or re.search(r"(^|/)override|_override\.tf$", name):
            refusals.append(f"{name}: only plain .tf files are accepted (no .tf.json, .tofu, override or other file).")
            continue
        try:
            clean = _clean(text)
            blocks = _blocks(clean)
        except ParseError as error:
            refusals.append(f"{name}: the code could not be read ({error}).")
            continue
        for word in sorted({m.group(1) for m in functions.finditer(clean)}):
            refusals.append(f"{name}: the function {word}() is not allowed.")
        for word in sorted({m.group(1).lower() for m in attributes.finditer(clean)}):
            refusals.append(f"{name}: the attribute {word} is not allowed.")
        for word in sorted({m.group(1) for m in nested.finditer(clean)}):
            refusals.append(f"{name}: a {word} block is not allowed.")
        for kind, labels, body in blocks:
            if kind not in BLOCKS_ALLOWED:
                refusals.append(f"{name}: a {kind} block is not allowed.")
            elif kind == "resource":
                rtype = labels[0] if labels else ""
                if not rtype.startswith(("azurerm_", "azapi_")) or _type_denied(rtype):
                    refusals.append(f"{name}: the resource type {rtype or '(none)'} is not allowed.")
                for arm in re.findall(r'"([A-Za-z]+\.[A-Za-z]+/[A-Za-z/]+)@', body):
                    if _type_denied(arm):
                        refusals.append(f"{name}: the Azure type {arm} is not allowed.")
            elif kind == "data" and (not labels or labels[0] not in DATA_ALLOWED):
                refusals.append(f"{name}: the data source {labels[0] if labels else '(none)'} is not allowed.")
            elif kind == "module":
                # One `source` word in the whole block, and it is the attribute: a heredoc or string cannot carry a decoy.
                source = re.search(r'(?m)^\s*source\s*=\s*"([^"]*)"\s*$', body)
                if len(re.findall(r"(?<![A-Za-z0-9_-])source(?![A-Za-z0-9_-])", body)) != 1 or not source \
                        or not _MODULE_SOURCE.fullmatch(source.group(1)):
                    refusals.append(f"{name}: a module may only come from ./modules/<name>.")
    return refusals


# ------------------------------------------------------------------------------------------------- the plan

def _action_set(rc: dict[str, Any]) -> list[str]:
    return list((rc.get("change") or {}).get("actions") or [])


def _changes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [rc for rc in plan.get("resource_changes") or [] if isinstance(rc, dict)]


def _managed(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [rc for rc in _changes(plan) if rc.get("mode") == "managed"]


def _is_import(rc: dict[str, Any]) -> bool:
    return bool((rc.get("change") or {}).get("importing"))


def _arm_type(rc: dict[str, Any]) -> str:
    after = (rc.get("change") or {}).get("after") or {}
    return str(after.get("type") or "").split("@")[0] if str(rc.get("type", "")).startswith("azapi_") else ""


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _is_id(value: Any) -> bool:
    return isinstance(value, str) and value.lower().startswith("/subscriptions/")


def _unknown_at(unknown: Any, key: str) -> Any:
    return unknown.get(key) if isinstance(unknown, dict) else None


def _walk(before: Any, after: Any, unknown: Any, path: str = ""):
    """Yield `(path, before, after)` for every difference. `after` is `UNKNOWN` where the plan does not know it and
    `None` where the value is gone. Dicts are walked; lists are compared whole, by path."""
    if unknown is True:
        yield path, before, UNKNOWN
    elif isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            sub = f"{path}.{key}" if path else key
            u = _unknown_at(unknown, key)
            if key in after:
                yield from _walk(before.get(key), after[key], u, sub)
            elif u:
                yield sub, before.get(key), UNKNOWN
            elif before.get(key) is not None:
                yield sub, before[key], None
    elif before != after or (unknown and unknown != [] and unknown != {}):
        yield path, before, (UNKNOWN if unknown and unknown != [] and unknown != {} else after)


def _may(path: str, allowed: list[str] | tuple[str, ...]) -> bool:
    return any(path == m or path.startswith(m + ".") for m in allowed)


def _same(a: Any, b: Any) -> bool:
    return a.lower() == b.lower() if isinstance(a, str) and isinstance(b, str) else a == b


def _covers(new: Any, old: Any) -> bool:
    """`new` is at least as wide as `old` for a prefix or a port range."""
    if _same(new, old) or new in ("*", "0.0.0.0/0"):
        return True
    try:
        return ipaddress.ip_network(str(old), strict=False).subnet_of(ipaddress.ip_network(str(new), strict=False))
    except (ValueError, TypeError):
        pass
    try:
        lo = lambda r: tuple(int(x) for x in str(r).split("-"))  # noqa: E731
        (a, *b), (c, *d) = lo(old), lo(new)
        return c <= a and (d[0] if d else c) >= (b[0] if b else a)
    except (ValueError, TypeError):
        return False


def _protected(leaf: str, path: str, before: Any) -> bool:
    return (_is_id(before) or leaf in NEVER_DOWN or path in NEVER_DOWN or leaf in NEVER_CHANGE or path in NEVER_CHANGE
            or leaf in NEVER_OFF or bool(_PREFIX_KEY.search(leaf) or _PORT_KEY.search(leaf)))


def _keeps_list(path: str, before: list[Any], after: list[Any], out: list[str]) -> None:
    """Every element the list had is still there (by `name` or `key`, else by value); matched pairs are walked on."""
    for old in before:
        ident = next((k for k in ("name", "key") if isinstance(old, dict) and old.get(k) not in (None, "")), None)
        if ident:
            new = next((n for n in after if isinstance(n, dict) and _same(n.get(ident), old[ident])), None)
            if new is None:
                out.append(f"{path} loses its element {old[ident]!r}.")
            else:
                for sub, b, a in _walk(old, new, None, path):
                    _judge(sub, b, a, out)
        elif not any(_same(old, n) for n in after):
            out.append(f"{path} loses the value {old!r}.")


def _judge(path: str, before: Any, after: Any, out: list[str]) -> None:
    """The rules for one changed attribute: nothing is removed, emptied, re-pointed, lowered, switched off or narrowed."""
    leaf = path.rsplit(".", 1)[-1]
    if after == UNKNOWN:
        if _protected(leaf, path, before) and before is not None:
            out.append(f"{path} would change to a value the plan cannot show, and it is one that must not move.")
        return
    if before is None or (before == after):
        return
    if _empty(after) and not _empty(before):
        out.append(f"{path} would be emptied or removed.")
    elif isinstance(before, list) and isinstance(after, list):
        _keeps_list(path, before, after, out)
    elif _is_id(before) and not _same(before, after):
        out.append(f"{path} would point at a different resource.")
    elif (leaf in NEVER_DOWN or path in NEVER_DOWN) and isinstance(before, (int, float)) and isinstance(after, (int, float)) \
            and not isinstance(before, bool):
        if after < before:
            out.append(f"{path} would go down from {before} to {after}.")
    elif leaf in NEVER_CHANGE or path in NEVER_CHANGE:
        out.append(f"{path} would change from {before!r} to {after!r}; a size or a SKU is never changed.")
    elif (leaf in NEVER_OFF) and before is True and after is not True:
        out.append(f"{path} would be switched off.")
    elif (_PREFIX_KEY.search(leaf) or _PORT_KEY.search(leaf)) and not isinstance(before, (list, dict)):
        if not _covers(after, before):
            out.append(f"{path} would be replaced by a narrower value ({before!r} to {after!r}).")


def _arm_ids(rc: dict[str, Any], subscription: str) -> list[str]:
    """The Azure IDs a managed change names or will make, lower case, as far as the plan lets us know."""
    change = rc.get("change") or {}
    after = change.get("after") or {}
    unknown = change.get("after_unknown") or {}
    found = {str(after["id"]).lower()} if _is_id(after.get("id")) else set()
    rtype, name, rg = str(rc.get("type", "")), after.get("name"), after.get("resource_group_name")
    if rtype.startswith("azapi_") and isinstance(after.get("parent_id"), str) and isinstance(name, str):
        arm = str(after.get("type") or "").split("@")[0]
        tail = arm.split("/", 1)[1] if "/" in arm else ""
        parent = after["parent_id"].rstrip("/")
        found.add((f"{parent}/{tail.split('/')[-1]}/{name}" if "/" in tail else f"{parent}/providers/{arm}/{name}").lower())
    elif rtype == "azurerm_resource_group" and isinstance(name, str):
        found.add(f"/subscriptions/{subscription}/resourcegroups/{name}".lower())
    elif rtype in _ARM and isinstance(name, str) and isinstance(rg, str) and not unknown.get("name") and not unknown.get("resource_group_name"):
        path, parents = _ARM[rtype]
        try:
            parts = [after[p] for p in parents]
        except KeyError:
            return sorted(found)
        if all(isinstance(p, str) for p in parts):
            found.add(f"/subscriptions/{subscription}/resourceGroups/{rg}/providers/{path.format(*parts)}/{name}".lower())
    return sorted(found)


#: azurerm type -> (provider path with {} for each parent name, the attributes that hold the parent names)
_ARM: dict[str, tuple[str, tuple[str, ...]]] = {
    "azurerm_virtual_network": ("Microsoft.Network/virtualNetworks", ()),
    "azurerm_subnet": ("Microsoft.Network/virtualNetworks/{}/subnets", ("virtual_network_name",)),
    "azurerm_network_security_group": ("Microsoft.Network/networkSecurityGroups", ()),
    "azurerm_network_security_rule": ("Microsoft.Network/networkSecurityGroups/{}/securityRules", ("network_security_group_name",)),
    "azurerm_route_table": ("Microsoft.Network/routeTables", ()),
    "azurerm_route": ("Microsoft.Network/routeTables/{}/routes", ("route_table_name",)),
    "azurerm_virtual_network_peering": ("Microsoft.Network/virtualNetworks/{}/virtualNetworkPeerings", ("virtual_network_name",)),
    "azurerm_public_ip": ("Microsoft.Network/publicIPAddresses", ()),
    "azurerm_network_interface": ("Microsoft.Network/networkInterfaces", ()),
    "azurerm_private_endpoint": ("Microsoft.Network/privateEndpoints", ()),
    "azurerm_private_dns_zone": ("Microsoft.Network/privateDnsZones", ()),
    "azurerm_private_dns_zone_virtual_network_link": ("Microsoft.Network/privateDnsZones/{}/virtualNetworkLinks", ("private_dns_zone_name",)),
    "azurerm_service_plan": ("Microsoft.Web/serverFarms", ()),
    "azurerm_linux_web_app": ("Microsoft.Web/sites", ()),
    "azurerm_storage_account": ("Microsoft.Storage/storageAccounts", ()),
    "azurerm_linux_virtual_machine": ("Microsoft.Compute/virtualMachines", ()),
    "azurerm_log_analytics_workspace": ("Microsoft.OperationalInsights/workspaces", ()),
}


def _norm_region(value: Any) -> str:
    return re.sub(r"[\s_-]", "", str(value)).lower()


def _id_scope(value: str) -> tuple[str, str]:
    m = re.match(r"/subscriptions/([^/]+)(?:/resourcegroups/([^/]+))?", value, re.I)
    return (m.group(1).lower(), (m.group(2) or "").lower()) if m else ("", "")


def check_plan(plan: dict[str, Any], rails: dict[str, Any], draft: dict[str, Any],
               inventory_ids: set[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """`(checks, refusals)` for one plan. A non-empty `refusals` means the plan must not be shown or applied."""
    checks: list[dict[str, Any]] = []
    refusals: list[str] = []

    def check(label: str, problems: list[str]) -> None:
        checks.append({"label": label, "ok": not problems, **({"detail": problems[0]} if problems else {})})
        refusals.extend(problems)

    inventory = {str(i).lower() for i in inventory_ids}
    changes = _changes(plan)
    managed = [rc for rc in changes if rc.get("mode") == "managed"]
    sub = str(draft.get("subscription") or "").lower()
    ws_rg = str(draft.get("resource_group") or "").lower()
    template_may = draft.get("may_change")

    # 1. providers
    configured = {str(v.get("full_name") or "") for v in ((plan.get("configuration") or {}).get("provider_config") or {}).values()
                  if isinstance(v, dict)}
    used = {str(rc.get("provider_name") or "") for rc in changes}
    problems = [f"The plan uses the provider {p}, which is not allowed." for p in sorted((used | configured) - {""} - PROVIDERS)]
    check("Providers", problems + (["A resource change names no provider."] if "" in used else []))

    # 2. nothing is deleted, replaced or forgotten
    bad = [f"{rc.get('address')} would be {'/'.join(_action_set(rc)) or 'changed in a way that cannot be read'}; "
           "this product never deletes, replaces, stops or restarts anything."
           for rc in changes if set(_action_set(rc)) not in ACTIONS_ALLOWED or len(_action_set(rc)) != 1]
    check("Nothing is deleted, stopped or restarted", bad)

    # 3. a create over something that exists
    over = []
    for rc in managed:
        if _action_set(rc) == ["create"] and rc.get("type") != "azapi_update_resource":
            over += [f"{rc.get('address')} would create {i}, which exists: adopt it first." for i in _arm_ids(rc, sub) if i in inventory]
    check("Nothing that exists is created again", over)

    # 4 and 5. updates stay in may_change and only add
    updates: list[str] = []
    for rc in managed:
        if _action_set(rc) != ["update"]:
            continue
        change, address, rtype = rc["change"], rc.get("address"), str(rc.get("type"))
        allowed = template_may if template_may is not None else MAY_CHANGE.get(rtype, ())
        for path, before, after in _walk(change.get("before"), change.get("after"), change.get("after_unknown")):
            if not _may(path, allowed):
                updates.append(f"{address}: {path or '(whole resource)'} may not be changed by this change.")
            else:
                found: list[str] = []
                _judge(path, before, after, found)
                updates += [f"{address}: {line}" for line in found]
    check("Updates add, they never remove", updates)

    # 6. restarts_existing
    restarts = []
    if draft.get("restarts_existing"):
        named = {str(t).lower() for t in draft.get("target_ids") or []}
        restarts = [f"This change restarts what it touches, and {i} exists." for i in sorted(named & inventory)]
        restarts += [f"{rc.get('address')} would restart {i}, which exists." for rc in managed for i in _arm_ids(rc, sub) if i in inventory]
    check("Nothing existing is restarted", restarts)

    # 7. types
    allowed_types = set(rails.get("resource_types") or [])
    types: list[str] = []
    for rc in changes:
        rtype, arm = str(rc.get("type")), _arm_type(rc)
        if rc.get("mode") == "data":
            if rtype not in DATA_ALLOWED:
                types.append(f"{rc.get('address')}: reading {rtype} is not allowed.")
        elif rtype not in allowed_types or _type_denied(rtype) or (arm and _type_denied(arm)):
            types.append(f"{rc.get('address')}: the type {arm or rtype} is not allowed.")
        elif rtype.startswith("azapi_") and not arm:
            types.append(f"{rc.get('address')}: the Azure type of an azapi resource must be known.")
    check("Resource types", types)

    # 8. scope, region, size, count
    scope: list[str] = []
    existing = {str(r).lower() for r in (rails.get("resource_groups") or {}).get("existing") or []}
    existing |= {str(g).lower() for g in draft.get("groups_ok") or []}  # made by this product, or by an earlier step of the set
    prefix = str((rails.get("resource_groups") or {}).get("new_prefix") or "").lower()
    if sub not in {str(s).lower() for s in rails.get("subscriptions") or []}:
        scope.append(f"The subscription {sub or '(none)'} is not one this product may change.")
    makes_group = any(rc.get("type") == "azurerm_resource_group" and _action_set(rc) == ["create"] for rc in managed)
    if makes_group:
        if not prefix or not ws_rg.startswith(prefix):
            scope.append(f"A new resource group must start with '{prefix}'.")
    elif ws_rg not in existing:
        scope.append(f"The resource group {draft.get('resource_group')} is not one this product may change.")
    for rc in managed:
        if set(_action_set(rc)) <= {"no-op"}:
            continue
        after = (rc.get("change") or {}).get("after") or {}
        unknown = (rc.get("change") or {}).get("after_unknown") or {}
        for attr in ("resource_group_name",):
            if unknown.get(attr):
                scope.append(f"{rc.get('address')}: {attr} is not known, so its scope cannot be checked.")
            elif isinstance(after.get(attr), str) and after[attr].lower() not in existing | {ws_rg}:
                scope.append(f"{rc.get('address')}: the resource group {after[attr]} is not one this product may change.")
        for attr in SCOPE_IDS:
            if isinstance(after.get(attr), str):
                s, g = _id_scope(after[attr])
                if s != sub or (g and g not in existing | {ws_rg}):
                    scope.append(f"{rc.get('address')}: {attr} is outside the subscription or resource groups this product may change.")
        if rc.get("type") == "azurerm_resource_group" and str(after.get("name", "")).lower() != ws_rg:
            scope.append(f"{rc.get('address')}: a change makes only the resource group it was drafted for.")
    check("Scope", scope)

    regions: list[str] = []
    allowed_regions = {_norm_region(r) for r in rails.get("regions") or []}
    for rc in managed:
        if set(_action_set(rc)) <= {"no-op"}:
            continue
        change = rc.get("change") or {}
        if (change.get("after_unknown") or {}).get("location"):
            regions.append(f"{rc.get('address')}: the region is not known.")
        elif (loc := (change.get("after") or {}).get("location")) and _norm_region(loc) not in allowed_regions:
            regions.append(f"{rc.get('address')}: the region {loc} is not allowed.")
    check("Region", regions)

    sizes: list[str] = []
    for rc in managed:
        after, unknown = (rc.get("change") or {}).get("after") or {}, (rc.get("change") or {}).get("after_unknown") or {}
        if rc.get("type") == "azurerm_linux_virtual_machine" and set(_action_set(rc)) - {"no-op"}:
            size = after.get("size")
            m = _VCPU.match(str(size)) if not unknown.get("size") else None
            if not m or int(m.group(1)) > int(rails.get("vm_sizes_max_vcpu") or 0):
                sizes.append(f"{rc.get('address')}: the VM size {size} is not within {rails.get('vm_sizes_max_vcpu')} vCPU.")
    check("VM size", sizes)

    count = sum(1 for rc in managed if _action_set(rc) in (["create"], ["update"]) or _is_import(rc))
    check("Number of resources", [f"{count} resources is more than the limit of {rails.get('max_resources')}."]
          if count > int(rails.get("max_resources") or 0) else [])

    # 9. free code is judged more strictly than a reviewed template
    if draft.get("template") == "free":
        check("Free code", _free_problems(managed, sub))
    return checks, refusals


_DENIED_KEYS = frozenset(a.replace("_", "") for a in ATTRS_DENIED)


def _denied_keys(tree: Any, depth: int = 0):
    """Keys holding code that runs in Azure, anywhere in a planned value (or its unknown map). The source scan cannot see
    a key that free code builds with a string template, so the plan is read too."""
    if depth > 12:
        return
    if isinstance(tree, dict):
        for key, value in tree.items():
            if re.sub(r"[_-]", "", str(key)).lower() in _DENIED_KEYS and not _empty(value) and value is not False:
                yield str(key)
            yield from _denied_keys(value, depth + 1)
    elif isinstance(tree, list):
        for item in tree:
            yield from _denied_keys(item, depth + 1)


def _free_problems(managed: list[dict[str, Any]], sub: str) -> list[str]:
    """What only free code is refused. A create must be one whose Azure ID the gate can work out, so the inventory check
    covers it (a create is a PUT, and a PUT over something that exists replaces it); a patch of an existing resource has no
    before and after to compare; and no planned value may carry code that runs in Azure. Nor may it take hold of a resource
    it did not make: an `import` (or a `moved` onto its own address) followed by an update would change an existing,
    model-chosen resource in place, and adopted resources come from the inventory, never the model (ADR 0071)."""
    out: list[str] = []
    for rc in managed:
        actions, change, address = _action_set(rc), rc.get("change") or {}, rc.get("address")
        if _is_import(rc) or rc.get("previous_address"):
            out.append(f"{address}: free code cannot import or move an existing resource. Adopt it from the inventory.")
            continue
        if set(actions) <= {"no-op", "read"}:
            continue
        if rc.get("type") == "azapi_update_resource":
            out.append(f"{address}: free code cannot patch an existing resource, because its effect cannot be compared. Use a template.")
        elif actions == ["create"] and not _arm_ids(rc, sub) and not (
                rc.get("type") in SUBNET_LINKS and (change.get("after_unknown") or {}).get("subnet_id") is True):
            out.append(f"{address}: its Azure ID cannot be worked out, so it cannot be shown not to overwrite something that exists.")
        for key in sorted(set(_denied_keys(change.get("after"))) | set(_denied_keys(change.get("after_unknown")))):
            out.append(f"{address}: {key} would run code in Azure, which free code may not do.")
    return out


# ---------------------------------------------------------------------------------------------- the digest

def _mask(key: str, value: Any, sensitive: Any) -> Any:
    return SENSITIVE if sensitive is True or (isinstance(value, (str, list, dict)) and _SECRETY.search(key.rsplit(".", 1)[-1])) else value


def _any_true(tree: Any) -> bool:
    if isinstance(tree, dict):
        return any(_any_true(v) for v in tree.values())
    return any(_any_true(v) for v in tree) if isinstance(tree, list) else tree is True


def _marked(tree: Any, path: str) -> bool:
    """A sensitive mark on `path`, on a parent of it, or anywhere under it. A list is shown whole, so a mark on one
    value inside it (a nested block's secret) hides the list."""
    for key in path.split("."):
        if tree is True:
            return True
        tree = tree.get(key) if isinstance(tree, dict) else None
    return _any_true(tree)


def changed_attributes(rc: dict[str, Any]) -> dict[str, list[Any]]:
    """`{path: [before, after | "(known after apply)"]}` for one resource change, sensitive values masked."""
    change = rc.get("change") or {}
    before, after = change.get("before"), change.get("after")
    bs, as_ = change.get("before_sensitive"), change.get("after_sensitive")
    unknown = change.get("after_unknown")
    out: dict[str, list[Any]] = {}
    for path, b, a in _walk(before if isinstance(before, dict) else {}, after if isinstance(after, dict) else {}, unknown):
        sensitive = _marked(bs, path) or _marked(as_, path)
        out[path] = [_mask(path, b, sensitive), _mask(path, a, sensitive) if a != UNKNOWN else UNKNOWN]
    return out


def _action(rc: dict[str, Any]) -> str:
    actions = _action_set(rc)
    return "import" if _is_import(rc) and actions in (["no-op"], []) else (actions[0] if actions else "unknown")


def plan_digest(plan: dict[str, Any], source: dict[str, Any], workspace: Any) -> str:
    """SHA-256 over the canonical JSON of the workspace, the source and what the plan changes. Unchanged
    attributes and no-op resources are left out, so the same code against the same Azure gives the same digest on
    any day. This hash is the approved artefact (ADR 0062)."""
    items = [{"address": rc.get("address"), "type": rc.get("type"), "action": _action(rc), "changed": changed_attributes(rc)}
             for rc in _managed(plan) if _action(rc) != "no-op" and (rc.get("change") or {}).get("actions") != ["read"]]
    items.sort(key=lambda i: str(i["address"]))
    body = json.dumps({"workspace": workspace, "source": source, "changes": items}, sort_keys=True,
                      separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def set_digest(subscription: str, steps: list[dict[str, Any]]) -> str:
    """SHA-256 over the subscription and the ordered steps' template, group and plan digest: the artefact of a change set
    (ADR 0073). `steps` are `[{template, resource_group, plan_digest}]` in the order they apply; `n` is the position."""
    body = json.dumps({"subscription": str(subscription).lower(),
                       "steps": [{"n": n, "template": s["template"], "resource_group": s["resource_group"],
                                  "plan_digest": s["plan_digest"]} for n, s in enumerate(steps, 1)]},
                      sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------------------------- the summary

def _show(value: Any, limit: int = 70) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, separators=(",", ":"))
    return text if len(text) <= limit else text[: limit - 1] + "…"


def summarise(plan: dict[str, Any]) -> dict[str, Any]:
    """`{add, change, import, lines, resources}`: the masked list of changes the card, the document and the model see."""
    add = change = imports = 0
    lines: list[str] = []
    resources: list[dict[str, Any]] = []
    for rc in sorted(_managed(plan), key=lambda r: str(r.get("address"))):
        action = _action(rc)
        if action == "no-op":
            continue
        attrs = changed_attributes(rc)
        after = (rc.get("change") or {}).get("after") or {}
        name = str(after.get("name") or rc.get("name") or "")
        add, change, imports = add + (action == "create"), change + (action == "update"), imports + (action == "import")
        if action == "create":
            skip = {"id", "name", "tags", "tags_all", "resource_group_name"}
            settings = ", ".join(f"{k}={_show(v[1], 40)}" for k, v in attrs.items()
                                 if k not in skip and v[1] != UNKNOWN and not _empty(v[1]))[:240]
            lines.append(f"+ {rc.get('address')}  {name}  {settings}".rstrip())
        elif action == "update":
            lines.append(f"~ {rc.get('address')}")
            for path, (b, a) in attrs.items():
                lines += [f"    - {path}: {_show(b)}", f"    + {path}: {_show(a)}"]
        else:
            lines.append(f"= {rc.get('address')}  import {_show((rc['change'].get('importing') or {}).get('id', ''), 120)}")
        resource: dict[str, Any] = {
            "address": rc.get("address"), "type": rc.get("type"), "action": action, "name": name,
            "before": {k: v[0] for k, v in attrs.items()} if action == "update" else {},
            "after": {k: v[1] for k, v in attrs.items()},
        }
        if _is_id(after.get("id")):
            resource["native_id"] = after["id"]
        resources.append(resource)
    return {"add": add, "change": change, "import": imports, "lines": lines, "resources": resources}


def drift_lines(plan: dict[str, Any]) -> list[str]:
    """What Azure changed outside the product since the last plan (`resource_drift`), as readable lines."""
    lines = []
    for rc in plan.get("resource_drift") or []:
        if not isinstance(rc, dict):
            continue
        paths = ", ".join(changed_attributes(rc)) or "attributes"
        lines.append(f"{rc.get('address')}: {'/'.join(_action_set(rc)) or 'changed'} outside the product ({paths})")
    return lines


#: Public names for the callers that read a plan beside the gate (cost, the runner).
managed_changes, change_action, arm_ids = _managed, _action, _arm_ids
