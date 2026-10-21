# Device Inventory

The instance's list of network devices — name, vendor, platform, model, serial,
place, management IP, status, software version, HA group, tags — kept in
PostgreSQL, with where each field came from.

A device's **place** is picked from a master list, never typed: site > location > building > floor > room > rack
(`DIFC-GA-GV4-L1-R02`). Administrators add sites, locations and buildings, engineers floors, rooms and racks; an
import and the agent never create one ([ADR 0054](../../docs/internal/decisions/0054-places-are-picked-from-a-master-list.md)).

Changes are **proposals**: an engineer requests, an engineer approves (the same
person may do both and is then accountable), and only the approval alters the
device. Nothing is deleted; a retired device becomes `decommissioned`. Active
devices are synced to the Ansible executor's inventory.

What a device reports about itself (its ports, VLANs, neighbours, optics, PoE, modules and licences) is **observed**:
Read from device stores it with a date and replaces the previous snapshot, with no approval ([ADR 0049](../../docs/internal/decisions/0049-observed-ports-are-a-snapshot.md)).
You can mark a port as an uplink; a port whose neighbour is another inventory device counts as one too.

The agent can look devices up, by place at any level (`inventory_lookup`) and read the stored ports and parts, or which ports carry a VLAN (`inventory_ports`); both are read-only. It is not shown
custodian, support contact or custom fields. It cannot change the inventory.
**Download template** is a header row for an import.

User guide: [docs/guides/inventory.md](../../docs/guides/inventory.md). Decisions:
[ADR 0040](../../docs/internal/decisions/0040-inventory-changes-are-proposals.md),
[ADR 0045](../../docs/internal/decisions/0045-one-owner-per-inventory-field.md),
[ADR 0049](../../docs/internal/decisions/0049-observed-ports-are-a-snapshot.md),
[ADR 0054](../../docs/internal/decisions/0054-places-are-picked-from-a-master-list.md).
Contract for developers: [AGENTS.md](AGENTS.md).

Settings: `credential_by_platform` in `default_config.yaml` maps a platform to the
name of an executor credential for the generated groups.
