"""What phase 2 W1 lays down for the Azure change plugin: the permission rows, the tool access and the skeleton.

Asserted on the policy tables and on files, so it holds whichever worker's handler lands first.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from helpers import api_policy, dotenv, rbac, tool_permissions, tool_policy

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "_ea_azure_change"

ENDPOINTS = {
    "plugins/_ea_azure_change/azure_approve": rbac.PERM_NETWORK_APPROVE,
    "plugins/_ea_azure_change/azure_edit": rbac.PERM_TOOL_NETWORK,
    "plugins/_ea_azure_change/azure_document": rbac.PERM_TOOL_NETWORK,
    "plugins/_ea_azure_change/azure_change_config": rbac.PERM_PLUGINS_MANAGE,
    "plugins/_ea_azure_change/azure_changes": rbac.PERM_INVENTORY_VIEW,
    "plugins/_ea_azure/azure_read_approve": rbac.PERM_NETWORK_APPROVE,
    "plugins/_ea_azure/azure_read_edit": rbac.PERM_TOOL_NETWORK,
}
TOOLS = ("azure_change", "azure_apply", "azure_action", "azure_read")


@pytest.mark.parametrize("endpoint,permission", ENDPOINTS.items())
def test_every_azure_change_endpoint_is_in_the_policy_table(endpoint, permission):
    assert api_policy.required_permission_for(endpoint) == permission


@pytest.mark.parametrize("tool", TOOLS)
def test_every_azure_change_tool_needs_the_network_tool_permission(tool):
    assert tool_permissions.required_permission(tool) == rbac.PERM_TOOL_NETWORK
    assert not tool_permissions.is_allowed(tool, rbac.ROLE_USER)
    assert tool_permissions.is_allowed(tool, rbac.ROLE_POWER)


def _allowed(profile: str, tool_id: str) -> bool:
    agent = SimpleNamespace(config=SimpleNamespace(profile=profile), context=SimpleNamespace(get_data=lambda *a, **k: None))
    return tool_policy.resolve_tool(agent, tool_id.rsplit(":", 1)[-1], canonical_id=tool_id).allowed


@pytest.mark.parametrize("tool", ("azure_change", "azure_apply", "azure_action"))
def test_only_the_operator_profile_may_draft_and_apply_azure_changes(tool):
    tool_id = f"plugin:_ea_azure_change:{tool}"
    assert _allowed("primary", tool_id)
    for profile in ("network-troubleshooter", "network-researcher", "automation-engineer"):
        assert not _allowed(profile, tool_id)


def test_the_job_token_is_not_an_agent_visible_secret_name():
    assert dotenv.KEY_CLOUD_JOB_TOKEN == "REZZTOR_CLOUD_JOB_TOKEN"
    assert not dotenv.KEY_CLOUD_JOB_TOKEN.startswith("API_KEY")


def test_the_plugin_is_off_by_default_and_not_bundled_as_always_on():
    manifest = yaml.safe_load((PLUGIN / "plugin.yaml").read_text())
    assert manifest["name"] == "_ea_azure_change" and PLUGIN.name == manifest["name"]
    assert not manifest.get("always_enabled")
    assert (PLUGIN / ".toggle-0").exists() and not (PLUGIN / ".toggle-1").exists()
    for name in ("AGENTS.md", "README.md", "default_config.yaml"):
        assert (PLUGIN / name).is_file()


def _config() -> dict:
    return yaml.safe_load((PLUGIN / "default_config.yaml").read_text())


def test_the_default_config_points_at_the_socket_and_holds_the_contract_guard_rails():
    config = _config()
    assert config["executor_url"] == "unix:///run/cloud-socket/cloud.sock"
    rails = config["guardrails"]
    assert rails["subscriptions"] == [] and rails["allow_free_code"] is False and rails["allow_unknown_price"] is False
    assert (rails["usd_month_per_change"], rails["usd_month_total"], rails["low_risk_usd_month"]) == (250, 1000, 20)
    assert rails["document_valid_days"] == 14 and rails["vm_sizes_max_vcpu"] == 8 and rails["max_resources"] == 40


def test_deleting_is_not_configurable():
    """The owner's first rule: no setting, however named, for removing or stopping anything."""
    text = (PLUGIN / "default_config.yaml").read_text().lower()
    code = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    for word in ("delete", "destroy", "deallocate", "restart", "stop", "scale_in"):
        assert word not in code, word


def test_the_default_reference_pattern_accepts_a_number_and_refuses_the_rest():
    pattern = re.compile(_config()["guardrails"]["reference_pattern"], re.IGNORECASE)
    for good in ("CR-12345", "cr12345", "SR-0001234567", "Sr-1234"):
        assert pattern.fullmatch(good), good
    for bad in ("", "cr 12", "CR-123", "CR-12345678901", "INC-12345", "CR-12345 ", "xCR-12345", "CR-12345; drop"):
        assert not pattern.fullmatch(bad), bad
    assert not pattern.fullmatch("")
