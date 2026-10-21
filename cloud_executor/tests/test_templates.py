"""The Azure change templates: catalogue shape, typed variables, recorded plans, and what the gate and plan policy say.

Needs no database, no network and no `tofu`. The plan fixtures in fixtures/templates/ are OpenTofu plan JSON
(format_version 1.2) written from the plan JSON format and the azurerm 5.8 / azapi 2.13 documentation, not recorded
from a run: `<name>.json` is the first apply, `<name>.update.json` is the one change the template's `may_change`
allows. The tests at the bottom import cloud_executor/gate.py and are skipped until it exists.
"""
import copy
import importlib.util
import ipaddress
import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"
FIXTURES = Path(__file__).parent / "fixtures" / "templates"

# The sixteen of the build contract's phase-2 "Templates" section, plus phase-5 firewall_rule and the change-set build's nsg.
NAMES = {
    "resource_group", "vnet", "subnet", "nsg_rule", "route", "peering", "public_ip", "web_app_with_vnet",
    "storage_account", "linux_vm", "private_endpoint", "log_workspace", "diagnostic_setting", "vnet_flow_log",
    "budget", "tags", "firewall_rule", "nsg",
}
VARIABLE_TYPES = {"str", "enum", "int", "bool", "region", "cidr", "list", "tags"}
PROVIDERS = {"registry.opentofu.org/hashicorp/azurerm", "registry.opentofu.org/azure/azapi"}
SUB = "11111111-2222-3333-4444-555555555555"

CATALOGUE = {e["name"]: e for e in yaml.safe_load((ROOT / "templates.yaml").read_text())}


def _fixture(name, update=False):
    return json.loads((FIXTURES / f"{name}{'.update' if update else ''}.json").read_text())


def _strip_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?m)(#|//).*$", "", text)


def _code(name):
    return (TEMPLATES / name / "main.tf").read_text()


def _tf_variables(code):
    """name -> True when the variable block has a top-level `default`."""
    out = {}
    for m in re.finditer(r'(?m)^variable "(\w+)" \{', code):
        depth, i = 1, m.end()
        top = []
        while depth:
            c = code[i]
            depth += c == "{"
            depth -= c == "}"
            top.append(c if depth == 1 else " ")
            i += 1
        out[m.group(1)] = bool(re.search(r"(?m)^\s*default\s*=", "".join(top)))
    return out


# --------------------------------------------------------------------------- a small checker for the typed variables


def _problem(spec, v):
    """The loader's job lives in W2's code; this mirrors the schema the catalogue states, so the patterns are tested."""
    t = spec["type"]
    if t == "str":
        return None if isinstance(v, str) and re.fullmatch(spec["pattern"], v) else "pattern"
    if t == "enum":
        return None if v in spec["values"] else "enum"
    if t == "int":
        ok = isinstance(v, int) and not isinstance(v, bool) and spec.get("min", v) <= v <= spec.get("max", v)
        return None if ok else "int"
    if t == "bool":
        return None if isinstance(v, bool) else "bool"
    if t == "region":
        return None if isinstance(v, str) and re.fullmatch(r"[a-z0-9]{3,30}", v) else "region"
    if t == "cidr":
        try:
            n = ipaddress.ip_network(v, strict=True)
        except (ValueError, TypeError):
            return "cidr"
        ok = n.version == 4 and spec.get("min_prefix", 0) <= n.prefixlen <= spec.get("max_prefix", 32)
        return None if ok else "prefix"
    if t == "tags":
        if not isinstance(v, dict) or not all(isinstance(a, str) and isinstance(b, str) for a, b in v.items()):
            return "tags"
        return "required tag" if any(k not in v for k in spec.get("required", [])) else None
    if t == "list":
        if not isinstance(v, list) or not spec.get("min", 0) <= len(v) <= spec["max"]:
            return "length"
        for item in v:
            if not isinstance(item, dict):
                return "item"
            for f, fs in spec["item"].items():
                if f not in item:
                    if not fs.get("optional"):
                        return f"missing {f}"
                elif _problem(fs, item[f]):
                    return f"{f}: {_problem(fs, item[f])}"
            if set(item) - set(spec["item"]):
                return "unknown field"
        return None
    raise AssertionError(f"unknown type {t}")


def _variable_problems(name, values):
    entry = CATALOGUE[name]
    out = []
    for k, v in values.items():
        if k in entry.get("workspace_variables", []):
            continue
        if k not in entry["variables"]:
            out.append(f"{k}: not a variable")
        elif (p := _problem(entry["variables"][k], v)):
            out.append(f"{k}: {p}")
    out += [f"{k}: missing" for k, s in entry["variables"].items() if not s.get("optional") and k not in values]
    out += [f"{k}: missing" for k in entry.get("workspace_variables", []) if k not in values]
    return out


# ------------------------------------------------------------------------------------------------ the catalogue


def test_the_catalogue_is_the_contracts_eighteen():
    assert set(CATALOGUE) == NAMES and len(CATALOGUE) == 18
    assert {p.name for p in TEMPLATES.iterdir() if p.is_dir()} == NAMES


@pytest.mark.parametrize("name", sorted(NAMES))
def test_entry_shape(name):
    e = CATALOGUE[name]
    assert e["risk"] in ("low", "high")
    assert e["summary"].strip() and e["backout"].strip()
    assert isinstance(e["may_change"], list) and isinstance(e["restarts_existing"], bool)
    assert isinstance(e["types"], list) and e["types"]
    for var, spec in e["variables"].items():
        assert spec["type"] in VARIABLE_TYPES, var
        if spec["type"] == "str":
            re.compile(spec["pattern"])  # no free text: every string has a pattern
        if spec["type"] == "list":
            assert "max" in spec and isinstance(spec["item"], dict)
    assert not set(e["variables"]) & set(e.get("workspace_variables", []))


@pytest.mark.parametrize("name", sorted(NAMES))
def test_entry_matches_its_code(name):
    code = _strip_comments(_code(name))
    e = CATALOGUE[name]
    assert set(re.findall(r'(?m)^resource "(\w+)"', code)) == set(e["types"])
    tf = _tf_variables(code)
    assert set(tf) == set(e["variables"]) | set(e.get("workspace_variables", []))
    for var, spec in e["variables"].items():
        assert tf[var] == bool(spec.get("optional")), f"{var}: optional in the catalogue iff it has a default in code"
    assert not any(tf[v] for v in e.get("workspace_variables", [])), "the workspace's values have no default"
    assert (not e["may_change"]) == (not (FIXTURES / f"{name}.update.json").exists())


# ------------------------------------------------------- nothing in a template can remove, stop or restart anything


@pytest.mark.parametrize("name", sorted(NAMES))
def test_no_removal_stop_or_restart_path(name):
    code = _strip_comments(_code(name))
    assert not re.search(r"(?i)destroy|deallocat|power_?off|restart|reboot|redeploy|reimage|failover|purge|\bstop", code)
    # blob soft delete is a retention setting, not a removal; nothing else may say delete
    assert not re.search(r"(?i)delete", re.sub(r"(container_)?delete_retention_policy", "", code))
    assert not re.search(r'(?m)^(provider|terraform|module|removed|check|moved|import|output)\b', code)
    assert not re.search(r"\b(provisioner|connection|backend|cloud)\s*[\"{]", code)
    assert not re.search(r"\b(file|filebase64|fileset|templatefile|fileexists|pathexpand|timestamp|plantimestamp|uuid|bcrypt)\(", code)
    assert not re.search(r"\b(custom_data|user_data|extension|run_command)\b", code)
    assert not re.search(r"(?i)lifecycle\s*\{[^}]*(prevent_destroy|ignore_changes|replace_triggered_by)", code)


@pytest.mark.parametrize("name", sorted(NAMES))
def test_children_are_separate_resources(name):
    # an inline list would let a parent be written without a child it had
    assert not re.search(r"(?m)^\s+(subnet|security_rule|route)\s*(\{|=)", _strip_comments(_code(name)))


def test_nothing_that_keeps_a_secret_or_a_free_text_value():
    for name, e in CATALOGUE.items():
        code = _strip_comments(_code(name)).replace("disable_password_authentication", "")
        assert not re.search(r"(?i)password|secret|app_settings", code), name
        assert "any" not in {s["type"] for s in e["variables"].values()}


# ------------------------------------------------------------------------------------- typed variables, both ways

BAD = [
    ("nsg_rule", "sources", [{"cidr": "0.0.0.0/0"}]),            # the open internet: prefix below /8
    ("nsg_rule", "sources", [{"cidr": "Internet"}]),
    ("nsg_rule", "ports", [{"range": "443; rm -rf /"}]),
    ("nsg_rule", "direction", "Both"),
    ("nsg_rule", "priority", 50),
    ("route", "next_hop_type", "None"),                          # a blackhole is a stop
    ("route", "next_hop_ip", "10.0.1.999"),
    ("vnet", "address_space", [{"cidr": "10.0.0.0/8"}]),
    ("vnet", "name", "payments"),
    ("subnet", "name", "web"),
    ("subnet", "name", "gatewaysubnet"),                         # reserved names are exact
    ("subnet", "name", "GatewaySubnet2"),
    ("vnet", "subnets", [{"name": "AzureBastionSubnets", "prefix": "10.0.0.0/26"}]),
    ("nsg", "name", "payments"),
    ("nsg", "region", "UAE North"),
    ("nsg", "tags", {"env": "prod"}),
    ("subnet", "prefix", "10.30.3.1/24"),                        # host bits set
    ("peering", "remote_resource_group", "rg-hub/../x"),
    ("linux_vm", "admin_username", "root"),
    ("linux_vm", "admin_username", "Admin"),
    ("linux_vm", "ssh_public_key", "hunter2"),
    ("linux_vm", "ssh_public_key", "ssh-dss AAAAB3NzaC1kc3MAAACBAL" + "A" * 60),
    ("linux_vm", "size", "Standard_D64s_v5"),
    ("storage_account", "name", "StoragePayments"),
    ("storage_account", "replication", "RAGRS"),
    ("budget", "start_month", "2026-11-15"),
    ("budget", "emails", [{"address": "x@y.com; bcc:z"}]),
    ("private_endpoint", "zone_id", "/subscriptions/" + SUB + "/resourceGroups/rg/providers/Microsoft.Network/privateDnsZones/example.com"),
    ("log_workspace", "daily_quota_gb", -1),
    ("log_workspace", "retention_days", 7),
    ("vnet_flow_log", "network_watcher", "nw"),
    ("tags", "tags", {"owner": 1}),
    ("resource_group", "tags", {"env": "prod"}),                 # a required tag missing
    ("firewall_rule", "kind", "dnat"),                          # DNAT is a suggestion
    ("firewall_rule", "sources", [{"cidr": "0.0.0.0/0"}]),      # source wider than /8
    ("firewall_rule", "ports", [{"range": "443; rm -rf /"}]),
    ("firewall_rule", "firewall_policy_id", "/subscriptions/%s/resourceGroups/rg/providers/Microsoft.Network/azureFirewalls/fw1" % SUB),
]
WEB_APPS = [
    "/subscriptions/{s}/resourceGroups/rg-x/providers/Microsoft.Web/sites/app1",
    "/subscriptions/{s}/resourceGroups/rg-x/providers/microsoft.web/sites/app1",
    "/subscriptions/{s}/resourceGroups/rg-x/providers/Microsoft.Web/sites/app1/slots/staging",
]
WS = "/subscriptions/%s/resourceGroups/rg-x/providers/Microsoft.OperationalInsights/workspaces/log-hub" % SUB


@pytest.mark.parametrize("name", sorted(NAMES))
def test_the_recorded_variables_pass_their_own_schema(name):
    values = _fixture(name)["variables"]
    assert _variable_problems(name, {k: v["value"] for k, v in values.items()}) == []


@pytest.mark.parametrize("name,var,value", BAD)
def test_a_bad_value_is_refused(name, var, value):
    values = {k: v["value"] for k, v in _fixture(name)["variables"].items()}
    values[var] = value
    assert any(p.startswith(f"{var}:") for p in _variable_problems(name, values))


@pytest.mark.parametrize("target", WEB_APPS)
def test_a_diagnostic_setting_cannot_target_a_web_app(target):
    values = {k: v["value"] for k, v in _fixture("diagnostic_setting")["variables"].items()}
    values["target_id"] = target.format(s=SUB)
    assert any(p.startswith("target_id:") for p in _variable_problems("diagnostic_setting", values))
    for ok in ("Microsoft.Web/serverfarms/plan1", "Microsoft.Storage/storageAccounts/st1"):
        values["target_id"] = f"/subscriptions/{SUB}/resourceGroups/rg-x/providers/{ok}"
        assert _variable_problems("diagnostic_setting", values) == []


# --------------------------------------------------------------------------------- the recorded plans themselves


def _changes(plan):
    return plan["resource_changes"]


@pytest.mark.parametrize("name", sorted(NAMES))
def test_recorded_plans_are_additions_and_allowed_changes(name):
    code = _strip_comments(_code(name))
    declared = {f"{t}.{n}" for t, n in re.findall(r'(?m)^resource "(\w+)" "(\w+)"', code)}
    for update in (False, True):
        if update and not (FIXTURES / f"{name}.update.json").exists():
            continue
        plan = _fixture(name, update)
        assert plan["format_version"].startswith("1.")
        assert {p["full_name"] for p in plan["configuration"]["provider_config"].values()} <= PROVIDERS
        for rc in _changes(plan):
            assert rc["provider_name"] in PROVIDERS and rc["mode"] == "managed"
            assert rc["type"] + "." + rc["name"] in declared and rc["type"] in CATALOGUE[name]["types"]
            assert rc["change"]["actions"] == (["update"] if update and rc["change"]["before"] != rc["change"]["after"]
                                               else ["no-op"] if update else ["create"])


@pytest.mark.parametrize("name", sorted(n for n in NAMES if (FIXTURES / f"{n}.update.json").exists()))
def test_the_recorded_update_changes_only_may_change(name):
    may = set(CATALOGUE[name]["may_change"])
    changed = {k for rc in _changes(_fixture(name, True)) if rc["change"]["actions"] == ["update"]
               for k in rc["change"]["after"] if rc["change"]["before"].get(k) != rc["change"]["after"][k]}
    assert changed and changed <= may and all(k in may for k in changed)


# ------------------------------------------------------------------------ the gate and the plan policy (W2's file)


def _load_gate():
    path = ROOT / "gate.py"
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location("cloud_executor_gate", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gate = _load_gate()
needs_gate = pytest.mark.skipif(gate is None, reason="cloud_executor/gate.py is not written yet")

RG, HUBRG = "rg-payments-prod-uaen", "rg-hub-prod-uaen"
RAILS = {
    "subscriptions": [SUB],
    "resource_groups": {"existing": [RG, HUBRG, "NetworkWatcherRG"], "new_prefix": "rg-"},
    "regions": ["uaenorth"],
    "resource_types": sorted({t for e in CATALOGUE.values() for t in e["types"]}),
    "vm_sizes_max_vcpu": 8, "usd_month_per_change": 250, "usd_month_total": 1000, "low_risk_usd_month": 20,
    "max_resources": 40, "allow_free_code": False, "allow_unknown_price": False,
    "reference_pattern": "(CR|SR)-?[0-9]{4,10}", "document_valid_days": 14,
}


def _draft(name):
    # ponytail: the draft's keys are a guess until gate.py exists; this is the one place to fix when they differ
    e = CATALOGUE[name]
    return {"template": name, "may_change": e["may_change"], "restarts_existing": e["restarts_existing"],
            "subscription": SUB, "resource_group": RG, "location": "uaenorth"}


def _policy(name, plan, inventory=frozenset()):
    return gate.check_plan(plan, RAILS, _draft(name), set(inventory))[1]


def _shrink(before, after):
    """`after` with one element fewer than `before` where the plan added one (a removed child), else one value lowered."""
    def smaller(b):
        return b[:-1] if isinstance(b, list) else dict(list(b.items())[:-1])

    def collection(b, a):
        for k, v in a.items():
            if isinstance(v, (list, dict)) and b.get(k) != v:
                if isinstance(v, dict) and any(isinstance(x, (list, dict)) for x in v.values()):
                    inner = collection(b[k], v)
                    if inner is not None:
                        return {**a, k: inner}
                else:
                    return {**a, k: smaller(b[k])}
        return None

    out = collection(before, after)
    if out is not None:
        return before, out
    for k, v in after.items():
        if before.get(k) != v and isinstance(v, bool):
            return {**before, k: True}, {**after, k: False}
        if before.get(k) != v and isinstance(v, int):
            return before, {**after, k: before[k] - 1}
    raise AssertionError("nothing to remove")


def _first(plan, action):
    return next(rc for rc in _changes(plan) if rc["change"]["actions"] == [action])


@needs_gate
@pytest.mark.parametrize("name", sorted(NAMES))
def test_the_gate_accepts_the_code(name):
    assert gate.check_code({"main.tf": _code(name)}) == []


@needs_gate
@pytest.mark.parametrize("name", sorted(NAMES))
@pytest.mark.parametrize("update", [False, True])
def test_the_policy_accepts_the_recorded_plan(name, update):
    if update and not (FIXTURES / f"{name}.update.json").exists():
        pytest.skip("create only")
    assert _policy(name, _fixture(name, update)) == []


@needs_gate
@pytest.mark.parametrize("name", sorted(NAMES))
@pytest.mark.parametrize("actions", [["delete"], ["delete", "create"], ["create", "delete"], ["forget"]])
def test_any_delete_replace_or_forget_is_refused(name, actions):
    plan = _fixture(name)
    plan["resource_changes"][0]["change"]["actions"] = actions
    assert _policy(name, plan)


@needs_gate
@pytest.mark.parametrize("name", sorted(n for n in NAMES if (FIXTURES / f"{n}.update.json").exists()))
def test_a_removed_child_is_refused(name):
    plan = _fixture(name, True)
    rc = _first(plan, "update")["change"]
    rc["before"], rc["after"] = _shrink(rc["before"], rc["after"])
    assert _policy(name, plan)


@needs_gate
@pytest.mark.parametrize("name", sorted(NAMES))
def test_a_change_outside_may_change_is_refused(name):
    plan = _fixture(name)
    rc = plan["resource_changes"][0]["change"]
    key = next(k for k, v in rc["after"].items() if isinstance(v, str) and k not in CATALOGUE[name]["may_change"])
    rc["actions"], rc["before"] = ["update"], {**rc["after"], key: rc["after"][key] + "x"}
    assert _policy(name, plan)


@needs_gate
def test_a_plan_with_a_type_outside_the_guard_rails_is_refused():
    plan = _fixture("public_ip")
    plan["resource_changes"][0]["type"] = "azurerm_virtual_machine_extension"
    assert _policy("public_ip", plan)


# A tag change targets a resource group that exists, so the rule "a create over an inventory ID is refused" must not
# apply to azapi_update_resource, which makes nothing. Until W2 says it does not, this is expected to fail.
@needs_gate
@pytest.mark.xfail(reason="needs the plan policy to exempt azapi_update_resource from the exists rule", strict=False)
def test_tags_on_a_group_in_the_inventory_is_accepted():
    rg_id = f"/subscriptions/{SUB}/resourceGroups/{RG}".lower()
    assert _policy("tags", _fixture("tags"), {rg_id}) == []


@needs_gate
def test_firewall_rule_removal_or_replace_is_refused():
    """A copy that drops a named rule, or replaces the collection group, must not pass the plan policy."""
    removed = copy.deepcopy(_fixture("firewall_rule", True))
    rc = _first(removed, "update")["change"]
    after_rules = rc["after"]["network_rule_collection"][0]["rule"]
    rc["after"]["network_rule_collection"][0]["rule"] = [r for r in after_rules if r["name"] != "snet-a-to-vm-b"]
    assert _policy("firewall_rule", removed)

    replaced = copy.deepcopy(_fixture("firewall_rule"))
    replaced["resource_changes"][0]["change"]["actions"] = ["delete", "create"]
    assert _policy("firewall_rule", replaced)


# ------------------------------------------------------------- reserved subnet names, the nsg template, `parents`
# The reserved-name rules run in OpenTofu (`lifecycle precondition` in subnet, `validation` in vnet), which no test here
# can run. The tests below hold the table in main.tf to the contract and say what the rule decides; the rule itself is
# written from Microsoft's documented subnet requirements and is proved by `tofu validate` and a real plan.
# Sizes: https://learn.microsoft.com/azure/vpn-gateway/vpn-gateway-about-vpn-gateway-settings#gwsub (GatewaySubnet /27 or
# larger), https://learn.microsoft.com/azure/firewall/firewall-faq (AzureFirewallSubnet /26),
# https://learn.microsoft.com/azure/firewall/firewall-network-rule-name-resolution (management subnet /26),
# https://learn.microsoft.com/azure/bastion/configuration-settings#subnet (AzureBastionSubnet /26),
# https://learn.microsoft.com/azure/route-server/route-server-faq (RouteServerSubnet /27 or /26: the stricter is used).
RESERVED = {"AzureFirewallSubnet": 26, "AzureFirewallManagementSubnet": 26, "AzureBastionSubnet": 26,
            "RouteServerSubnet": 26, "GatewaySubnet": 27}


def _table(text):
    return {k: int(v) for k, v in re.findall(r"(?m)^\s*(\w+Subnet)\s*=\s*(\d+)\s*$", text)}


def _reserved_problems(name, prefix, nsg="", route_table=""):
    """The subnet template's two preconditions, evaluated against the table read out of its own main.tf."""
    table = _table(_code("subnet"))
    out = []
    if name in table and int(prefix.split("/")[1]) > table[name]:
        out.append("size")
    if name in table and (nsg or route_table):
        out.append("attachment")
    return out


def test_the_reserved_table_is_the_contracts_in_both_templates():
    assert _table(_code("subnet")) == RESERVED
    assert _table(_code("vnet")) == RESERVED
    for name in RESERVED:
        for template, field in (("subnet", "name"), ("vnet", None)):
            spec = CATALOGUE[template]["variables"][field] if field else CATALOGUE[template]["variables"]["subnets"]["item"]["name"]
            assert re.fullmatch(spec["pattern"], name) and not re.fullmatch(spec["pattern"], name.lower())
            assert re.fullmatch(spec["pattern"], "snet-app")


def test_the_subnet_code_states_both_rules():
    code = _strip_comments(_code("subnet"))
    assert "tonumber(split(\"/\", var.prefix)[1]) <= local.reserved[var.name]" in code
    assert 'var.nsg_name == "" && var.route_table_name == ""' in code
    assert len(re.findall(r"precondition\s*\{", code)) == 2
    assert "tonumber(split(\"/\", s.prefix)[1]) <= lookup(" in _strip_comments(_code("vnet"))


@pytest.mark.parametrize("name,size", sorted(RESERVED.items()))
def test_a_reserved_name_is_accepted_at_its_size_and_larger_and_refused_below(name, size):
    values = {k: v["value"] for k, v in _fixture("subnet")["variables"].items()}
    values.update(name=name)
    values.pop("nsg_name"), values.pop("route_table_name")
    for prefix, ok in ((f"10.30.0.0/{size}", True), (f"10.30.0.0/{size - 1}", True), (f"10.30.0.0/{size + 1}", False),
                       ("10.30.0.0/29", False)):
        values["prefix"] = prefix
        assert _variable_problems("subnet", values) == []  # the schema takes the name and the prefix; the code decides
        assert (_reserved_problems(name, prefix) == []) is ok, (name, prefix)


@pytest.mark.parametrize("name,size", sorted(RESERVED.items()))
def test_a_reserved_name_is_refused_with_an_nsg_or_a_route_table(name, size):
    prefix = f"10.30.0.0/{size}"
    assert _reserved_problems(name, prefix, nsg="nsg-x") == ["attachment"]
    assert _reserved_problems(name, prefix, route_table="rt-x") == ["attachment"]
    assert _reserved_problems("snet-app", "10.30.0.0/29", nsg="nsg-x", route_table="rt-x") == []


def test_the_nsg_template_is_small_and_has_no_inline_rule():
    code = _strip_comments(_code("nsg"))
    assert re.findall(r'(?m)^resource "(\w+)"', code) == ["azurerm_network_security_group"]
    assert "security_rule" not in code and "association" not in code
    e = CATALOGUE["nsg"]
    assert e["risk"] == "low" and e["may_change"] == ["tags"] and not e.get("parents")
    assert e["variables"]["name"]["pattern"] == "^nsg-[a-z0-9-]{1,60}$"
    assert "default rules" in e["backout"]


@needs_gate
def test_an_nsg_with_an_inline_rule_is_refused():
    plan = _fixture("nsg", True)
    rc = _first(plan, "update")["change"]
    rc["after"]["security_rule"] = [{"name": "any", "access": "Allow", "priority": 100, "direction": "Inbound"}]
    assert any("security_rule" in line for line in _policy("nsg", plan))
    created = _fixture("nsg")
    assert _policy("nsg", created) == []


# ---------------------------------------------------------------------------------------------------- `parents`

from cloud_executor import templates as tmpl  # noqa: E402 - the catalogue loader (W2's), used from here down

PARENTS = {"subnet": 3, "peering": 2, "route": 1, "nsg_rule": 1}
VNET = "Microsoft.Network/virtualNetworks"
DATA_OF = {"microsoft.network/virtualnetworks": "azurerm_virtual_network",
           "microsoft.network/networksecuritygroups": "azurerm_network_security_group",
           "microsoft.network/routetables": "azurerm_route_table"}


def _loaded():
    return tmpl.load()


def _one(tmp_path, entry, code='variable "name" { type = string }\nresource "azurerm_virtual_network" "this" {\n  name = var.name\n}\n'):
    (tmp_path / "templates" / "probe").mkdir(parents=True)
    (tmp_path / "templates" / "probe" / "main.tf").write_text(code)
    base = {"name": "probe", "summary": "x", "risk": "low", "types": ["azurerm_virtual_network"], "backout": "x",
            "variables": {"name": {"type": "str", "pattern": "^x$"}, "other": {"type": "str", "optional": True, "pattern": "^x$"},
                          "flag": {"type": "bool", "optional": True}}}
    (tmp_path / "templates.yaml").write_text(yaml.safe_dump([{**base, **entry}]))
    return tmpl.load(tmp_path)


def test_the_catalogue_loads_with_the_four_templates_that_name_parents():
    catalogue = _loaded()
    assert {n: len(e.parents) for n, e in catalogue.items() if e.parents} == PARENTS
    assert all(not e.parents for n, e in catalogue.items() if n not in PARENTS)


@pytest.mark.parametrize("name", sorted(PARENTS))
def test_a_template_with_parents_reads_none_of_them(name):
    code = _strip_comments(_code(name))
    for parent in _loaded()[name].parents:
        assert f'data "{DATA_OF[parent["type"]]}"' not in code, (name, parent)
    assert not re.search(r'(?m)^data "', code)


def test_parent_ids_are_built_lower_case_from_the_values():
    c = _loaded()
    sub = SUB.upper()
    subnet = {"vnet_name": "Vnet-Hub", "name": "snet-a", "prefix": "10.0.0.0/24", "nsg_name": "NSG-A", "route_table_name": "RT-A"}
    assert c["subnet"].parent_ids(subnet, sub, "RG-Hub") == [
        (f"/subscriptions/{SUB}/resourcegroups/rg-hub/providers/microsoft.network/virtualnetworks/vnet-hub", "microsoft.network/virtualnetworks"),
        (f"/subscriptions/{SUB}/resourcegroups/rg-hub/providers/microsoft.network/networksecuritygroups/nsg-a", "microsoft.network/networksecuritygroups"),
        (f"/subscriptions/{SUB}/resourcegroups/rg-hub/providers/microsoft.network/routetables/rt-a", "microsoft.network/routetables")]
    # an optional variable left out skips its parent
    assert [t for _, t in c["subnet"].parent_ids({"vnet_name": "v", "name": "snet-a", "prefix": "10.0.0.0/24"}, SUB, "rg")] == ["microsoft.network/virtualnetworks"]
    # the remote network is in its own group
    peering = {"local_vnet": "a", "remote_vnet": "b", "remote_resource_group": "RG-Remote"}
    assert [i for i, _ in c["peering"].parent_ids(peering, SUB, "rg-near")] == [
        f"/subscriptions/{SUB}/resourcegroups/rg-near/providers/microsoft.network/virtualnetworks/a",
        f"/subscriptions/{SUB}/resourcegroups/rg-remote/providers/microsoft.network/virtualnetworks/b"]
    # a route table the change makes is not a parent
    route = {"route_table": "rt", "create_table": True}
    assert c["route"].parent_ids(route, SUB, "rg") == []
    assert len(c["route"].parent_ids({"route_table": "rt"}, SUB, "rg")) == 1
    assert len(c["route"].parent_ids({"route_table": "rt", "create_table": False}, SUB, "rg")) == 1
    assert [t for _, t in c["nsg_rule"].parent_ids({"nsg_name": "n"}, SUB, "rg")] == ["microsoft.network/networksecuritygroups"]
    assert c["nsg"].parent_ids({"name": "nsg-a"}, SUB, "rg") == []


@pytest.mark.parametrize("parents", [
    [{"type": "Microsoft.Network/virtualNetworks"}],                                           # no name
    [{"type": VNET, "name": "name", "scope": "x"}],                                            # unknown key
    [{"type": "Microsoft.Network/virtualNetworks/subnets", "name": "name"}],                   # a child type needs two names
    [{"type": "Microsoft.KeyVault/vaults", "name": "name"}],                                   # not in adopt.ARM_TYPES
    [{"type": VNET, "name": "nothing"}],                                                       # not a variable of the entry
    [{"type": VNET, "name": "name", "resource_group": "nothing"}],
    [{"type": VNET, "name": "name", "unless": "other"}],                                        # unless must be a bool variable
    [{"type": VNET, "name": "flag"}],                                                           # name must be a str variable
    ["Microsoft.Network/virtualNetworks"],
])
def test_a_malformed_parent_is_a_loader_error(tmp_path, parents):
    with pytest.raises(tmpl.CatalogueError, match="probe.parents"):
        _one(tmp_path, {"parents": parents})


def test_a_wellformed_parent_loads_and_type_case_does_not_matter(tmp_path):
    entry = _one(tmp_path, {"parents": [{"type": "microsoft.network/VIRTUALNETWORKS", "name": "name", "unless": "flag"}]})["probe"]
    assert entry.parent_ids({"name": "N"}, "S", "G") == [("/subscriptions/s/resourcegroups/g/providers/microsoft.network/virtualnetworks/n", "microsoft.network/virtualnetworks")]
    assert entry.parent_ids({"name": "N", "flag": True}, "S", "G") == []


@pytest.mark.parametrize("name", ["set", "free", "adopt", "drift_accept", "suggestion"])
def test_a_reserved_template_name_is_refused(tmp_path, name):
    with pytest.raises(tmpl.CatalogueError, match="reserved"):
        _one(tmp_path, {"name": name})


# ------------------------------------------------------- a step plans the same before and after its parent exists


@needs_gate
def test_the_subnet_plan_does_not_depend_on_whether_the_network_exists():
    """The fixture is the plan of a subnet whose network exists or is still to be made: nothing in it comes from the
    network (every value is known and no data source is read), so adding the network to the prior state, and the read
    the old template made of it, changes no digest."""
    plan = _fixture("subnet")
    for rc in _changes(plan):
        unknown = set(rc["change"]["after_unknown"])
        assert unknown <= {"id", "subnet_id"}, rc["address"]
    subnet = next(rc for rc in _changes(plan) if rc["type"] == "azurerm_subnet")
    assert isinstance(subnet["change"]["after"]["virtual_network_name"], str)
    with_parent = copy.deepcopy(plan)
    vnet = {"type": "azurerm_virtual_network", "name": "vnet-payments-prod-uaen-001",
            "values": {"name": "vnet-payments-prod-uaen-001", "id": "/subscriptions/x/resourceGroups/y/providers/Microsoft.Network/virtualNetworks/z"}}
    with_parent["prior_state"] = {"values": {"root_module": {"resources": [vnet]}}}
    with_parent["resource_changes"].append({"address": "data.azurerm_virtual_network.this", "mode": "data", "type": "azurerm_virtual_network",
                                            "name": "this", "provider_name": "registry.opentofu.org/hashicorp/azurerm",
                                            "change": {"actions": ["read"], "before": None, "after": vnet["values"]}})
    args = ({"template": "subnet"}, {"subscription": SUB, "resource_group": RG})
    assert gate.plan_digest(plan, *args) == gate.plan_digest(with_parent, *args)


def test_a_tag_value_with_a_line_break_and_a_cidr_that_is_a_number_are_refused():
    tags = {"env": "dev", "owner": "net", "costcentre": "1"}
    entry = tmpl.load()["nsg"]
    assert entry.validate({"name": "nsg-a", "region": "uaenorth", "tags": tags})
    with pytest.raises(tmpl.VariableRejected):
        entry.validate({"name": "nsg-a", "region": "uaenorth", "tags": {**tags, "note": "a\n+ azurerm_x.forged"}})
    rule = {"nsg_name": "nsg-a", "name": "allow-web", "priority": 200, "direction": "Inbound", "protocol": "Tcp",
            "sources": [{"cidr": "10.0.0.0/8"}], "destinations": [{"cidr": "10.1.0.0/24"}], "ports": [{"range": "443"}]}
    assert tmpl.load()["nsg_rule"].validate(rule)
    for number in (167772160, True):
        with pytest.raises(tmpl.VariableRejected):
            tmpl.load()["nsg_rule"].validate({**rule, "sources": [{"cidr": number}]})
