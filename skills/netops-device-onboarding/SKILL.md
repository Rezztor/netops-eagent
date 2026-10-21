---
name: netops-device-onboarding
description: "Bring a new device into Inventory so the agent can read it: record, credential, executor sync, first read. Use when asked to add, onboard or register a switch, router or firewall."
triggers:
  - "onboard a device"
  - "add this switch to the inventory"
  - "new device"
  - "register the firewall"
  - "why can't you read this device"
---

# NetOps device onboarding

The agent can only read devices that are **active in Inventory** with a management address and a stored credential. You cannot add a device yourself and cannot approve anything; the engineer does each step in the UI or on a card. For a file or a list of many devices use netops-inventory-import.

1. **Check it is new.** `inventory_lookup` by name, address and serial. Found as planned, offline or decommissioned: say so and ask what they want, do not duplicate. Optionally `solarwinds_lookup` `node_search` to prefill vendor, model, version and address; quote it as SolarWinds' value.
2. **Collect the record.** Name (lower case, `a-z 0-9 . _ -`), device type, role, vendor, platform, model, serial, management IP, site, status. Ask for what is missing; never guess a serial, an address or a platform. Platform is one of: ios, iosxe, nxos, eos, junos, fortios, panos, sonic, sonic_community.
3. **Hand over the record.** One device from chat: no tool stages it, so tell the engineer to use Inventory, Devices, **Add device** (an engineer who can approve sees **Create and approve**). Several devices or a file: netops-inventory-import. Say that it is a request until approved on **Review**.
4. **Make it reachable.** After approval the status must be `active`. The engineer runs **Sync to executor** (Devices toolbar overflow; it shows a dry-run diff first). Check with `inventory_lookup` that it is active.
5. **Credential.** Request the first read. If the pre-check says the credential is missing, the secure form appears (or call `net_credential`). Wait for "Credential <name> stored." Never ask for a password in chat; if one is typed, tell them to use the form and that it should be rotated.
6. **First read** (one `net_read` card, then `run`): `bundle=switch_info`, or for firewalls only `reads/show` where the platform has reads. For ios, iosxe and sonic you may call `inventory_refresh_facts` with `dry_run` first.
7. **Compare** the answer with the record: hostname, model, serial, version. A difference is a conflict or a request for the engineer on Review, never an edit you make. Empty facts from community SONiC are normal.
8. **Report** a checklist: record, credential, sync, first read, facts, ports. Name what is missing (site, rack, lifecycle dates) and say that the engineer starts a config backup from the record's Backups tab (**Back up now**).

| Identity read | ios | nxos | eos | junos | sonic_community |
|---|---|---|---|---|---|
| Version and uptime | `show version` | `show version` | `show version` | `show version` | `show version` |
| Serial and parts | `show inventory` | `show inventory` | `show inventory` | `show chassis hardware` | `show platform summary` |

Good: the pre-check passes, the version output names the expected model and the hostname matches. Bad: `platform_mismatch` (the platform in Inventory is wrong: ask the engineer to correct it with an edit request), `credential_failed` (replace the credential through the form), `host_unreachable` or `timeout` (report; the path or address may be wrong, ask), a serial that differs from the record.

Stop and hand back when a pre-check fails twice with the same reason. Report the code and one line of cause, not raw Ansible output.

Never: ask for or repeat a credential, edit Inventory yourself, say a device is onboarded before the first read worked, read a device that is not active.
