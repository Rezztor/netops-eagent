"""The accept and refuse table for the static gate and the plan policy (ADR 0046 practice).

Extend this table before loosening any constant in cloud_executor/gate.py. No database, no network, no `tofu`.
"""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("cloud_executor_gate_table", Path(__file__).parents[1] / "gate.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

SUB, RG = "11111111-2222-3333-4444-555555555555", "rg-app"
AZURERM = "registry.opentofu.org/hashicorp/azurerm"
RAILS = {
    "subscriptions": [SUB], "resource_groups": {"existing": [RG], "new_prefix": "rg-"}, "regions": ["uaenorth"],
    "resource_types": ["azurerm_resource_group", "azurerm_virtual_network", "azurerm_network_security_group",
                       "azurerm_network_security_rule", "azurerm_subnet", "azurerm_linux_virtual_machine",
                       "azurerm_service_plan", "azurerm_storage_account", "azapi_update_resource", "azapi_resource"],
    "vm_sizes_max_vcpu": 8, "max_resources": 3,
}
DRAFT = {"template": "free", "may_change": None, "restarts_existing": False, "subscription": SUB, "resource_group": RG}
RID = f"/subscriptions/{SUB}/resourceGroups/{RG}/providers/Microsoft.Network"


def rc(rtype, actions, before, after, name="this", unknown=None, **extra):
    return {"address": f"{rtype}.{name}", "mode": "managed", "type": rtype, "name": name, "provider_name": AZURERM,
            "change": {"actions": actions, "before": before, "after": after, "after_unknown": unknown or {},
                       "before_sensitive": False, "after_sensitive": {}, **extra}}


def verdict(*changes, draft=None, inventory=(), rails=None):
    plan = {"resource_changes": list(changes), "configuration": {"provider_config": {"azurerm": {"full_name": AZURERM}}}}
    checks, refusals = gate.check_plan(plan, rails or RAILS, draft or DRAFT, set(inventory))
    assert (not refusals) == all(c["ok"] for c in checks)
    return refusals


VNET = {"name": "vnet", "resource_group_name": RG, "location": "uaenorth", "address_space": ["10.0.0.0/16"],
        "tags": {"env": "dev"}, "subnet": [{"name": "a", "address_prefixes": ["10.0.1.0/24"]},
                                           {"name": "b", "address_prefixes": ["10.0.2.0/24"]}]}
NSG_RULE = {"name": "allow", "resource_group_name": RG, "network_security_group_name": "nsg", "protocol": "Tcp",
            "destination_port_ranges": ["22", "443"], "source_address_prefixes": ["10.0.0.0/8"], "priority": 100}


def update(rtype, before, **changed):
    return rc(rtype, ["update"], before, {**before, **changed})


# ---------------------------------------------------------------------------------------------- accepted

def test_a_create_is_accepted():
    assert verdict(rc("azurerm_virtual_network", ["create"], None, VNET, unknown={"id": True})) == []


def test_an_added_tag_is_accepted():
    assert verdict(update("azurerm_virtual_network", VNET, tags={"env": "dev", "owner": "net"})) == []


def test_an_added_rule_is_accepted():
    assert verdict(update("azurerm_virtual_network", VNET, subnet=VNET["subnet"] + [{"name": "c", "address_prefixes": ["10.0.3.0/24"]}]),
                   draft={**DRAFT, "may_change": ["subnet"]}) == []


def test_an_added_port_and_an_added_source_are_accepted():
    after = {"destination_port_ranges": ["22", "443", "8443"], "source_address_prefixes": ["10.0.0.0/8", "172.16.0.0/12"]}
    assert verdict(update("azurerm_network_security_rule", NSG_RULE, **after)) == []


def test_a_wider_address_space_is_accepted():
    assert verdict(update("azurerm_virtual_network", VNET, address_space=["10.0.0.0/16", "10.1.0.0/16"])) == []


def test_a_higher_capacity_is_accepted():
    plan = {"name": "p", "resource_group_name": RG, "sku": {"name": "S1", "capacity": 2}}
    assert verdict(update("azurerm_service_plan", plan, sku={"name": "S1", "capacity": 3}),
                   draft={**DRAFT, "may_change": ["sku.capacity"]}) == []


# ---------------------------------------------------------------------------------------------- refused

@pytest.mark.parametrize("actions", [["delete"], ["delete", "create"], ["create", "delete"], ["forget"], ["no-op", "delete"], []])
def test_a_delete_a_replace_or_a_forget_is_refused(actions):
    assert verdict(rc("azurerm_virtual_network", actions, VNET, VNET))


def test_a_vnet_update_that_drops_a_subnet_is_refused():
    assert verdict(update("azurerm_virtual_network", VNET, subnet=VNET["subnet"][:1]), draft={**DRAFT, "may_change": ["subnet"]})


def test_an_nsg_update_that_drops_a_rule_is_refused():
    nsg = {"name": "nsg", "resource_group_name": RG, "security_rule": [{"name": "r1", "priority": 100}, {"name": "r2", "priority": 101}]}
    assert verdict(update("azurerm_network_security_group", nsg, security_rule=nsg["security_rule"][:1]),
                   draft={**DRAFT, "may_change": ["security_rule"]})


def test_a_rule_that_loses_a_port_is_refused():
    assert verdict(update("azurerm_network_security_rule", NSG_RULE, destination_port_ranges=["22"]))


def test_a_scalar_port_or_prefix_replaced_by_a_narrower_one_is_refused():
    rule = {**NSG_RULE, "destination_port_range": "8000-9000", "source_address_prefix": "10.0.0.0/16"}
    assert verdict(update("azurerm_network_security_rule", rule, destination_port_range="8000-8100"),
                   draft={**DRAFT, "may_change": ["destination_port_range"]})
    assert verdict(update("azurerm_network_security_rule", rule, source_address_prefix="10.0.0.0/24"),
                   draft={**DRAFT, "may_change": ["source_address_prefix"]})
    assert verdict(update("azurerm_network_security_rule", rule, source_address_prefix="10.0.0.0/8"),
                   draft={**DRAFT, "may_change": ["source_address_prefix"]}) == []


def test_a_subnet_that_loses_its_nsg_is_refused():
    subnet = {"name": "s", "resource_group_name": RG, "network_security_group_id": f"{RID}/networkSecurityGroups/nsg"}
    for after in (None, "", f"{RID}/networkSecurityGroups/other"):
        assert verdict(update("azurerm_subnet", subnet, network_security_group_id=after), draft={**DRAFT, "may_change": ["network_security_group_id"]})


def test_a_resource_id_that_will_be_known_only_after_apply_is_refused():
    subnet = {"name": "s", "resource_group_name": RG, "network_security_group_id": f"{RID}/networkSecurityGroups/nsg"}
    change = rc("azurerm_subnet", ["update"], subnet, {k: v for k, v in subnet.items() if k != "network_security_group_id"},
                unknown={"network_security_group_id": True})
    assert verdict(change, draft={**DRAFT, "may_change": ["network_security_group_id"]})


def test_a_capacity_that_goes_down_is_refused():
    plan = {"name": "p", "resource_group_name": RG, "sku": {"name": "S1", "capacity": 3}}
    assert verdict(update("azurerm_service_plan", plan, sku={"name": "S1", "capacity": 2}), draft={**DRAFT, "may_change": ["sku.capacity"]})


@pytest.mark.parametrize("field,value", [("size", "Standard_D4s_v5"), ("sku_name", "P2v3")])
def test_a_size_change_is_refused(field, value):
    resource = {"name": "x", "resource_group_name": RG, field: "Standard_B2s" if field == "size" else "P1v3"}
    assert verdict(update("azurerm_linux_virtual_machine", resource, **{field: value}), draft={**DRAFT, "may_change": [field]})


def test_a_sku_tier_change_inside_a_dict_is_refused():
    plan = {"name": "p", "resource_group_name": RG, "sku": {"name": "S1", "capacity": 2}}
    assert verdict(update("azurerm_service_plan", plan, sku={"name": "P1v3", "capacity": 2}), draft={**DRAFT, "may_change": ["sku"]})


def test_enabled_true_to_false_is_refused():
    resource = {"name": "x", "resource_group_name": RG, "https_only": True}
    assert verdict(update("azurerm_storage_account", resource, https_only=False), draft={**DRAFT, "may_change": ["https_only"]})


def test_a_tag_that_is_removed_or_emptied_is_refused():
    assert verdict(update("azurerm_virtual_network", VNET, tags={}))
    assert verdict(update("azurerm_virtual_network", VNET, tags={"env": ""}))


def test_a_create_over_an_inventory_id_is_refused():
    vnet = f"{RID}/virtualNetworks/vnet"
    assert verdict(rc("azurerm_virtual_network", ["create"], None, VNET), inventory={vnet.lower()})
    assert verdict(rc("azurerm_virtual_network", ["create"], None, VNET), inventory={vnet.upper().lower()}) != []
    assert verdict(rc("azurerm_virtual_network", ["create"], None, VNET), inventory={f"{RID}/virtualNetworks/other".lower()}) == []


def test_a_create_of_a_child_over_an_inventory_id_is_refused():
    subnet = {"name": "s", "resource_group_name": RG, "virtual_network_name": "vnet"}
    assert verdict(rc("azurerm_subnet", ["create"], None, subnet), inventory={f"{RID}/virtualNetworks/vnet/subnets/s".lower()})


def test_azapi_update_resource_is_not_a_create_over_something_that_exists():
    group = f"/subscriptions/{SUB}/resourceGroups/{RG}"
    change = rc("azapi_update_resource", ["create"], None,
                {"type": "Microsoft.Resources/resourceGroups@2024-03-01", "resource_id": group, "body": {"tags": {"a": "b"}}})
    assert verdict(change, inventory={group.lower()}, draft={**DRAFT, "template": "tags"}) == []  # a template; free code is refused below


def test_an_update_outside_may_change_is_refused_and_a_type_with_no_entry_cannot_be_updated():
    assert verdict(update("azurerm_virtual_network", VNET, location="uaenorth", name="renamed"))
    assert verdict(update("azurerm_subnet", {"name": "s", "resource_group_name": RG, "x": "1"}, x="2"))


def test_a_template_that_restarts_what_exists_is_refused_on_an_existing_target():
    target = f"{RID}/virtualNetworks/vnet"
    draft = {**DRAFT, "restarts_existing": True, "target_ids": [target]}
    assert verdict(rc("azurerm_virtual_network", ["create"], None, VNET, name="a"), draft=draft, inventory={target.lower()})
    assert verdict(rc("azurerm_virtual_network", ["create"], None, VNET, name="a"), draft=draft) == []


@pytest.mark.parametrize("rtype", ["azurerm_virtual_machine_extension", "azurerm_role_assignment", "azurerm_policy_assignment",
                                   "azurerm_resource_group_template_deployment", "azurerm_container_group", "azapi_resource_action"])
def test_a_denied_type_is_refused_even_when_a_rail_names_it(rtype):
    rails = {**RAILS, "resource_types": RAILS["resource_types"] + [rtype]}
    assert verdict(rc(rtype, ["create"], None, {"name": "x", "resource_group_name": RG}), rails=rails)


@pytest.mark.parametrize("arm", ["Microsoft.Authorization/roleAssignments", "Microsoft.Resources/deploymentScripts",
                                 "Microsoft.Compute/virtualMachines/extensions", "Microsoft.Insights/autoscaleSettings"])
def test_a_denied_azapi_type_is_refused(arm):
    after = {"name": "x", "parent_id": f"/subscriptions/{SUB}/resourceGroups/{RG}", "type": f"{arm}@2024-01-01"}
    assert verdict(rc("azapi_resource", ["create"], None, after))


def test_a_provider_outside_azurerm_and_azapi_is_refused():
    change = rc("azurerm_virtual_network", ["create"], None, VNET)
    change["provider_name"] = "registry.opentofu.org/hashicorp/null"
    assert verdict(change)


def test_scope_region_size_and_count_are_enforced():
    assert verdict(rc("azurerm_virtual_network", ["create"], None, VNET), draft={**DRAFT, "subscription": "other"})
    assert verdict(rc("azurerm_virtual_network", ["create"], None, VNET), draft={**DRAFT, "resource_group": "rg-other"})
    assert verdict(rc("azurerm_virtual_network", ["create"], None, {**VNET, "location": "westeurope"}))
    assert verdict(rc("azurerm_virtual_network", ["create"], None, {**VNET, "resource_group_name": "rg-elsewhere"}))
    assert verdict(rc("azurerm_virtual_network", ["create"], None, {**VNET, "location": "UAE North"})) == []
    vm = {"name": "vm", "resource_group_name": RG, "location": "uaenorth", "size": "Standard_D16s_v5"}
    assert verdict(rc("azurerm_linux_virtual_machine", ["create"], None, vm))
    assert verdict(rc("azurerm_linux_virtual_machine", ["create"], None, {**vm, "size": "Standard_D8s_v5"})) == []
    assert verdict(rc("azurerm_linux_virtual_machine", ["create"], None, {**vm, "size": "weird"}))
    many = [rc("azurerm_virtual_network", ["create"], None, VNET, name=str(i)) for i in range(4)]
    assert verdict(*many)


def test_a_new_resource_group_needs_the_prefix_and_an_existing_one_must_be_listed():
    group = {"name": "rg-new", "location": "uaenorth"}
    assert verdict(rc("azurerm_resource_group", ["create"], None, group), draft={**DRAFT, "resource_group": "rg-new"}) == []
    assert verdict(rc("azurerm_resource_group", ["create"], None, {**group, "name": "prod"}), draft={**DRAFT, "resource_group": "prod"})
    assert verdict(rc("azurerm_resource_group", ["create"], None, {**group, "name": "rg-other"}), draft={**DRAFT, "resource_group": "rg-new"})


def test_a_group_the_executor_made_or_an_earlier_step_makes_is_in_scope_and_nothing_else_is():
    """`groups_ok` (ADR 0074) widens `existing` for the workspace group and for every group a change names, and for nothing
    else: the subscription, the region and a group outside both lists are still refused."""
    vnet = rc("azurerm_virtual_network", ["create"], None, {**VNET, "resource_group_name": "rg-made"})
    peer = rc("azurerm_virtual_network_peering", ["create"], None,
              {"name": "p", "resource_group_name": "rg-made", "remote_virtual_network_id": f"{RID}/virtualNetworks/v"},
              unknown={"id": True})
    link = rc("azapi_resource", ["create"], None, {"parent_id": f"/subscriptions/{SUB}/resourceGroups/rg-made", "name": "x",
                                                    "type": "Microsoft.Network/networkSecurityGroups@2024-05-01"})
    ws = {**DRAFT, "template": "vnet", "resource_group": "rg-made"}  # a template: free code has its own stricter rows
    rails = {**RAILS, "resource_types": RAILS["resource_types"] + ["azurerm_virtual_network_peering"]}
    for change in (vnet, link):
        assert verdict(change, draft=ws)  # not listed, not made: refused
        assert verdict(change, draft={**ws, "groups_ok": ["rg-made"]}) == []
        assert verdict(change, draft={**ws, "groups_ok": ["RG-Made"]}) == []  # compared in lower case
        assert verdict(change, draft={**ws, "groups_ok": ["rg-made-2", "rg-ma"]})  # another group, or a prefix of it
    assert verdict(peer, draft=ws, rails=rails) and verdict(peer, draft={**ws, "groups_ok": ["rg-made"]}, rails=rails) == []
    elsewhere = {**ws, "groups_ok": ["rg-made"]}
    assert verdict(vnet, draft={**elsewhere, "subscription": "other"})
    assert verdict(rc("azurerm_virtual_network", ["create"], None, {**VNET, "location": "westeurope", "resource_group_name": "rg-made"}),
                   draft=elsewhere)
    assert verdict(rc("azurerm_virtual_network", ["create"], None, {**VNET, "resource_group_name": "rg-other"}), draft=elsewhere)


def test_the_set_digest_follows_order_values_and_membership_and_the_subscription_is_compared_in_lower_case():
    a, b, c = ({"template": t, "resource_group": "rg-x", "plan_digest": d * 64} for t, d in (("vnet", "a"), ("subnet", "b"), ("route", "c")))
    one = gate.set_digest(SUB, [a, b, c])
    assert len(one) == 64 and one == gate.set_digest(SUB.upper(), [dict(a), dict(b), dict(c)])
    assert one != gate.set_digest(SUB, [a, c, b]) and one != gate.set_digest(SUB, [a, b])
    assert one != gate.set_digest(SUB, [a, b, {**c, "plan_digest": "d" * 64}]) and one != gate.set_digest(SUB, [a, b, {**c, "resource_group": "rg-y"}])
    assert one != gate.set_digest("0" * 8 + SUB[8:], [a, b, c])
    assert gate.SET_MAX_STEPS == 12 and gate.arm_ids is gate._arm_ids


def test_nothing_may_change_with_no_subscription_in_the_rails():
    assert verdict(rc("azurerm_virtual_network", ["create"], None, VNET), rails={**RAILS, "subscriptions": []})
    assert verdict(rc("azurerm_virtual_network", ["create"], None, VNET), rails={**RAILS, "regions": []})
    assert verdict(rc("azurerm_virtual_network", ["create"], None, VNET), rails={**RAILS, "resource_types": []})


# ---------------------------------------------------------------------------------------------- the digest

def _plan(vnet):
    return {"resource_changes": [rc("azurerm_virtual_network", ["create"], None, vnet, unknown={"id": True}),
                                 rc("azurerm_subnet", ["no-op"], {"name": "s"}, {"name": "s"})]}


def test_the_digest_is_stable_and_follows_what_changes():
    one = gate.plan_digest(_plan(VNET), {"template": "vnet"}, {"subscription": SUB})
    assert one == gate.plan_digest(copy.deepcopy(_plan(VNET)), {"template": "vnet"}, {"subscription": SUB})
    assert one != gate.plan_digest(_plan({**VNET, "tags": {"env": "prod"}}), {"template": "vnet"}, {"subscription": SUB})
    assert one != gate.plan_digest(_plan(VNET), {"template": "vnet", "variables": {"a": 1}}, {"subscription": SUB})
    assert one != gate.plan_digest(_plan(VNET), {"template": "vnet"}, {"subscription": "other"})
    reordered = {"resource_changes": list(reversed(_plan(VNET)["resource_changes"]))}
    assert one == gate.plan_digest(reordered, {"template": "vnet"}, {"subscription": SUB})


def test_the_digest_and_the_summary_mask_a_sensitive_value():
    secret = rc("azurerm_storage_account", ["create"], None, {"name": "st", "primary_access_key": "hunter2", "admin_password": "x"})
    text = repr(gate.summarise({"resource_changes": [secret]})) + repr(gate.changed_attributes(secret))
    assert "hunter2" not in text and gate.SENSITIVE in text
    marked = rc("azurerm_storage_account", ["create"], None, {"name": "st", "thing": "hidden"})
    marked["change"]["after_sensitive"] = {"thing": True}
    assert "hidden" not in repr(gate.summarise({"resource_changes": [marked]}))


def test_the_summary_counts_and_writes_lines():
    plan = {"resource_changes": [rc("azurerm_virtual_network", ["create"], None, VNET, unknown={"id": True}),
                                 update("azurerm_service_plan", {"name": "p", "sku": {"capacity": 1}}, sku={"capacity": 2}, name="p")]}
    summary = gate.summarise(plan)
    assert (summary["add"], summary["change"], summary["import"]) == (1, 1, 0)
    assert summary["lines"][0].startswith("+ azurerm_service_plan") or summary["lines"][0].startswith("~ azurerm_service_plan")
    assert any(line.startswith("+ azurerm_virtual_network.this  vnet") for line in summary["lines"])
    assert any("- sku.capacity: 1" in line for line in summary["lines"]) and any("+ sku.capacity: 2" in line for line in summary["lines"])


# ------------------------------------------------------------------------------------------------- the code

GOOD = '''
variable "name" { type = string }
locals { tags = { env = "dev" } }
resource "azurerm_resource_group" "this" {
  name     = var.name   # a comment with file("x") in it is not code
  location = "uaenorth"
  tags     = local.tags
}
data "azurerm_subscription" "current" {}
output "id" { value = azurerm_resource_group.this.id }
'''


def test_good_code_is_accepted():
    assert gate.check_code({"main.tf": GOOD}) == []


def test_a_good_module_is_accepted_and_a_remote_one_is_not():
    assert gate.check_code({"main.tf": 'module "m" {\n  source = "./modules/net"\n}\n'}) == []
    for source in ("git::https://example.com/x.git", "../x", "./modules/../../x", "hashicorp/consul/aws", "./other/x"):
        assert gate.check_code({"main.tf": f'module "m" {{\n  source = "{source}"\n}}\n'}), source


@pytest.mark.parametrize("code", [
    'terraform { required_version = ">= 1" }',
    'provider "azurerm" { features {} }',
    'terraform {\n backend "local" {}\n}',
    'removed {\n from = azurerm_resource_group.x\n}',
    'check "c" {}',
    'ephemeral "x" "y" {}',
    'resource "azurerm_resource_group" "x" {\n provisioner "local-exec" { command = "id" }\n}',
    'resource "azurerm_resource_group" "x" {\n connection { host = "x" }\n}',
    'resource "azurerm_resource_group" "x" { name = file("/etc/passwd") }',
    'resource "azurerm_resource_group" "x" { name = "${filebase64("x")}" }',
    'resource "azurerm_resource_group" "x" { name = templatefile("a", {}) }',
    'resource "azurerm_resource_group" "x" { name = fileset(".", "*") }',
    'resource "azurerm_resource_group" "x" { name = timestamp() }',
    'resource "azurerm_resource_group" "x" { name = uuid() }',
    'resource "azurerm_resource_group" "x" { name = bcrypt("x") }',
    'resource "azurerm_resource_group" "x" { name = file ( "x") }',
    'resource "azurerm_resource_group" "x" { name = <<EOT\n${file("x")}\nEOT\n}',
    'resource "azurerm_resource_group" "x" { name = "a ${join("", [file("x")])}" }',
    'resource "azurerm_linux_virtual_machine" "x" { custom_data = "x" }',
    'resource "azurerm_linux_virtual_machine" "x" { user_data = "x" }',
    'resource "azurerm_linux_virtual_machine" "x" {\n extension { name = "x" }\n}',
    'resource "azapi_resource" "x" { body = { properties = { osProfile = { customData = "x" } } } }',
    'resource "azurerm_virtual_machine_extension" "x" {}',
    'resource "azurerm_role_assignment" "x" {}',
    'resource "azurerm_policy_assignment" "x" {}',
    'resource "azapi_resource_action" "x" {}',
    'resource "null_resource" "x" {}',
    'resource "terraform_data" "x" {}',
    'resource "azapi_resource" "x" { type = "Microsoft.Authorization/roleAssignments@2022-04-01" }',
    'data "external" "x" { program = ["sh"] }',
    'data "http" "x" { url = "https://x" }',
    'data "azurerm_key_vault_secret" "x" {}',
    'x = 1',
    'resource "azurerm_resource_group" "x" {',
    'resource "azurerm_resource_group" "x" { name = "unclosed }',
    'resource "azurerm_resource_group" "x" { name = <<EOT\nnever closed\n}',
    '/* never closed',
])
def test_refused_code(code):
    assert gate.check_code({"main.tf": code}), code


@pytest.mark.parametrize("name", ["main.tf.json", "x.tofu", "override.tf", "x_override.tf", "../x.tf", "/etc/x.tf", "a/b/c.tf", "x.tfvars"])
def test_refused_file_names(name):
    assert gate.check_code({name: GOOD})


def test_a_comment_cannot_hide_code_and_code_cannot_hide_in_a_comment_lookalike():
    assert gate.check_code({"main.tf": 'resource "azurerm_resource_group" "x" {\n  name = "a" /* c */ + file("x")\n}'})
    assert gate.check_code({"main.tf": 'resource "azurerm_resource_group" "x" {\n  name = "#" + file("x")\n}'})
    assert gate.check_code({"main.tf": 'resource "azurerm_resource_group" "x" {\n  name = "//" + file("x")\n}'})
    assert gate.check_code({"main.tf": 'resource "azurerm_resource_group" "x" {\n  name = "${"}"}${file("x")}"\n}'})
    assert gate.check_code({}) != []


def test_a_word_inside_a_longer_name_is_not_a_function_or_an_attribute():
    code = 'resource "azurerm_resource_group" "x" {\n  name = profile_name\n  location = "uaenorth"\n  tags = { fileshare = "a", extension_note = "b", uuidv5 = "c" }\n}'
    assert gate.check_code({"main.tf": code}) == []


# ----------------------------------------------------------------------------------------------- free code
# Phase 7: free code reaches the gate as `template: "free"` and `may_change: None`, so an update may touch only
# `MAY_CHANGE[type]`. Every vector of research 01-change-engine.md section 6.1 and every removal row of the policy
# table is written here as free code and refused; the accepted rows show what free code can still do.

def free(*changes, inventory=(), **rails):
    """The refusals for free code. The types in the plan are allowed by the rails, so none is refused for that reason alone."""
    types = RAILS["resource_types"] + [c["type"] for c in changes]
    return verdict(*changes, draft=DRAFT, inventory=inventory, rails={**RAILS, "resource_types": types, **rails})


def tf(body):
    return gate.check_code({"main.tf": body})


RG_TF = 'resource "azurerm_resource_group" "x" {\n  name = "rg-x"\n  location = "uaenorth"\n'


@pytest.mark.parametrize("code", [
    # provisioners on any resource, on terraform_data or null_resource, and inside a removed block
    RG_TF + ' provisioner "local-exec" { command = "id" }\n}',
    RG_TF + ' provisioner "remote-exec" { inline = ["id"] }\n}',
    RG_TF + ' provisioner "file" { source = "a" destination = "b" }\n}',
    RG_TF + ' connection { type = "ssh" host = "10.0.0.1" }\n}',
    'resource "terraform_data" "x" {\n provisioner "local-exec" { command = "id" }\n}',
    'resource "null_resource" "x" {\n provisioner "local-exec" { command = "id" }\n}',
    'removed {\n from = azurerm_resource_group.x\n provisioner "local-exec" { when = destroy command = "id" }\n}',
    # data sources that run a program, fetch a URL or read a file, and every provider that is not azurerm or azapi
    'data "external" "x" { program = ["sh", "-c", "id"] }',
    'data "http" "x" { url = "https://example.com" }',
    'data "local_file" "x" { filename = "/etc/passwd" }',
    'data "archive_file" "x" { type = "zip" }',
    'data "null_data_source" "x" {}',
    'resource "random_password" "x" { length = 16 }',
    'resource "aws_instance" "x" {}',
    'resource "helm_release" "x" {}',
    'resource "local_file" "x" { filename = "a" }',
    'terraform {\n required_providers { aws = { source = "hashicorp/aws" } }\n}',
    'provider "azurerm" {\n alias = "other"\n subscription_id = "00000000-0000-0000-0000-000000000000"\n features {}\n}',
    # the azapi way to make any ARM POST, and the data-plane way, as resource, data source and ephemeral resource
    'resource "azapi_resource_action" "x" {}',
    'data "azapi_resource_action" "x" {}',
    'ephemeral "azapi_resource_action" "x" {}',
    'resource "azapi_data_plane_resource" "x" {}',
    'data "azapi_data_plane_resource" "x" {}',
    # remote module sources
    'module "m" {\n source = "hashicorp/consul/aws"\n version = "0.1"\n}',
    'module "m" {\n source = "registry.opentofu.org/x/y/z"\n}',
    'module "m" {\n source = "git::https://example.com/x.git?ref=v1"\n}',
    'module "m" {\n source = "github.com/hashicorp/example"\n}',
    'module "m" {\n source = "https://example.com/x.zip"\n}',
    'module "m" {\n source = "s3::https://s3.amazonaws.com/b/x.zip"\n}',
    'module "m" {\n source = "gcs::https://www.googleapis.com/storage/v1/b/x.zip"\n}',
    'module "m" {\n source = "oci://example.com/x:1"\n}',
    'module "m" {\n source = "./modules/net/../../x"\n}',
    'module "m" {\n source = "./modules/${var.x}"\n}',
    'module "m" {\n}',
    # a decoy `source` line inside a heredoc must not stand in for the real attribute
    'module "m" {\n note = <<EOT\nsource = "./modules/net"\nEOT\n source = "git::https://example.com/x.git"\n}',
    'module "m" { source = "git::https://example.com/x.git" note = <<EOT\nsource = "./modules/net"\nEOT\n}',
    # reading the file system or the clock, by any function and through a string template or a heredoc
    RG_TF + ' tags = { a = file("/etc/passwd") }\n}',
    RG_TF + ' tags = { a = filebase64("x") }\n}',
    RG_TF + ' tags = { a = filemd5("x") }\n}',
    RG_TF + ' tags = { a = filesha256("x") }\n}',
    RG_TF + ' tags = { a = base64sha256(file("x")) }\n}',
    RG_TF + ' tags = { a = jsonencode(fileset(".", "*")) }\n}',
    RG_TF + ' tags = { a = templatefile("x", {}) }\n}',
    RG_TF + ' tags = { a = fileexists("x") }\n}',
    RG_TF + ' tags = { a = pathexpand("~/x") }\n}',
    RG_TF + ' tags = { a = timestamp() }\n}',
    RG_TF + ' tags = { a = plantimestamp() }\n}',
    RG_TF + ' tags = { a = uuid() }\n}',
    RG_TF + ' tags = { a = "x${file("y")}" }\n}',
    RG_TF + ' tags = { a = <<EOT\n${file("y")}\nEOT\n }\n}',
    # state and provider configuration of its own
    'terraform {\n backend "azurerm" {}\n}',
    'terraform {\n cloud { organization = "x" }\n}',
    'terraform {\n encryption {}\n}',
    'check "c" {\n data "http" "x" { url = "https://example.com" }\n}',
    # code that runs in Azure
    'resource "azurerm_virtual_machine_extension" "x" {}',
    'resource "azurerm_virtual_machine_run_command" "x" {}',
    'resource "azurerm_linux_virtual_machine_scale_set" "x" {\n extension { name = "x" }\n}',
    'resource "azurerm_linux_virtual_machine" "x" { custom_data = base64encode("#!/bin/sh") }',
    'resource "azurerm_linux_virtual_machine" "x" { user_data = "x" }',
    'resource "azurerm_resource_group_template_deployment" "x" {}',
    'resource "azurerm_subscription_template_deployment" "x" {}',
    'resource "azurerm_automation_runbook" "x" {}',
    'resource "azurerm_automation_account" "x" {}',
    'resource "azurerm_container_group" "x" {}',
    'resource "azapi_resource" "x" { type = "Microsoft.Resources/deploymentScripts@2023-08-01" }',
    'resource "azapi_resource" "x" { type = "Microsoft.Resources/deployments@2022-09-01" }',
    'resource "azapi_resource" "x" { type = "Microsoft.Resources/deploymentStacks@2024-03-01" }',
    'resource "azapi_resource" "x" { type = "Microsoft.Compute/virtualMachines/extensions@2024-07-01" }',
    'resource "azapi_resource" "x" { type = "Microsoft.Compute/virtualMachines/runCommands@2024-07-01" }',
    'resource "azapi_resource" "x" { type = "Microsoft.Web/sites/extensions@2023-12-01" }',
    'resource "azapi_resource" "x" { type = "Microsoft.Automation/automationAccounts/runbooks@2023-11-01" }',
    'resource "azapi_resource" "x" { type = "Microsoft.ContainerInstance/containerGroups@2023-05-01" }',
    'resource "azapi_resource" "x" { body = { properties = { virtualMachineProfile = { extensionProfile = { extensions = [] } } } } }',
    'resource "azapi_resource" "x" { body = { properties = { osProfile = { customData = "x" } } } }',
    # secrets pulled into state
    'data "azurerm_key_vault_secret" "x" {}',
    'data "azurerm_key_vault_secrets" "x" {}',
    'data "azurerm_storage_account" "x" {}',
    'data "azurerm_storage_account_sas" "x" {}',
    'data "azurerm_storage_account_blob_container_sas" "x" {}',
    # who may do what, and what blocks the owner
    'resource "azurerm_role_assignment" "x" {}',
    'resource "azurerm_role_definition" "x" {}',
    'resource "azurerm_policy_definition" "x" {}',
    'resource "azurerm_management_lock" "x" {}',
    'resource "azurerm_monitor_autoscale_setting" "x" {}',
    'resource "azurerm_storage_management_policy" "x" {}',
    'resource "azapi_resource" "x" { type = "Microsoft.Authorization/roleAssignments@2022-04-01" }',
    'resource "azapi_resource" "x" { type = "Microsoft.Authorization/locks@2020-05-01" }',
    'resource "azapi_resource" "x" { type = "Microsoft.Insights/autoscaleSettings@2022-10-01" }',
    # removals written as code
    'removed {\n from = azurerm_resource_group.x\n lifecycle { destroy = true }\n}',
    'removed {\n from = azurerm_resource_group.x\n lifecycle { destroy = false }\n}',
])
def test_free_code_vector_is_refused_before_anything_runs(code):
    assert tf(code), code


@pytest.mark.parametrize("name", ["backend.tf.json", "main.tofu", "main_override.tf", "override.tf", "terraform.tfvars", "x.auto.tfvars",
                                  ".terraformrc", "modules/../x.tf", "modules/a/b/c.tf", "main.tf\n", "ma in.tf"])
def test_free_code_file_names_that_could_carry_more_are_refused(name):
    assert gate.check_code({name: GOOD})


def test_free_code_that_a_decoy_or_a_comment_dresses_up_is_still_refused():
    # the same check on text a reader would skip: comments, strings, line breaks inside the call
    assert tf(RG_TF + ' tags = { a = file(\n"x") }\n}')
    assert tf(RG_TF + ' tags = { a = file /* hi */ ("x") }\n}')
    assert tf(RG_TF + ' tags = { a = "${"$"}${file("x")}" }\n}')
    assert tf(RG_TF + '}\nprovisioner "local-exec" {}')
    assert tf(RG_TF + '}\n# harmless\nprovider "azurerm" {}')


def test_free_code_that_only_adds_is_accepted():
    code = (RG_TF + ' tags = { env = "dev" }\n}\n'
            'resource "azurerm_virtual_network" "v" {\n name = "vnet"\n resource_group_name = azurerm_resource_group.x.name\n'
            ' location = "uaenorth"\n address_space = ["10.0.0.0/16"]\n}\n'
            'data "azurerm_client_config" "c" {}\noutput "n" { value = azurerm_resource_group.x.name }\n')
    assert tf(code) == []


def test_a_remote_looking_word_is_not_a_module_source():
    assert tf('module "m" {\n  source = "./modules/net"\n  resource_source_note = "git::x"\n}') == []


# -- the plan policy, with free code's own rules: each removal row, and what it may still do

def test_a_free_code_create_is_accepted():
    assert free(rc("azurerm_virtual_network", ["create"], None, VNET, unknown={"id": True})) == []


def test_a_free_code_update_is_accepted_for_a_tag_and_for_an_addition():
    assert free(update("azurerm_virtual_network", VNET, tags={"env": "dev", "owner": "net"})) == []
    assert free(update("azurerm_virtual_network", VNET, address_space=["10.0.0.0/16", "10.1.0.0/16"])) == []
    assert free(update("azurerm_network_security_rule", NSG_RULE, destination_port_ranges=["22", "443", "8443"])) == []
    plan = {"name": "p", "resource_group_name": RG, "worker_count": 2, "sku_name": "S1"}
    assert free(update("azurerm_service_plan", plan, worker_count=3)) == []
    ws = {"name": "w", "resource_group_name": RG, "retention_in_days": 30, "daily_quota_gb": 1}
    assert free(update("azurerm_log_analytics_workspace", ws, retention_in_days=60, daily_quota_gb=2)) == []
    peering = {"name": "p", "resource_group_name": RG, "allow_forwarded_traffic": False}
    assert free(update("azurerm_virtual_network_peering", peering, allow_forwarded_traffic=True)) == []


@pytest.mark.parametrize("actions", [["delete"], ["delete", "create"], ["create", "delete"], ["forget"], ["no-op", "delete"], ["update", "delete"], []])
def test_free_code_that_would_delete_replace_or_forget_is_refused(actions):
    assert free(rc("azurerm_virtual_network", actions, VNET, VNET))
    assert free(rc("azurerm_resource_group", actions, {"name": RG}, {"name": RG}))


def test_free_code_left_out_of_the_code_is_a_delete_and_is_refused():
    # leaving a resource out of the code, or renaming it, is a delete or a replace in the plan, never a quiet change
    assert free(rc("azurerm_virtual_network", ["delete"], VNET, None, name="left_out"))
    assert free(rc("azurerm_virtual_network", ["delete", "create"], VNET, {**VNET, "name": "renamed"}))


@pytest.mark.parametrize("rtype,before,after", [
    ("azurerm_virtual_network", VNET, {"address_space": ["10.9.0.0/16"]}),                          # replaces the prefix it had
    ("azurerm_virtual_network", VNET, {"address_space": []}),
    ("azurerm_virtual_network", VNET, {"tags": {}}),                                                # every tag removed
    ("azurerm_virtual_network", VNET, {"tags": {"env": ""}}),                                       # a tag emptied
    ("azurerm_virtual_network", VNET, {"tags": {"owner": "x"}}),                                    # the tag it had is gone
    ("azurerm_virtual_network", VNET, {"subnet": VNET["subnet"][:1]}),                              # a subnet dropped: not an allowed path
    ("azurerm_network_security_group", {"name": "n", "resource_group_name": RG, "security_rule": [{"name": "r1"}, {"name": "r2"}]},
     {"security_rule": [{"name": "r1"}]}),                                                          # a rule dropped
    ("azurerm_network_security_rule", NSG_RULE, {"destination_port_ranges": ["22"]}),               # a rule that loses a port
    ("azurerm_network_security_rule", NSG_RULE, {"source_address_prefixes": ["192.168.0.0/16"]}),   # a source swapped, not added
    ("azurerm_network_security_rule", {**NSG_RULE, "destination_port_range": "8000-9000"}, {"destination_port_range": "8000-8100"}),
    ("azurerm_network_security_rule", {**NSG_RULE, "access": "Allow"}, {"access": "Deny"}),         # not an allowed path
    ("azurerm_subnet", {"name": "s", "resource_group_name": RG, "network_security_group_id": f"{RID}/networkSecurityGroups/nsg"},
     {"network_security_group_id": None}),                                                          # a subnet that loses its NSG
    ("azurerm_subnet", {"name": "s", "resource_group_name": RG, "network_security_group_id": f"{RID}/networkSecurityGroups/nsg"},
     {"network_security_group_id": f"{RID}/networkSecurityGroups/other"}),                          # or is re-pointed
    ("azurerm_subnet", {"name": "s", "resource_group_name": RG, "address_prefixes": ["10.0.1.0/24"]}, {"address_prefixes": ["10.0.1.0/25"]}),
    ("azurerm_service_plan", {"name": "p", "resource_group_name": RG, "worker_count": 3, "sku_name": "S1"}, {"worker_count": 2}),  # capacity down
    ("azurerm_service_plan", {"name": "p", "resource_group_name": RG, "worker_count": 3, "sku_name": "S1"}, {"sku_name": "P2v3"}),  # a size
    ("azurerm_linux_virtual_machine", {"name": "v", "resource_group_name": RG, "size": "Standard_B2s"}, {"size": "Standard_D4s_v5"}),
    ("azurerm_linux_virtual_machine", {"name": "v", "resource_group_name": RG, "size": "Standard_B2s"}, {"size": ""}),
    ("azurerm_log_analytics_workspace", {"name": "w", "resource_group_name": RG, "retention_in_days": 60}, {"retention_in_days": 30}),
    ("azurerm_log_analytics_workspace", {"name": "w", "resource_group_name": RG, "daily_quota_gb": 5}, {"daily_quota_gb": 1}),
    ("azurerm_virtual_network_peering", {"name": "p", "resource_group_name": RG, "allow_forwarded_traffic": True}, {"allow_forwarded_traffic": False}),
    ("azurerm_storage_account", {"name": "s", "resource_group_name": RG, "https_only": True}, {"https_only": False}),  # enabled true to false
    ("azurerm_storage_account", {"name": "s", "resource_group_name": RG, "public_network_access_enabled": True}, {"public_network_access_enabled": False}),
    ("azurerm_virtual_network", {**VNET, "enabled": True}, {"enabled": False}),
    ("azurerm_resource_group", {"name": RG, "location": "uaenorth"}, {"location": "westeurope"}),   # a move is a replace
    ("azurerm_virtual_network", VNET, {"name": "renamed"}),
    ("azurerm_virtual_network", VNET, {"location": "westeurope"}),
    ("azurerm_public_ip", {"name": "ip", "resource_group_name": RG}, {"sku": "Standard"}),          # not an allowed path
    ("azurerm_route_table", {"name": "rt", "resource_group_name": RG, "route": [{"name": "r", "next_hop_type": "None"}]}, {"route": []}),
    ("azurerm_private_endpoint", {"name": "pe", "resource_group_name": RG, "subnet_id": f"{RID}/virtualNetworks/v/subnets/s"}, {"subnet_id": "x"}),
    ("azurerm_consumption_budget_subscription", {"name": "b", "amount": 100}, {"amount": 10}),      # no longer free code's to create or change
    ("azapi_resource", {"name": "x", "parent_id": f"/subscriptions/{SUB}/resourceGroups/{RG}"}, {"body": {"properties": {}}}),
])
def test_every_removal_row_written_as_free_code_is_refused(rtype, before, after):
    refusals = free(update(rtype, before, **after))
    assert refusals and not any("the type" in r for r in refusals), (rtype, after, refusals)


def test_a_free_code_update_of_a_type_with_no_entry_is_refused_even_for_a_tag():
    assert free(update("azurerm_linux_virtual_machine", {"name": "v", "resource_group_name": RG, "tags": {}}, tags={"a": "b"}))
    assert free(update("azurerm_subnet", {"name": "s", "resource_group_name": RG}, name="s2"))


def test_every_free_code_may_change_entry_is_a_tag_or_a_proven_addition():
    allowed = {"tags", "address_space", "worker_count", "retention_in_days", "daily_quota_gb", "allow_forwarded_traffic", "allow_gateway_transit",
               "source_address_prefixes", "destination_address_prefixes", "destination_port_ranges"}
    for rtype, paths in gate.MAY_CHANGE.items():
        assert set(paths) <= allowed, (rtype, paths)
        assert not any(p in gate.NEVER_CHANGE for p in paths), rtype
        assert not gate._type_denied(rtype), rtype


def test_a_free_code_create_over_an_inventory_id_is_refused_and_so_is_one_the_gate_cannot_name():
    vnet = f"{RID}/virtualNetworks/vnet"
    assert free(rc("azurerm_virtual_network", ["create"], None, VNET), inventory={vnet.lower()})
    assert free(rc("azurerm_virtual_network", ["create"], None, VNET), inventory={f"{RID}/virtualNetworks/other".lower()}) == []
    named_later = {**VNET, "name": "x"}
    assert free(rc("azurerm_virtual_network", ["create"], None, named_later, unknown={"id": True, "name": True}))
    for rtype in ("azurerm_monitor_diagnostic_setting", "azurerm_network_watcher_flow_log", "azurerm_firewall_policy_rule_collection_group",
                  "azurerm_consumption_budget_subscription"):
        assert free(rc(rtype, ["create"], None, {"name": "same-name-as-one-that-exists", "resource_group_name": RG})), rtype


def test_free_code_cannot_patch_an_existing_resource_with_azapi_update_resource():
    group = f"/subscriptions/{SUB}/resourceGroups/{RG}"
    change = rc("azapi_update_resource", ["create"], None,
                {"type": "Microsoft.Network/networkSecurityGroups@2024-05-01", "resource_id": f"{group}/providers/Microsoft.Network/networkSecurityGroups/nsg",
                 "body": {"properties": {"securityRules": []}}})
    assert free(change)
    assert verdict(change, draft={**DRAFT, "template": "tags"}) == []   # a reviewed template may


def test_free_code_may_link_a_subnet_it_creates_but_not_one_that_exists():
    link = "azurerm_subnet_network_security_group_association"
    after = {"subnet_id": f"{RID}/virtualNetworks/vnet/subnets/s", "network_security_group_id": f"{RID}/networkSecurityGroups/nsg"}
    assert free(rc(link, ["create"], None, after, unknown={"id": True, "subnet_id": True})) == []
    assert free(rc(link, ["create"], None, after, unknown={"id": True}))


@pytest.mark.parametrize("key,value", [("custom_data", "IyEvYmluL3No"), ("user_data", "x"), ("customData", "x"), ("run_command", {"a": 1}),
                                       ("extensionProfile", {"extensions": [{"name": "x"}]}), ("extension", [{"name": "x"}])])
def test_free_code_cannot_put_code_that_runs_in_azure_into_a_planned_value_even_when_built_by_a_template(key, value):
    after = {"name": "vm", "resource_group_name": RG, "location": "uaenorth", "size": "Standard_B2s", "body": {"properties": {"osProfile": {key: value}}}, key: value}
    assert free(rc("azurerm_linux_virtual_machine", ["create"], None, after))
    assert free(rc("azurerm_linux_virtual_machine", ["create"], None, {k: v for k, v in after.items() if k != key and k != "body"},
                   unknown={"body": {"properties": {"osProfile": {key: True}}}}))
    empty = {**after, key: None, "body": {"properties": {"osProfile": {key: None}}}}
    assert free(rc("azurerm_linux_virtual_machine", ["create"], None, empty)) == []


@pytest.mark.parametrize("provider", ["registry.opentofu.org/hashicorp/external", "registry.opentofu.org/hashicorp/http",
                                      "registry.opentofu.org/hashicorp/null", "registry.opentofu.org/hashicorp/local",
                                      "registry.opentofu.org/hashicorp/random", "registry.opentofu.org/hashicorp/archive",
                                      "registry.opentofu.org/hashicorp/azurerm-evil", "example.com/x/azurerm"])
def test_free_code_that_reached_another_provider_is_refused_in_the_plan_too(provider):
    plan = {"resource_changes": [rc("azurerm_virtual_network", ["create"], None, VNET, unknown={"id": True})],
            "configuration": {"provider_config": {"azurerm": {"full_name": AZURERM}, "other": {"full_name": provider}}}}
    assert gate.check_plan(plan, RAILS, DRAFT, set())[1]


def test_free_code_that_builds_a_denied_azure_type_is_refused_in_the_plan():
    for arm in ("Microsoft.Authorization/roleAssignments", "Microsoft.Resources/deploymentScripts", "Microsoft.Resources/deployments",
                "Microsoft.Compute/virtualMachines/extensions", "Microsoft.Insights/autoscaleSettings"):
        after = {"name": "x", "parent_id": f"/subscriptions/{SUB}/resourceGroups/{RG}", "type": f"{arm}@2024-01-01"}
        assert free(rc("azapi_resource", ["create"], None, after)), arm
    unknown_type = {"name": "x", "parent_id": f"/subscriptions/{SUB}/resourceGroups/{RG}"}
    assert free(rc("azapi_resource", ["create"], None, unknown_type, unknown={"type": True}))


def test_free_code_scope_region_and_count_are_enforced_like_any_change():
    assert verdict(rc("azurerm_virtual_network", ["create"], None, VNET), draft={**DRAFT, "subscription": "other"})
    assert verdict(rc("azurerm_virtual_network", ["create"], None, {**VNET, "location": "westeurope"}))
    assert verdict(rc("azurerm_virtual_network", ["create"], None, {**VNET, "resource_group_name": "rg-elsewhere"}))
    many = [rc("azurerm_virtual_network", ["create"], None, VNET, name=str(i)) for i in range(4)]
    assert verdict(*many)


# --------------------------------------------------------------- the scanner reads what OpenTofu reads, or refuses

@pytest.mark.parametrize("code", [
    # a marker OpenTofu accepts and the scanner does not read (a dash, another alphabet, a space after it)
    'locals {\n  a = <<EOT-X\ntext\nEOT-X\n}\n',
    'locals {\n  a = <<ÉOT\ntext\nÉOT\n}\n',
    'locals {\n  a = <<EOT \ntext\nEOT\n}\n',
    # a line that is the marker, inside a template that is still open: OpenTofu does not end the heredoc there
    'locals {\n  a = <<EOT\n${\nEOT\n}\nEOT\n}\n',
    'locals {\n  a = { b = <<true\n${\ntrue\n}{\ntrue\n  }\n}\n',
    # a comment inside a template of a string, where a quote or a brace in it would be read as code
    'locals {\n  a = "${ 1 /* x */ }"\n}\n',
    'locals {\n  a = "${ 1 # x\n }"\n}\n',
])
def test_text_the_scanner_would_read_differently_from_opentofu_is_refused(code):
    refusals = gate.check_code({"main.tf": code})
    assert refusals and "could not be read" in refusals[0], refusals


def test_a_plain_heredoc_with_a_closed_template_is_still_accepted():
    assert gate.check_code({"main.tf": 'locals {\n  a = <<-EOT\n    ${upper("x")} $${literal} # not a comment\n  EOT\n}\n'}) == []


def test_a_sensitive_value_inside_a_nested_block_is_masked_in_the_summary_and_kept_out_of_the_digest():
    block = lambda secret: [{"name": "idp", "client_secret": secret}]  # noqa: E731
    def plan(secret):
        change = rc("azurerm_storage_account", ["create"], None, {"name": "st1", "resource_group_name": RG, "auth": block(secret),
                                                                   "body": {"properties": {"value": secret}}}, unknown={"id": True})
        change["change"]["after_sensitive"] = {"auth": [{"client_secret": True}], "body": {"properties": {"value": True}}}
        return {"resource_changes": [change]}
    shown = json.dumps(gate.summarise(plan("s3cr3t-value")))
    assert "s3cr3t-value" not in shown and gate.SENSITIVE in shown
    assert gate.plan_digest(plan("s3cr3t-value"), {}, {}) == gate.plan_digest(plan("another-value"), {}, {})


@pytest.mark.parametrize("rtype,attr,value", [
    ("azurerm_firewall_policy_rule_collection_group", "firewall_policy_id", "/subscriptions/{s}/resourceGroups/rg-other/providers/Microsoft.Network/firewallPolicies/fp"),
    ("azurerm_firewall_policy_rule_collection_group", "firewall_policy_id", "/subscriptions/99999999-2222-3333-4444-555555555555/resourceGroups/{g}/providers/Microsoft.Network/firewallPolicies/fp"),
    ("azurerm_monitor_diagnostic_setting", "target_resource_id", "/subscriptions/{s}/resourceGroups/rg-other/providers/Microsoft.Network/virtualNetworks/v"),
    ("azurerm_private_dns_zone_virtual_network_link", "private_dns_zone_id", "/subscriptions/{s}/resourceGroups/rg-other/providers/Microsoft.Network/privateDnsZones/privatelink.blob.core.windows.net"),
])
def test_a_resource_made_on_something_outside_the_rails_is_refused(rtype, attr, value):
    rails = {**RAILS, "resource_types": [rtype]}
    draft = {**DRAFT, "template": "reviewed", "may_change": []}
    outside = rc(rtype, ["create"], None, {"name": "x", attr: value.format(s=SUB, g=RG)}, unknown={"id": True})
    assert any("outside the subscription or resource groups" in r for r in verdict(outside, draft=draft, rails=rails))
    inside = rc(rtype, ["create"], None, {"name": "x", attr: value.format(s=SUB, g=RG).replace("rg-other", RG).replace("99999999", "11111111")},
                unknown={"id": True})
    assert verdict(inside, draft=draft, rails=rails) == []


def test_free_code_cannot_import_or_move_an_existing_resource_and_then_change_it():
    """`import { to = ... id = "<existing>" }` plus a widened rule planned as one update (audit 3, L6)."""
    imported = update("azurerm_network_security_rule", NSG_RULE, source_address_prefixes=["10.0.0.0/8", "10.1.0.0/16"])
    imported["change"]["importing"] = {"id": f"{RID}/networkSecurityGroups/nsg/securityRules/allow"}
    assert free(imported)
    assert verdict(imported, draft={**DRAFT, "template": "adopt"}) == []   # adopting from the inventory may
    moved = {**update("azurerm_network_security_rule", NSG_RULE, tags={"a": "b"}), "previous_address": "azurerm_network_security_rule.old"}
    assert free(moved)
    quiet = rc("azurerm_network_security_rule", ["no-op"], NSG_RULE, NSG_RULE, importing={"id": "x"})
    assert free(quiet)
