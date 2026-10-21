"""Generate the executor's inventory from active inventory devices.

Postgres is the source of truth; the executor document is derived from it and
never the other way round. Hosts are managed only when they carry the var
`eagent_managed: inventory`; every other host and group is left untouched.

Credentials are referenced by name on the group. No secret passes through here.
`render` and `diff` are pure so they test without a database or an executor.
"""

from __future__ import annotations

import copy
import ipaddress
import re
from typing import Any

MANAGED_VAR = "eagent_managed"
MANAGED = "inventory"
PLUGIN_NAME = "_ea_inventory"
GROUP_PREFIX = "inv_"

#: Same pattern as executor/inventory.py NAME; a host the executor would refuse
#: must not take the whole document down with it.
NAME = re.compile(r"^(?!(?i:all|ungrouped|localhost)$)[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")  # and its RESERVED names

_IOS = {"ansible_network_os": "cisco.ios.ios", "ansible_connection": "ansible.netcommon.network_cli"}
PLATFORM_VARS: dict[str, dict[str, str]] = {
    "ios": _IOS,
    "iosxe": _IOS,
    "sonic": {
        "ansible_network_os": "dellemc.enterprise_sonic.sonic",
        "ansible_connection": "ansible.netcommon.network_cli",
    },
    # Community SONiC: plain SSH + show CLI. Dell Enterprise stays on `sonic`.
    "sonic_community": {
        "ansible_network_os": "sonic_community",
        "ansible_connection": "paramiko_ssh",
    },
    "nxos": {"ansible_network_os": "cisco.nxos.nxos", "ansible_connection": "ansible.netcommon.network_cli"},
    "eos": {"ansible_network_os": "arista.eos.eos", "ansible_connection": "ansible.netcommon.network_cli"},
    "junos": {"ansible_network_os": "junipernetworks.junos.junos", "ansible_connection": "ansible.netcommon.network_cli"},
    # Read by `uri` on the executor over HTTPS (ADR 0058), so Ansible opens no connection to the device.
    "fortios": {"ansible_network_os": "fortinet.fortios.fortios", "ansible_connection": "local"},
    "panos": {"ansible_network_os": "paloaltonetworks.panos.panos", "ansible_connection": "local"},
}
#: Platforms reached by an HTTPS API rather than SSH (Inventory shows them as such).
API_PLATFORMS = ("fortios", "panos")


#: A device with this tag is reached with the older SSH algorithms its software offers (SHA-1 key exchange,
#: ssh-rsa host keys, CBC ciphers): IOS 12.x and its like. The executor's default SSH library offers none of
#: them, and that is right for everything else, so this is asked for device by device and shows in the tags.
LEGACY_SSH_TAG = "legacy-ssh"
_NETWORK_CLI = "ansible.netcommon.network_cli"


def host_vars(device: dict) -> dict[str, str]:
    """The variables of one managed host: our mark, and the older SSH transport when the device is tagged for it."""
    found = {MANAGED_VAR: MANAGED}
    tagged = LEGACY_SSH_TAG in (device.get("tags") or [])
    if tagged and PLATFORM_VARS[device["platform"]].get("ansible_connection") == _NETWORK_CLI:
        found["ansible_network_cli_ssh_type"] = "paramiko"
    return found


def _is_group_name(name: str, groups: dict) -> bool:
    """Hosts and groups are one namespace for `--limit`: `inv_ios` as a host would select every IOS device."""
    return name.startswith(GROUP_PREFIX) or name in groups


def _is_managed(host: Any) -> bool:
    return isinstance(host, dict) and (host.get("vars") or {}).get(MANAGED_VAR) == MANAGED


def _valid_ip(value: Any) -> bool:
    try:
        ipaddress.ip_address(str(value or "").strip())
        return True
    except ValueError:
        return False


def _included(devices: list[dict]) -> list[dict]:
    return [
        d for d in devices
        if d.get("status") == "active" and d.get("platform") in PLATFORM_VARS and _valid_ip(d.get("mgmt_ip"))
    ]


def render(
    devices: list[dict], existing_inventory: dict | None, credential_by_platform: dict[str, str] | None
) -> dict:
    """The executor document: `existing_inventory` with managed content replaced."""
    doc = copy.deepcopy(existing_inventory or {})
    old_hosts = doc.get("hosts") or {}
    old_groups = doc.get("groups") or {}
    credential_by_platform = credential_by_platform or {}

    managed: dict[str, dict] = {}
    for device in _included(devices):
        name = str(device["name"])
        # Unusable names and names of a manual host are reported by `sync`; skipped here, so the manual entry survives.
        if NAME.match(name) and not _is_group_name(name, old_groups) and not (name in old_hosts and not _is_managed(old_hosts[name])):
            managed[name] = device

    # Hosts we own go; manual hosts stay. Dropped hosts leave every group.
    hosts = {n: h for n, h in old_hosts.items() if not _is_managed(h)}
    gone = {n for n in old_hosts if n not in hosts and n not in managed}
    for name, device in managed.items():
        hosts[name] = {
            "ansible_host": str(device["mgmt_ip"]).strip(),
            "vars": host_vars(device),
        }

    groups: dict[str, Any] = {}
    for name, group in old_groups.items():
        if name.startswith(GROUP_PREFIX):
            continue  # rebuilt below; its credential is carried over
        groups[name] = {**group, "hosts": [h for h in group.get("hosts") or [] if h not in gone]}

    for platform in sorted({d["platform"] for d in managed.values()}):
        name = GROUP_PREFIX + platform
        credential = credential_by_platform.get(platform) or (old_groups.get(name) or {}).get("credential") or ""
        groups[name] = {
            "hosts": sorted(n for n, d in managed.items() if d["platform"] == platform),
            "vars": dict(PLATFORM_VARS[platform]),
            "credential": credential,
        }

    return {"groups": groups, "hosts": hosts}


def _supported_platforms() -> set[str]:
    """Platforms the catalogue has a playbook for; ios entries cover iosxe."""
    try:
        from plugins._ea_ansible.helpers import catalogue

        found = {p for entry in catalogue.load().values() for p in entry.platforms}
    except Exception:  # noqa: BLE001 - a warning source must not break the sync
        return set()
    return found | ({"iosxe"} if "ios" in found else set())


def diff(old_doc: dict | None, new_doc: dict) -> dict[str, list[str]]:
    old_hosts, new_hosts = (old_doc or {}).get("hosts") or {}, new_doc.get("hosts") or {}
    old_groups, new_groups = (old_doc or {}).get("groups") or {}, new_doc.get("groups") or {}
    added = sorted(set(new_hosts) - set(old_hosts))
    removed = sorted(set(old_hosts) - set(new_hosts))
    changed = sorted(n for n in set(new_hosts) & set(old_hosts) if new_hosts[n] != old_hosts[n])
    changed += [f"group:{n}" for n in sorted(set(new_groups) & set(old_groups)) if new_groups[n] != old_groups[n]]

    supported = _supported_platforms()
    warnings = [
        f"No collection or catalogue entry covers platform '{name[len(GROUP_PREFIX):]}'; "
        "its devices are listed but jobs cannot run against them yet."
        for name, group in sorted(new_groups.items())
        if name.startswith(GROUP_PREFIX) and group.get("hosts") and name[len(GROUP_PREFIX):] not in supported
    ]
    return {"added": added, "removed": removed, "changed": changed, "warnings": warnings}


def _devices() -> list[dict]:
    from plugins._ea_inventory.helpers import store

    return store.list_devices(status="active")


def _credential_map(agent: Any) -> dict[str, str]:
    from helpers import plugins

    cfg = plugins.get_plugin_config(PLUGIN_NAME, agent=agent) or {}
    return {str(k): str(v) for k, v in (cfg.get("credential_by_platform") or {}).items()}


def sync(dry_run: bool = False, agent: Any = None) -> dict:
    """`{ok, diff, applied}`, or `{ok: False, error, applied: False}`; never raises on an executor fault."""
    from plugins._ea_ansible.helpers import admin_client

    try:
        devices = _devices()
        old = admin_client.get_inventory(agent).get("inventory") or {}
        new = render(devices, old, _credential_map(agent))
        result = diff(old, new)
        result["warnings"] += [
            f"Skipped '{d.get('name')}': not a usable executor host name."
            for d in _included(devices) if not NAME.match(str(d["name"]))
        ]
        result["warnings"] += [
            f"Skipped '{d['name']}': an executor group has that name (or it starts with '{GROUP_PREFIX}'); rename the device."
            for d in _included(devices)
            if NAME.match(str(d["name"])) and _is_group_name(str(d["name"]), old.get("groups") or {})
        ]
        result["warnings"] += [
            f"Skipped '{d['name']}': a manually added executor host has that name; rename one of them."
            for d in _included(devices)
            if NAME.match(str(d["name"])) and str(d["name"]) in old.get("hosts", {}) and not _is_managed(old["hosts"][str(d["name"])])
        ]
        if dry_run:
            return {"ok": True, "diff": result, "applied": False}
        admin_client.put_inventory(agent, new)
        return {"ok": True, "diff": result, "applied": True}
    except admin_client.ExecutorError as error:
        return {"ok": False, "error": str(error), "applied": False}


def sync_after_commit() -> dict:
    """Best effort after an approval. Never raises: the approval already succeeded."""
    try:
        result = sync()
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "error": str(error)}
    if not result["ok"]:
        return {"ok": False, "error": result["error"]}
    d = result["diff"]
    return {"ok": True, "changed": bool(d["added"] or d["removed"] or d["changed"])}
