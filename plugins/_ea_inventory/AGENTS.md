# Device Inventory Plugin DOX

## Purpose

- Own the instance's network device inventory: the place tree, devices, observations and
  the proposals that change them.
- Keep every change reviewable: nothing alters a device except an approved
  proposal, and nothing is ever deleted.

## Ownership

- `helpers/normalise.py` owns what a field may hold (pure, no database).
- `helpers/places.py` owns the place tree (ADR 0054): site > location > building > floor > room > rack, one table
  `inventory_place`, the pure ID and code rules, create / update / delete, `merge` and `resolve`. The rules themselves live in
  `helpers/schema/0015_inventory_places.sql`. `api/inventory_places.py`, `inventory_place_save.py` and
  `inventory_place_merge.py` are the endpoints.
- `helpers/store.py` owns reads, observations and the uniqueness checks.
- `helpers/proposals.py` owns `propose` and `decide`, the only writers of
  `inventory_device`, and the audit events.
- `helpers/executor_sync.py` owns generating the Ansible executor's inventory
  from active devices; `api/inventory_sync.py` is its endpoint.
- `api/inventory_*.py` own the HTTP surface; permissions live in
  `helpers/api_policy.py`, not on the handlers (the sync handler declares its
  own, which agrees).
- `helpers/adapters/solarwinds.py` owns turning fetched nodes into proposals and
  `run_solarwinds_sync`; `helpers/reconcile.py` (pure) decides what would change.
  `api/inventory_sources.py` and `api/inventory_sync_solarwinds.py` are its endpoints.
- `helpers/precedence.py` (pure) owns which source may change which field; `helpers/conflicts.py`
  owns conflicts, pins and `decide_conflict`; `api/inventory_conflict*.py`, `inventory_pins.py`,
  `inventory_unpin.py`, `inventory_stale.py` and `inventory_propose_decommission_stale.py` are their endpoints.
- `helpers/facts/` owns reading device facts through the executor and normalising them per platform;
  its output reaches devices only through `precedence.plan` (proposals or conflicts).
- `helpers/reports.py` (pure) owns the computed block (access method, monitored, completeness, lifecycle state) and the
  lifecycle, data-quality and racks reports; `api/inventory_lifecycle.py`, `inventory_quality.py` and `inventory_racks.py`
  are its endpoints, and `inventory_list` / `inventory_device` attach `computed` to each device.
- `helpers/backups.py` owns config backups as the application sees them (ADR 0047): it asks the executor to take, list,
  show (redacted) and diff (redacted) a backup, and `reports.computed` adds the backup age. The text itself never reaches
  this plugin in the clear and there is no agent tool for it. `api/inventory_backup*.py` are its endpoints.
- `backups.drift` (pure) and `api/inventory_drift.py` (`inventory.view`) own the drift inbox: every active device whose
  newest backup differs from the one before (the executor writes a new backup only when the config changed), marked
  explained when an approved and applied device change, rollback or plan step names the device and was applied after
  the older backup's last check and before the newer one was taken, `backups.SLACK` (five minutes) either side for the
  two clocks. Read only, from the backup index and the change ledger; no job runs, and no commands leave it.
  "Explained" is that a change ran there then, not that it is the whole difference: the page opens the redacted diff.
- `api/inventory_summary.py` owns the one request behind the Inventory screen's tab badges and attention tiles
  (counts only, from `store`, `proposals`, `conflicts` and `reports`).
- `api/inventory_propose_many.py` owns bulk edit (one `update` proposal per device, one batch id);
  `api/inventory_template.py` owns the blank import template.
- `helpers/staging.py` owns turning an agent-mapped file into proposals; `_resolve_place` turns `place_id` or the six
  level names into a `place_id` and reports misses as `missing_places`.
- `tools/inventory_lookup.py` owns the agent's read-only view. Each device carries `reached_through`, the appliance network its management address is in (`network_list.label`, the list asked of the executor), when the appliance keeps such a list.
- `helpers/ports.py` owns the observed ports and parts: the snapshot store, the computed summary, the uplink mark and the VLAN
  lookup. `helpers/facts/ports_parse.py` (pure) turns device output into rows; `helpers/facts/collect_ports.py` runs the
  `reads/inventory_ports` playbook and stores the result (`collect.py` is untouched). `api/inventory_ports.py`,
  `inventory_refresh_ports.py`, `inventory_port_uplink.py` and `inventory_vlan.py` are the endpoints;
  `tools/inventory_ports.py` is the agent's read-only view of the stored rows.
- `helpers/schedule.py` owns the scheduled refresh (`refresh_interval_hours`, 0 = off): facts then ports for every active device as
  `system:schedule`, one run at a time, recorded in `inventory_source_run` as source `schedule`, audit `inventory.scheduled_refresh`
  (counts only). `extensions/python/job_loop/_60_scheduled_refresh.py` only calls `start_if_due()` (job-loop extension modules are
  re-executed each tick, so state lives in `schedule.py`); `api/inventory_schedule.py` declares its own `inventory.view` gate.
- `helpers/schema/0009_inventory.sql`, `0010_inventory_source_runs.sql`, `0011_inventory_conflicts.sql`, `0012_inventory_sonic_community.sql`, `0013_inventory_asset_record.sql`, `0014_inventory_ports.sql`, `0015_inventory_places.sql` and `0017_drop_old_place_text.sql` (core) own the tables.

## Local Contracts

- `inventory_read_file` and `inventory_stage` open files only through `helpers/readers.read_source`, rooted by default at `helpers.workdir.user_file_roots` (workdir, uploads, downloads, skills, projects; project private folders refused), never the whole `usr/users/<id>/` tree, which holds change records with their seals and chat transcripts.
- A redaction rule never looks past its keyword without a bound (`_GAP` in `helpers/redact.py`, the same 256 characters in `executor/redaction.py`): an unbounded `keyword ... key` scans to the end of the line from every keyword on it, so a long line of keywords cost the square of its length, on the event loop, for every chat message. `tests/test_redaction_cost.py` holds both redactors to a time in proportion to the text.
- **Every change is a proposal** (ADR 0040): `create`, `update` or
  `decommission`, recorded with `changes {field: {old, new}}` and the device
  `version` it was made against. Approval applies it in one transaction, bumps
  `version` and writes `field_sources[field]` for each changed field; a stale
  `base_version` marks the proposal `superseded` and applies nothing.
- **The requester may approve** (ADR 0039). Audit `inventory.approved` /
  `inventory.rejected` carry `requested_by` and `decided_by`.
- **The agent proposes at most; it never approves.** No file under `tools/` may
  call `decide` or `propose`; a test asserts it. `inventory_lookup` is read-only
  and never returns `custom`, `custodian` or `support_contact` (free text and people's names stay away from the model).
- **Instance-wide, no row-level security.** Access is RBAC (`inventory.view`,
  `.manage`, `.approve`, `.places`). Transactions use `db.NO_SCOPE`.
- **Unknown fields are refused, never ignored.** Secret-shaped `custom` keys are
  refused; credentials are named by reference only, never stored here.
- **Unset text is `""`**; serial and management IP are NULL in the database when
  unset so placeholders do not collide under the unique indexes. Management IP is
  unique among devices that are not `decommissioned`.
- **Executor sync is generated, one way.** Active devices produce `inv_<platform>`
  groups and `eagent_managed: inventory` hosts; manual hosts and groups are never
  touched. `inventory_decide` calls `sync_after_commit()` after the commit and
  reports its result as `sync`; a failure never undoes an approval.

- **SolarWinds feeds proposals, nothing else** (ADR 0042). Disabled by default; one batch of
  `solarwinds` proposals per run; it may set vendor, model, software_version, mgmt_ip and
  platform (and fill an empty serial) only where the field is empty or last set by SolarWinds;
  it never touches name, role, site, status, tags, ha_group, asset_tag or custom, never
  proposes removal, and has no agent tool. `inventory_device.last_seen_at` is updated by a
  run (observational, no version bump); that is the only device write outside `proposals.decide`.

- `api/inventory_propose.py` always records source `manual` (the other sources are set by the code that runs them) and keeps a caller's `evidence` under `claimed`/`claimed_by`, so Review never shows it as a verified quote, confidence or batch. `executor_sync.render` skips a device whose name a manually added executor host already has and `sync` reports the clash in its warnings. The same for a device named like an executor group (any name starting `inv_`, or a group an administrator made): hosts and groups are one namespace for `--limit`, and the executor refuses a document in which a name is both.
- **Automatic sources never overwrite human values** (ADR 0043). Every feed (SolarWinds, facts) asks
  `precedence.plan`; a differing human value becomes a conflict, never a change. Ranks: `facts` 2,
  `solarwinds` 1; any other source is human. Accepting a conflict goes through `proposals.propose` +
  `decide` under the conflict's source and the deciding engineer. Audit events for conflicts and pins
  carry ids and field names only. Stale devices are reported; decommissioning them is a proposal.
- **One owner per field** (ADR 0045). Declared fields (type, location, ownership, lifecycle dates, status) go through
  proposals; computed values (`reports.computed`) are worked out on read and never stored. `facts` may also set
  `part_number` and `base_mac`; every other asset-record field is human-only. Only `active` devices reach the executor,
  so `spare` and `faulty` devices are never automated against.
- **Bulk edit never touches a per-device unique field** (`name`, `serial`, `mgmt_ip`, `asset_tag`) and commits nothing:
  it stages one batch for Pending changes.
- **Ports and parts are an observed snapshot** (ADR 0045, 0049). A refresh replaces a device's `inventory_interface` and
  `inventory_component` rows (rows no longer reported are removed) with no proposal, no conflict and no device-field write;
  `observed_at` dates them. A person's click on Read from device is the approval, so refresh needs `inventory.manage`.
  An empty or unparseable read never replaces a good snapshot. `is_uplink` is the one person-written column, an audited
  annotation on an observed row that survives a refresh while the port exists. Only active devices are read; platforms are
  `sonic_community` (checked against the lab switch) and `ios` (checked against a Cisco 3725, IOS 12.4(15)T14, with an NM-16ESW; other IOS and IOS-XE images are unverified); `nxos`, `eos` and `junos` are written from the vendors' documented
  `| json` / `| display json` output and have not been run against a device (no PoE on any of them; junos has no speed, mode or VLANs); `fortios` and `panos` are read through their management API (ADR 0058; written from documented output, not run against a device): no PoE, no neighbours, and the zone goes in the description).
- **The agent sees stored rows, never the device, through `inventory_ports`.** Capped at 60 ports, device-written
  descriptions go through `redact.redact_text`, and it cannot refresh, mark an uplink or write anything.
- **Places are a master list** (ADR 0054). A device stores one `place_id`, the ID of its deepest known place; site,
  location, building, floor, room and rack are read from it. An ID is the parent's ID plus a code and never changes
  (column privilege, not discipline). `location` in code is only the second level; the general word is `place`.
  Creating a place is an audited direct write (`inventory.places` for site, location and building; `inventory.manage`
  for floor, room and rack); changing a device's `place_id` is a proposal. **An import and the agent never create a
  place:** `staging` resolves names with `places.resolve` and reports misses, `inventory_lookup` only reads, and a test
  asserts no tool calls `places.create`, `update` or `delete`. **The old text columns are gone** (0017, ADR 0057): what a
  device with no place still said is one line in `custom.old_place_text`. **Merging** (`places.merge`,
  `inventory.places`) creates the missing places under the target, stages one batch of `place_id` proposals for every
  device at or below the old place, redirects pending moves into it, and marks it inactive; it never writes a device.
  A rack unit needs a rack ID (table check and `proposals._check_place`).
- A new automatic source adds its rank and field list to `precedence.RANK` / `MAY_TOUCH` and `SOURCES`.

## Work Guidance

- Add a device field in four places together: the migration, `normalise.FIELDS`
  and `DEFAULTS`, `store.DEVICE_COLUMNS` (written out, not sliced from `FIELDS`), and the frontend types (`api/inventory.ts` and the groups in
  `InventoryDeviceFields.tsx`). The export, the template and the import follow `FIELDS` on their own.
- A new source (SolarWinds, extraction) adds observations and proposals with its
  own `source`; it must not write devices directly.

## Verification

- `pytest plugins/_ea_inventory -q` (needs PostgreSQL; see the root `conftest.py`). `test_ports_parse.py` needs none.
- A new platform for ports adds a parser branch in `ports_parse.py`, a fixture under `tests/fixtures/ports`, the platform to
  `collect_ports.PLATFORMS`, and its commands to `reads/inventory_ports` (playbook and catalogue entry together).
- `pytest tests/test_api_permission_coverage.py tests/test_netops_profiles.py tests/test_tool_permissions.py -q`

## Child DOX Index

No child DOX files.
