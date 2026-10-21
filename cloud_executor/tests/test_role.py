"""The Azure role for key C (`role.json`) against the build contract: what it grants, and the 90 actions it withholds.

The role is the second wall behind `gate.check_plan`. Widening `Actions` or dropping a `NotActions` entry is a security
change: extend this file first. No network.
"""
import json
import re
from pathlib import Path

import pytest

ROLE = json.loads((Path(__file__).resolve().parents[1] / "role.json").read_text())

#: The contract's 90 withheld actions, copied from docs/plans/2026-10-03-azure-operations-build.md.
NOT_ACTIONS = (
    "*/delete",
    "Microsoft.Compute/virtualMachineScaleSets/delete/action",
    "Microsoft.Compute/cloudServices/delete/action",
    "Microsoft.Compute/virtualMachines/deletePreservedOSDisk/action",
    "Microsoft.ContainerService/managedClusters/agentPools/deleteMachines/action",
    "Microsoft.KeyVault/locations/deletedVaults/purge/action",
    "Microsoft.KeyVault/locations/deletedManagedHsms/purge/action",
    "Microsoft.OperationalInsights/workspaces/purge/action",
    "Microsoft.Resources/subscriptions/resourceGroups/moveResources/action",
    "Microsoft.Compute/virtualMachines/deallocate/action",
    "Microsoft.Compute/virtualMachines/powerOff/action",
    "Microsoft.Compute/virtualMachines/restart/action",
    "Microsoft.Compute/virtualMachines/redeploy/action",
    "Microsoft.Compute/virtualMachines/reimage/action",
    "Microsoft.Compute/virtualMachines/generalize/action",
    "Microsoft.Compute/virtualMachines/performMaintenance/action",
    "Microsoft.Compute/virtualMachines/simulateEviction/action",
    "Microsoft.Compute/virtualMachines/installPatches/action",
    "Microsoft.Compute/virtualMachines/attachDetachDataDisks/action",
    "Microsoft.Compute/virtualMachines/rollbackOSDisk/action",
    "Microsoft.Compute/virtualMachineScaleSets/deallocate/action",
    "Microsoft.Compute/virtualMachineScaleSets/powerOff/action",
    "Microsoft.Compute/virtualMachineScaleSets/restart/action",
    "Microsoft.Compute/virtualMachineScaleSets/redeploy/action",
    "Microsoft.Compute/virtualMachineScaleSets/reimage/action",
    "Microsoft.Compute/virtualMachineScaleSets/reimageAll/action",
    "Microsoft.Compute/virtualMachineScaleSets/virtualMachines/deallocate/action",
    "Microsoft.Compute/virtualMachineScaleSets/virtualMachines/powerOff/action",
    "Microsoft.Compute/virtualMachineScaleSets/virtualMachines/restart/action",
    "Microsoft.Compute/virtualMachineScaleSets/virtualMachines/redeploy/action",
    "Microsoft.Compute/virtualMachineScaleSets/virtualMachines/reimage/action",
    "Microsoft.Compute/virtualMachineScaleSets/virtualMachines/reimageAll/action",
    "Microsoft.Web/sites/stop/Action",
    "Microsoft.Web/sites/restart/Action",
    "Microsoft.Web/sites/slotsswap/Action",
    "Microsoft.Web/sites/applySlotConfig/Action",
    "Microsoft.Web/sites/resetSlotConfig/Action",
    "Microsoft.Web/sites/slots/stop/Action",
    "Microsoft.Web/sites/slots/restart/Action",
    "Microsoft.Web/sites/slots/slotsswap/Action",
    "Microsoft.Web/serverfarms/restartSites/Action",
    "Microsoft.ContainerService/managedClusters/stop/action",
    "Microsoft.ContainerInstance/containerGroups/stop/action",
    "Microsoft.ContainerInstance/containerGroups/restart/action",
    "Microsoft.DBforPostgreSQL/flexibleServers/stop/action",
    "Microsoft.DBforPostgreSQL/flexibleServers/restart/action",
    "Microsoft.DBforMySQL/flexibleServers/stop/action",
    "Microsoft.DBforMySQL/flexibleServers/restart/action",
    "Microsoft.DBforMySQL/flexibleServers/failover/action",
    "Microsoft.Sql/servers/databases/pause/action",
    "Microsoft.Sql/servers/databases/failover/action",
    "Microsoft.Sql/managedInstances/stop/action",
    "Microsoft.Sql/managedInstances/failover/action",
    "Microsoft.Cache/redis/stop/action",
    "Microsoft.Cache/redis/forceReboot/action",
    "Microsoft.Network/applicationGateways/stop/action",
    "Microsoft.Network/applicationGateways/restart/action",
    "Microsoft.Network/networkVirtualAppliances/restart/action",
    "microsoft.network/virtualnetworkgateways/reset/action",
    "microsoft.network/vpngateways/reset/action",
    "Microsoft.Storage/storageAccounts/failover/action",
    "Microsoft.Compute/virtualMachineScaleSets/scale/action",
    "Microsoft.Insights/AutoscaleSettings/Write",
    "Microsoft.Insights/AutoscaleSettings/Scaledown/Action",
    "Microsoft.Resources/deployments/write",
    "Microsoft.Resources/deploymentStacks/write",
    "Microsoft.Resources/deploymentScripts/write",
    "Microsoft.Automation/*",
    "Microsoft.Storage/storageAccounts/managementPolicies/write",
    "Microsoft.Compute/virtualMachines/extensions/write",
    "Microsoft.Compute/virtualMachines/runCommands/write",
    "Microsoft.Compute/virtualMachines/runCommand/action",
    "Microsoft.Compute/virtualMachineScaleSets/extensions/write",
    "Microsoft.Compute/virtualMachineScaleSets/virtualMachines/runCommands/write",
    "Microsoft.Compute/virtualMachineScaleSets/virtualMachines/runCommand/action",
    "Microsoft.ContainerService/managedClusters/runCommand/action",
    "Microsoft.ContainerInstance/containerGroups/write",
    "Microsoft.Web/sites/publish/Action",
    "Microsoft.Web/sites/publishxml/Action",
    "microsoft.web/sites/extensions/write",
    "Microsoft.Web/sites/sourcecontrols/Write",
    "Microsoft.Authorization/*/Write",
    "Microsoft.Authorization/*/Delete",
    "Microsoft.Authorization/elevateAccess/Action",
    "Microsoft.Blueprint/blueprintAssignments/write",
    "Microsoft.Compute/galleries/share/action",
    "Microsoft.Purview/consents/write",
    "Microsoft.Resources/deploymentStacks/manageDenySetting/action",
    "Microsoft.Subscription/cancel/action",
    "Microsoft.Subscription/enable/action",
)
STOP_WORDS = ("delete", "deallocate", "poweroff", "restart", "stop", "redeploy", "reimage", "failover", "reset", "swap",
              "scale", "pause", "forcereboot", "purge")


def test_the_role_withholds_every_action_the_contract_names():
    assert len(NOT_ACTIONS) == 90 and len(set(NOT_ACTIONS)) == 90
    assert not set(NOT_ACTIONS) - set(ROLE["NotActions"]), "a withheld action went missing from role.json"


def test_the_grant_is_read_write_and_a_short_list_of_named_actions():
    actions = ROLE["Actions"]
    assert actions[:2] == ["*/read", "*/write"]
    for action in actions:
        assert "*" not in action or action in ("*/read", "*/write")
        assert not action.lower().endswith("/delete")
        assert action not in ROLE["NotActions"]
    named = [a.lower() for a in actions[2:]]
    assert all(a.endswith(("/join/action", "/peer/action", "/joinviaserviceendpoint/action", "/start/action")) for a in named), named
    assert not [a for a in named if any(w in a.rsplit("/", 2)[-2] for w in STOP_WORDS if w != "start")]


def test_there_are_no_data_actions_and_the_scope_is_one_subscription_to_fill_in():
    assert ROLE["DataActions"] == [] and ROLE["NotDataActions"] == []
    assert ROLE["IsCustom"] is True and len(ROLE["AssignableScopes"]) == 1


@pytest.mark.parametrize("fragment", ["*/delete", "virtualMachines/deallocate", "virtualMachines/restart", "sites/stop",
                                      "sites/slotsswap", "Authorization/*/Write", "deployments/write"])
def test_the_named_stop_and_delete_actions_are_withheld(fragment):
    assert any(fragment.lower() in a.lower() for a in ROLE["NotActions"])


def test_role_json_names_no_stop_like_grant_a_reviewer_would_miss():
    for action in ROLE["Actions"]:
        assert not re.search(r"/(delete|deallocate|poweroff|restart|stop|redeploy|reimage|failover|reset)(/|$)", action, re.I)


def test_the_guide_an_administrator_pastes_from_is_the_role_the_image_checks_against():
    guide = (Path(__file__).resolve().parents[2] / "docs" / "guides" / "azure-changes.md").read_text(encoding="utf-8")
    assert json.loads(re.search(r"```json\n(.*?)```", guide, re.S).group(1)) == ROLE


def test_the_portal_layout_of_the_role_is_the_same_role():
    """The portal's "Start from JSON" refuses the command-line layout (seen on the real tenant, 2026-10-03)."""
    portal = json.loads((Path(__file__).resolve().parents[1] / "role.portal.json").read_text(encoding="utf-8"))["properties"]
    permissions = portal["permissions"][0]
    assert (portal["roleName"], portal["description"], portal["assignableScopes"]) == (ROLE["Name"], ROLE["Description"], ROLE["AssignableScopes"])
    assert (permissions["actions"], permissions["notActions"]) == (ROLE["Actions"], ROLE["NotActions"])
    assert (permissions["dataActions"], permissions["notDataActions"]) == (ROLE.get("DataActions", []), ROLE.get("NotDataActions", []))
