"""NX-OS, EOS and Junos are covered everywhere IOS is (unverified: no device has run them)."""

from __future__ import annotations

import os
import re

from plugins._ea_ansible.helpers import bundles, catalogue
from plugins._ea_inventory.helpers import executor_sync
from plugins._ea_inventory.helpers.facts import collect

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
PLATFORMS = ("nxos", "eos", "junos")
READS = ("device_facts", "inventory_facts", "show")


def _text(*parts: str) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


def test_reads_backup_bundles_and_sync_cover_every_platform():
    entries = catalogue.load()
    for platform in PLATFORMS:
        for name in READS:
            entry = entries[f"reads/{name}"]
            assert platform in entry.platforms and entry.read_commands.get(platform), (name, platform)
            assert platform in _text("plugins", "_ea_ansible", "playbooks", "reads", f"{name}.yml")
        assert platform in _text("executor", "playbooks", "config_backup.yml")
        assert platform in collect.PLATFORMS
        assert all(platform in b["commands"] for b in bundles.load().values())  # also checks each against reads/show
        assert executor_sync.PLATFORM_VARS[platform]["ansible_connection"] == "ansible.netcommon.network_cli"


def test_the_image_pins_the_three_collections():
    dockerfile = _text("executor", "Dockerfile")
    for collection in ("cisco.nxos", "arista.eos", "junipernetworks.junos", "juniper.device"):
        assert re.search(rf"^\s+{re.escape(collection)}:==\d+\.\d+\.\d+ \\$", dockerfile, re.M), collection


def test_firewalls_are_read_by_name_over_their_api_and_feed_the_inventory():
    entries = catalogue.load()
    for name in ("device_facts", "inventory_facts", "interfaces", "routing_table", "firewall_policy_summary",
                 "vpn_tunnels", "system_resources", "session_summary"):
        entry = entries[f"reads/{name}"]
        assert {"fortios", "panos"} <= set(entry.platforms) and all(entry.read_commands[p] for p in ("fortios", "panos")), name
    assert not {"fortios", "panos"} & set(entries["reads/show"].platforms)  # named reads only: no free-form text for them
    assert {"fortios", "panos"} <= set(collect.PLATFORMS)
    for platform in ("fortios", "panos"):
        assert executor_sync.PLATFORM_VARS[platform]["ansible_connection"] == "local"  # read by uri on the executor
        assert platform in executor_sync.API_PLATFORMS
