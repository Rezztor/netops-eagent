"""The Diagnostics role grants probes only, and a stop stays a suggestion in every troubleshooting skill."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GUIDE = ROOT / "docs/guides/azure.md"
SKILLS = (
    "azure-troubleshooting", "azure-vm-reachability", "azure-dns", "azure-peering",
    "azure-webapp-down", "azure-who-changed", "azure-firewall-drop", "azure-onprem-reach", "azure-microsoft-guidance",
)
# Research R2 section 8.1, casing from Microsoft's Network Watcher RBAC page and the gateway REST paths.
PROBES = (
    "*/read",
    "Microsoft.Network/networkWatchers/ipFlowVerify/action",
    "Microsoft.Network/networkWatchers/ipFlowVerify/read",
    "Microsoft.Network/networkWatchers/nextHop/action",
    "Microsoft.Network/networkWatchers/nextHop/read",
    "Microsoft.Network/networkWatchers/securityGroupView/action",
    "Microsoft.Network/networkWatchers/networkConfigurationDiagnostic/action",
    "Microsoft.Network/networkWatchers/networkConfigurationDiagnostic/read",
    "Microsoft.Network/networkWatchers/connectivityCheck/action",
    "Microsoft.Network/networkWatchers/connectivityCheck/read",
    "Microsoft.Network/networkInterfaces/effectiveRouteTable/action",
    "Microsoft.Network/networkInterfaces/effectiveNetworkSecurityGroups/action",
    "Microsoft.Network/virtualNetworkGateways/getBgpPeerStatus/action",
    "Microsoft.Network/virtualNetworkGateways/getLearnedRoutes/action",
    "Microsoft.Network/virtualNetworkGateways/getAdvertisedRoutes/action",
    "Microsoft.Network/virtualNetworkGateways/getEffectiveRoutes/action",
    "Microsoft.Network/connections/getIkeSas/action",
)


def _role() -> dict:
    block = re.search(r"```json\n(\{.*?\n\})\n```", GUIDE.read_text(), re.S)
    assert block, "docs/guides/azure.md has no Diagnostics role JSON"
    return json.loads(block.group(1))


def test_diag_role_adds_probes_and_nothing_that_changes_azure():
    role = _role()
    actions = role["Actions"]
    assert role["Name"] == "Rezztor Diagnostics" and role["IsCustom"] is True
    assert role["DataActions"] == [] and role["NotDataActions"] == []
    assert actions == list(PROBES)
    assert role["AssignableScopes"] == ["/subscriptions/REPLACE_WITH_SUBSCRIPTION_ID"]
    for action in actions:
        assert not action.lower().endswith(("/write", "/delete"))
        assert not any(bad in action.lower() for bad in
                       ("packetcapture", "troubleshoot", "runcommand", "sharedkey", "listkeys", "appsettings"))


NEEDED = {
    "azure-vm-reachability": ("vm_instance_view", "resource_health", "ip_flow_verify", "next_hop",
                              "effective_routes", "effective_security_rules", "connection_check"),
    "azure-dns": ("private_dns_links", "private_dns_records", "private_endpoint"),
    "azure-peering": ("vnet_peerings", "next_hop", "ip_flow_verify"),
    "azure-webapp-down": ("site_state", "resource_health", "service_health", "site_metrics",
                          "plan_metrics", "site_detectors", "activity_log"),
    "azure-who-changed": ("activity_log",),
}


def _catalogue() -> set[str]:
    return set(re.findall(r"^- name: (\S+)$", (ROOT / "plugins/_ea_azure/reads.yaml").read_text(), re.M))


def test_skills_name_only_catalogue_reads():
    names = _catalogue()
    assert NEEDED["azure-vm-reachability"][0] in names  # the catalogue is the live list, not a guess
    for skill, needed in NEEDED.items():
        text = (ROOT / "plugins/_ea_azure/skills" / skill / "SKILL.md").read_text()
        for read in needed:
            assert read in names and f"`{read}`" in text, f"{skill} names a read that is not in reads.yaml: {read}"


def test_troubleshooter_may_ask_azure_and_cannot_change_it():
    cfg = json.loads((ROOT / "agents/network-troubleshooter/plugins/_tool_access/config.json").read_text())
    prompt = (ROOT / "agents/network-troubleshooter/prompts/agent.system.main.specifics.md").read_text()
    assert "plugin:_ea_azure:azure_read" in cfg["allowed"]
    assert "azure-troubleshooting" in prompt and "azure_read" in prompt
    for blocked in ("azure_change", "azure_action", "azure_apply", "azure_pull", "code_execution"):
        assert not any(blocked in item for item in cfg["allowed"])


def test_troubleshooting_skills_suggest_a_stop_and_never_do_it():
    from helpers import skills

    for name in SKILLS:
        path = ROOT / "plugins/_ea_azure/skills" / name / "SKILL.md"
        assert skills.validate_skill_md(path) == [], path
        text = path.read_text().lower()
        assert "suggestion" in text
        assert "never" in text and ("stop" in text or "delet" in text)
