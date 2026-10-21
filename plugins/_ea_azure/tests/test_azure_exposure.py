"""Every exposure reason and a negative for each. Pure: no database."""

from plugins._ea_azure.helpers import exposure

NIC, VM, NSG = ("microsoft.network/networkinterfaces", "microsoft.compute/virtualmachines",
                "microsoft.network/networksecuritygroups")
PIP = {"kind": "public_ip", "id": "p1"}


def why(type_, props=None, links=()):
    return exposure.reasons({"type": type_, "props": props or {}}, list(links))


def rule(**kw):
    return {"direction": "Inbound", "access": "Allow", "sources": ["*"], **kw}


def test_public_ip_on_a_card_and_on_the_machine_that_owns_it():
    assert why(NIC, links=[PIP]) == ["public_ip"]
    assert why(NIC, links=[{"kind": "nsg"}]) == []
    assert why(VM, links=[{"kind": "nic", "links_out": [PIP]}]) == ["public_ip"]
    assert why(VM, links=[{"kind": "nic", "links_out": [{"kind": "nsg"}]}]) == []
    assert why(VM, links=[PIP]) == []  # a machine's own links are not enough, its card has to carry the address


def test_open_rule_needs_inbound_allow_from_anywhere():
    for source in ("*", "Internet", "internet", "0.0.0.0/0", "::/0"):
        assert why(NSG, {"rules": [rule(sources=["10.0.0.0/8", source])]}) == ["open_rule"], source
    assert why(NSG, {"rules": [rule(access="Deny")]}) == []
    assert why(NSG, {"rules": [rule(direction="Outbound")]}) == []
    assert why(NSG, {"rules": [rule(sources=["10.0.0.0/8"])]}) == []
    assert why(NSG, {"rules": []}) == [] and why(NSG) == []
    assert why("microsoft.compute/virtualmachines", {"rules": [rule()]}) == []  # only a security group has rules


def test_public_access_is_case_insensitive_and_storage_and_vaults_also_need_default_allow():
    assert why("microsoft.web/sites", {"public_network_access": "ENABLED"}) == ["public_access"]
    assert why("microsoft.web/sites", {"public_network_access": "Disabled"}) == []
    assert why("microsoft.sql/servers", {}) == []
    for type_ in ("microsoft.storage/storageaccounts", "microsoft.keyvault/vaults"):
        assert why(type_, {"public_network_access": "Enabled", "default_network_action": "ALLOW"}) == ["public_access"]
        assert why(type_, {"public_network_access": "Enabled", "default_network_action": "Deny"}) == []
        assert why(type_, {"public_network_access": "Enabled"}) == []


def test_public_blob_and_external_ingress():
    assert why("microsoft.storage/storageaccounts", {"allow_blob_public_access": True}) == ["public_blob"]
    assert why("microsoft.storage/storageaccounts", {"allow_blob_public_access": "true"}) == ["public_blob"]
    assert why("microsoft.storage/storageaccounts", {"allow_blob_public_access": False}) == []
    assert why("microsoft.app/containerapps", {"ingress_external": True}) == ["external_ingress"]
    assert why("microsoft.app/containerapps", {"ingress_external": False}) == []


def test_reasons_come_in_the_documented_order():
    props = {"public_network_access": "Enabled", "default_network_action": "Allow", "allow_blob_public_access": True,
             "ingress_external": True}
    assert why("microsoft.storage/storageaccounts", props) == ["public_access", "public_blob", "external_ingress"]
