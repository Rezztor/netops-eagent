"""Executor sync: render/diff are pure; sync is tested with the store and executor client mocked."""

from __future__ import annotations

import asyncio

import pytest

from helpers import rbac

from plugins._ea_ansible.helpers import admin_client
from plugins._ea_inventory.helpers import executor_sync as es

LAB = {"ansible_host": "10.0.0.9", "vars": {}}


def dev(name, platform="sonic", ip="10.0.0.1", status="active"):
    return {"name": name, "platform": platform, "mgmt_ip": ip, "status": status}


def existing():
    return {
        "hosts": {
            "lab-sonic": LAB,
            "old-sw": {"ansible_host": "10.0.0.2", "vars": {"eagent_managed": "inventory"}},
        },
        "groups": {
            "manual": {"hosts": ["lab-sonic", "old-sw"], "vars": {"a": 1}, "credential": "mine"},
            "inv_sonic": {"hosts": ["old-sw"], "vars": {}, "credential": "kept-cred"},
        },
    }


def test_manual_hosts_and_groups_survive_and_managed_hosts_are_tagged():
    doc = es.render([dev("sw1")], existing(), {})
    assert doc["hosts"]["lab-sonic"] == LAB
    assert doc["hosts"]["sw1"] == {"ansible_host": "10.0.0.1", "vars": {"eagent_managed": "inventory"}}
    assert doc["groups"]["manual"]["credential"] == "mine"
    assert doc["groups"]["inv_sonic"]["hosts"] == ["sw1"]
    assert doc["groups"]["inv_sonic"]["vars"]["ansible_network_os"] == "dellemc.enterprise_sonic.sonic"


def test_inactive_device_is_removed_everywhere():
    doc = es.render([dev("old-sw", status="offline")], existing(), {})
    assert "old-sw" not in doc["hosts"]
    assert doc["groups"]["manual"]["hosts"] == ["lab-sonic"]
    assert "inv_sonic" not in doc["groups"]
    assert es.diff(existing(), doc)["removed"] == ["old-sw"]


def test_devices_without_a_usable_ip_or_platform_are_left_out():
    doc = es.render([dev("a", ip="nope"), dev("b", platform="other"), dev("c", ip="")], {}, {})
    assert doc == {"groups": {}, "hosts": {}}


def test_credentials_are_names_only_and_mapping_wins():
    doc = es.render([dev("sw1")], existing(), {"sonic": "lab-cred"})
    assert doc["groups"]["inv_sonic"]["credential"] == "lab-cred"
    flat = repr(doc).lower()
    assert "password" not in flat and "secret" not in flat


def test_group_credential_is_preserved_when_no_mapping():
    assert es.render([dev("sw1")], existing(), {})["groups"]["inv_sonic"]["credential"] == "kept-cred"


def test_render_does_not_mutate_its_input():
    old = existing()
    es.render([dev("sw1")], old, {})
    assert old == existing()


def test_diff_added_changed_and_unsupported_platform_warning(monkeypatch):
    monkeypatch.setattr(es, "_supported_platforms", lambda: {"ios", "iosxe", "sonic"})  # every shipped platform is covered now
    new = es.render([dev("sw1"), dev("fw1", "panos", "10.0.0.5"), dev("sw2", "ios", "10.0.0.6")], existing(), {})
    result = es.diff(existing(), new)
    assert result["added"] == ["fw1", "sw1", "sw2"]
    assert "group:inv_sonic" in result["changed"]
    assert any("'panos'" in w for w in result["warnings"])
    assert not any("'ios'" in w or "'sonic'" in w for w in result["warnings"])


# --- sync --------------------------------------------------------------------

@pytest.fixture
def wired(monkeypatch):
    calls = {"put": []}
    monkeypatch.setattr(es, "_devices", lambda: [dev("sw1")])
    monkeypatch.setattr(es, "_credential_map", lambda agent: {})
    monkeypatch.setattr(admin_client, "get_inventory", lambda agent=None: {"inventory": existing()})
    monkeypatch.setattr(admin_client, "put_inventory", lambda agent, doc: calls["put"].append(doc) or {})
    return calls


def test_dry_run_changes_nothing(wired):
    result = es.sync(dry_run=True)
    assert result["ok"] and result["applied"] is False and "sw1" in result["diff"]["added"]
    assert wired["put"] == []


def test_apply_puts_the_rendered_document(wired):
    result = es.sync()
    assert result["applied"] is True
    assert wired["put"][0]["hosts"]["sw1"]["ansible_host"] == "10.0.0.1"
    assert es.sync_after_commit() == {"ok": True, "changed": True}


def test_unreachable_executor_is_reported_not_raised(wired, monkeypatch):
    def down(agent=None):
        raise admin_client.ExecutorUnreachable("no route")

    monkeypatch.setattr(admin_client, "get_inventory", down)
    assert es.sync() == {"ok": False, "error": "no route", "applied": False}
    assert es.sync_after_commit() == {"ok": False, "error": "no route"}
    assert wired["put"] == []


def test_missing_admin_token_is_reported(wired, monkeypatch):
    def refuse(agent=None):
        raise admin_client.NotConfigured("no admin token")

    monkeypatch.setattr(admin_client, "get_inventory", refuse)
    assert es.sync()["ok"] is False


def test_sync_after_commit_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("database gone")

    monkeypatch.setattr(es, "_devices", boom)
    assert es.sync_after_commit() == {"ok": False, "error": "database gone"}


@pytest.mark.skipif(not hasattr(rbac, "PERM_INVENTORY_APPROVE"), reason="rbac constant not added yet")
def test_handler_is_gated_on_approve_and_forwards_dry_run(wired):
    from plugins._ea_inventory.api.inventory_sync import InventorySync

    assert InventorySync.required_permission() == rbac.PERM_INVENTORY_APPROVE
    handler = InventorySync(app=None, thread_lock=None)  # type: ignore[arg-type]
    out = asyncio.new_event_loop().run_until_complete(handler.process({"dry_run": True}, None))
    assert out["ok"] and out["applied"] is False and wired["put"] == []


def test_a_device_named_like_an_ansible_keyword_is_left_out():
    """The executor refuses `all`, `ungrouped` and `localhost` as host names; one such device must not fail the sync."""
    from executor import inventory

    for name in inventory.RESERVED | {"ALL"}:
        assert es.NAME.match(name) is None
    assert es.NAME.match("all-core-1")


def test_a_device_named_like_a_manual_host_never_overwrites_it(wired, monkeypatch):
    monkeypatch.setattr(es, "_devices", lambda: [dev("lab-sonic", ip="10.9.9.9"), dev("sw1")])
    result = es.sync()
    assert wired["put"][0]["hosts"]["lab-sonic"] == LAB
    assert "lab-sonic" not in wired["put"][0]["groups"]["inv_sonic"]["hosts"]
    assert any("lab-sonic" in w and "manually added" in w for w in result["diff"]["warnings"])


def test_a_device_tagged_legacy_ssh_is_reached_with_the_older_transport_and_no_other_is():
    """IOS 12.4 in the test lab of 2026-10-11 offered only SHA-1 key exchange, which the executor's default SSH
    library refuses. The tag is the administrator's decision for that one device."""
    old = {**dev("old-rtr", "ios", "10.0.0.3"), "tags": ["core", "legacy-ssh"]}
    new = {**dev("new-rtr", "ios", "10.0.0.4"), "tags": ["core"]}
    fw = {**dev("fw1", "fortios", "10.0.0.5"), "tags": ["legacy-ssh"]}    # read over HTTPS: the tag means nothing there
    hosts = es.render([old, new, fw, dev("sw1")], None, None)["hosts"]
    assert hosts["old-rtr"]["vars"] == {"eagent_managed": "inventory", "ansible_network_cli_ssh_type": "paramiko"}
    assert hosts["new-rtr"]["vars"] == hosts["fw1"]["vars"] == hosts["sw1"]["vars"] == {"eagent_managed": "inventory"}
    # Taking the tag away takes the transport away at the next sync.
    again = es.render([{**old, "tags": ["core"]}], {"hosts": hosts, "groups": {}}, None)
    assert again["hosts"]["old-rtr"]["vars"] == {"eagent_managed": "inventory"}


def test_a_device_named_like_a_group_is_not_sent_as_a_host(wired, monkeypatch):
    """`inv_sonic` is the group this module makes for every SONiC device, and `manual` is a group an administrator
    made: as host names they would make `--limit <name>` select the whole group while the card showed one device."""
    monkeypatch.setattr(es, "_devices", lambda: [dev("inv_sonic", "ios", "10.9.9.1"), dev("manual", "ios", "10.9.9.2"), dev("sw1")])
    result = es.sync()
    sent = wired["put"][0]
    assert "inv_sonic" not in sent["hosts"] and "manual" not in sent["hosts"] and "sw1" in sent["hosts"]
    assert not set(sent["hosts"]) & set(sent["groups"])
    for name in ("inv_sonic", "manual"):
        assert any(f"'{name}'" in w and "group" in w for w in result["diff"]["warnings"])
