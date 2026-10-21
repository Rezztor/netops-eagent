# Network Automation Plugin DOX

## Purpose

- Own the agent's access to network devices and firewalls through Ansible.
- Keep every device credential, inventory file and vault out of the application
  container and off the per-user runner.
- Make change execution conditional on an engineer having read and approved the
  exact commands, and keep that gate impossible to separate from the tools it
  gates.

## Ownership

- `helpers/job_schema.py` owns the executor's job contract -- the fields a job may
  carry, and the far longer list it may not.
- `helpers/conditions.py` owns the ordered conditions execution must satisfy and
  each condition's refusal text.
- `helpers/executor_client.py` owns the only outbound reach to the executor. No
  other module in this tree may import an HTTP client.
- `helpers/catalogue.py` owns the catalogue, and the typed schema every variable
  is validated against.
- `helpers/render.py` owns turning an entry into the commands an engineer
  approves, and recording which of the two sources produced them.
- `helpers/change_model.py` owns the record and the plan hash;
  `helpers/change_store.py` owns where records live and the approval register (`change_approval`, migration 0023).
- `extensions/python/tool_execute_before/_95_change_gate.py` owns refusing.
- `extensions/python/tool_execute_before/_96_local_model_gate.py` owns the `require_local_model` switch (ADR 0052):
  when it is on, a tool that returns device output or inventory data is refused unless every model the result could
  reach is local. It fails closed.
- `tools/net_credential.py` owns asking for a device credential: it shows the
  `secret_form` card (`plugins/_ea_interaction`) and never receives or reads back
  a secret. `net_read` shows the same card when its pre-check finds the credential
  missing. The agent never asks for a value in chat.
- `bundles.yaml` and `helpers/bundles.py` own the named show-command bundles and the one builder of a read's
  command lines (a request and a card edit both use it).
- `api/read_edit.py` owns editing a free-form read's commands on its card.
- `tools/net_change.py` also owns `change_card`, the one builder of a change's or rollback's decision card.
- `helpers/approvers.py` owns who may sign a waiting change and telling them (ADR 0089); `api/approvals_waiting.py`
  owns the Approvals page's list.
- `tools/net_read.py`, `net_change.py`, `net_apply.py` own the three tool
  surfaces. `api/change_approve.py` owns approval, which is not a tool.
  `net_read` has two actions: `request` and `run` (below).
- `plugin.yaml` and `default_config.yaml` own the manifest and settings defaults.
- `playbooks/reads/inventory_facts.yml` owns the `EAGENT_FACTS <host> <json>`
  line the inventory facts refresh parses; changing that line changes
  `plugins/_ea_inventory/helpers/facts/collect.py`'s parser with it.
- `playbooks/reads/inventory_ports.yml` owns the `EAGENT_PORTS <host> <json>` line the inventory's port and parts
  refresh parses (`plugins/_ea_inventory/helpers/facts/ports_parse.py`); change the two together.
- `playbooks/reads/fw/read.yml` and `filter_plugins/eagent_firewall.py` own every FortiGate and Palo Alto read (ADR 0058):
  `uri`, GET only, from the executor, TLS verified, token in a header. Inventory sync records `ansible_connection: local` for
  them (`executor_sync.PLATFORM_VARS`; the executor renders `local` too). `inventory_ports` asks for the `ports` shape and
  `plugins/_ea_inventory/helpers/facts/ports_parse.py` maps it; change the shape and the parser together. These two have
  named reads only (no `reads/show`) and no change entries.
- `helpers/network_list.py` owns the appliance's network list (milestone N4): the one reader of its shape, the lookup of an
  address in it, the reason class of a failed connect, the five plain sentences and the "VLAN 200, routed" label. Pure
  standard library, because the executor image copies `helpers/` and imports it too. `executor/networks.py` reads the file,
  `api/network_list.py` serves the Networks view (`network.view`), `executor_client.networks` is the only way to ask for it.
- `tests/test_net_contract.py` owns E1-E6, the architecture contract.

## Local Contracts

- **The application never connects to a device.** The only outbound connection
  this plugin makes is to the Ansible executor. No `paramiko`, `asyncssh`,
  `netmiko`, `scrapli` or shell-out to `ssh` anywhere in this tree, tests
  excepted -- E2 greps for them.
- **Ansible reaches devices by whatever transport suits the platform** --
  `network_cli`, `httpapi`, `netconf`, REST. That happens inside the executor and
  is not what the rule above forbids.
- **The executor rejects playbook content**, including YAML this application
  generated itself. A job is a name, a commit, validated variables, a limit and a
  check-mode flag. `validate_job_fields` enforces it on both sides.
- **A job against devices names them.** The executor refuses an empty `limit` for every entry except the one
  whose platform is `any`, and refuses `all`, `ungrouped` and `localhost` as host or group names, so no selector
  can come to mean the whole inventory. Every comma-separated part of `limit` must be a host name the executor's
  inventory holds (not a group, not a pattern: `* ! & [ ] :` are refused), so `max_devices_per_job` counts devices.
- **Execution satisfies every condition in `CONDITIONS`, or it refuses.** The
  gate walks the list in order and reports the first failure by name.
- **The gate fails closed.** A check that raises is a refusal. This inverts
  `plugins/_infection_check`, which swallows its own faults so a checker outage
  never blocks work; here a broken check must never become an open gate. Say so
  in a comment wherever it appears, because the surrounding code says the
  opposite.
- **The gate and the tools ship in one plugin, deliberately.** Disabling the
  plugin removes `net_apply` and its gate together; there is no configuration in
  which the tool exists and the gate does not.
- **The tokens are references, never values.** `default_config.yaml` names the
  `usr/.env` keys; no handler returns either token. The deployment injects
  both into the application (ADR 0036); the administration token is pasted in Settings only where the deployment
  keeps it back (`REZZTOR_ANSIBLE_APP_ADMIN_TOKEN=`).
- `api/ansible_config.py` emits `ansible.config.save` / `ansible.config.clear_token` with
  setting names and whether a token field was submitted; never token values.
  `tests/test_missing_audit_events.py` covers this.
- **The requesting engineer is the accountable approver** (ADR 0039). The power
  role holds `network.approve`; the user role does not. The audit carries
  `requested_by` and `approved_by`. The agent, sub-agents and schedules still
  cannot approve.
- **Every read is requested, then approved, then run.** `net_read` `request`
  resolves targets against *active* Inventory only, runs `helpers/preflight.py`,
  and on a clean pre-check saves a `kind="read"` change record (15-minute
  `expires_at`) and shows a decision card; a failed pre-check returns a plain
  reason and no card. `run` executes only a record that is `approved`, unexpired,
  unchanged since approval (`conditions.first_failure`) and requested by the
  current user, in check mode at the pinned commit, and records the job id and a
  redacted excerpt. A failed job is reported as a `failures.classify` code, never
  as the raw Ansible output alone. The agent stops after the card until the
  engineer answers.
- **A change gets the same card as a read** (ADR 0050). `net_change draft` resolves targets against active
  Inventory with `net_read`'s resolver, runs `helpers/preflight.py` (a failed check means no card), fills
  `<variable>` placeholders in reviewed CLI (`render.fill`), and saves a `kind="change"` record with a 30-minute
  `expires_at`. The tier is code: an entry may declare `risk: low`; anything else, or more than three devices,
  is `change-high` and carries `confirm` (the device name, or `apply to N devices`). `change_approve` refuses an
  approval whose `confirmation` does not match, and requires `expected_hash` for every record that carries a tier.
- **A change can be proven and undone from its card.** An entry may declare `verify: {commands, expect, absent}`;
  `commands` are checked against the `reads/show` pattern at catalogue load. After a successful apply
  `net_apply` runs them per device through `reads/show` and records `verified` (`null` = no verify) with a
  redacted excerpt; a failed verify is status `verify_failed`. After any apply it saves a `kind="rollback"`
  record (the commands the change's approver already read) and shows a "Roll back?" card. That record is
  approved and applied through the same gate as a change (hash, expiry, requester) and, once its own verify
  passes, marks the original `rolled_back` (audited `network.change.rolled_back`). `net_apply rollback` only
  draws that card and is not gated, because it reaches no device.
- **A runbook plan is approved once** (ADR 0053). `net_plan` `request` takes up to eight ordered steps, each a read or a
  change, and validates every one as it would be alone (active Inventory, same patterns, pre-check, placeholders). It saves
  one `kind="plan"` record whose `steps` are part of the hash, with the highest tier of its steps (typed confirmation when
  any is `change-high`); it never takes the `read_approval` setting. `run` is gated like `net_apply` (`_95_change_gate`),
  re-renders every change first (drift supersedes the plan), executes in order, stops at the first failure or failed verify
  and saves the undo of the applied changes, in reverse order, as a second plan record (`rollback_of`) with its own card.
  `helpers/plans.py` owns the tier, the listing and the card; `tools/net_plan.py` owns the I/O.
- **Free-form reads are `show` plus plain words, nothing else** (ADR 0046). `reads/show` takes up to five
  commands as `command_1` .. `command_5`; the pattern on those variables in `catalogue.yaml` is the whole
  safety argument and is enforced by the application, by the executor (same catalogue) and by an `assert`
  in `playbooks/reads/show.yml`. Loosening it is a security change: extend the accept/refuse table in
  `tests/test_net_catalogue.py` first. A `<variable>` line in an entry's `read_commands` is shown on the
  card as that variable's value.
- **A card can be edited, a fixed read can be covered by a setting** (ADR 0048). A `reads/show` card carries
  `editable`; `read_edit` (requester only, `awaiting_approval` only, same pattern as a request) rewrites the
  same record, gives it a new hash and a fresh expiry and returns the new card, so approving binds to the
  edited text. `bundles.yaml` names up to five `show` commands per platform (checked against the `reads/show`
  pattern at load; one platform per request). The `read_approval` setting (`every_time` default,
  `once_per_chat`, `log_only`; saved only with `plugins.manage`) lets a fixed read or bundle skip the card for
  a requester who holds `network.approve`: the record is saved approved with `approval_basis` set, the
  requester as approver, and `network.read.auto_approved` is audited. **Free-form commands always ask.**
  A bundle is refused with any line of the caller's own beside it, `commands` or `command_1`..`command_5`.
  The standing decision lives in `conditions.standing_basis` and `change_store.approve_standing`, never in a tool.
- **The appliance's networks are read, never written** (N4). The appliance console writes
  `/etc/rezztor/networks/networks.json`; the Compose file mounts that directory read-only into the executor, which returns the
  list from `GET /v1/networks` and, in each pre-check row, the reason class of its own TCP connect (`ok`, `no_route_known`,
  `no_answer_on_link`, `no_answer_via_gateway`, `refused`, `network_down`; no ICMP, no raw socket) with the network's name. A
  missing or broken file is "nothing known": the reason is empty and the pre-check says what it said before. The "reachable"
  row then carries the plain sentence with the real addresses, the gateway and the appliance's own address in that network;
  the label beside a device's address is worked out from the list and **no device record stores a network**. Changing a network
  is the appliance console's menu Network; nothing in this plugin does it. Changing the list's shape is a contract with the
  appliance tool: say so before you do.
- **The model never sees a stored device secret.** What it can see, what it cannot, and
  the accepted risks are in ADR 0044 and `docs/guides/security-credentials.md`. Device
  output reaches it after the executor scrub and the `tool_execute_after` redaction hook;
  approved commands keep operational values by design; credentials are obtained only
  through `net_credential`.
- **A record's approval is signed by the server.** `change_store.save` seals the status, approver, approved hash,
  expiry, CR/SR reference and documents with the change id and the owning user, under a key derived from
  `FLASK_SECRET_KEY` (a per-process key when it is unset, so an approval then does not outlive a restart). Every read
  goes through `change_store._read`: a record that says `approved` or `applying` without a valid seal is put back to
  `awaiting_approval`, audited as `network.change.tampered`, and never applied. The Azure records live in the same
  store and are covered by it. Write a record only through `change_store.save`; never read the file directly.
- **The approval is a row in PostgreSQL, used exactly once** (ADR 0077, table `change_approval`). The file keeps the plan;
  the row holds the status, approver, approved hash, CR/SR number, times and the step taken, and `change_store._read` lays
  it over the file, so every gate, tool and endpoint decides from the row. A record with no row is not approved. Only
  `change_store.approve` (the approve endpoints, and the standing read setting) writes `approved`; only `change_store.take`
  uses it, in one `UPDATE` that names the status, the hash of what is about to run and the expiry, so of two applies one
  proceeds and a file put back after its apply is refused; `give_back` undoes a take the executor turned away before
  anything ran. `save` writes every other status (with the kind and the expiry, never an approval column) to the row and
  raises `NotApproved` for `approved` or `applying` the row does not hold; it never moves a row that has left draft,
  rendered or awaiting approval back into one of them, so a record loaded before an approval and saved after it cannot
  undo it (the save is dropped). `approve` keeps the expiry the row was drafted with. A file whose seal does not verify
  keeps no `documents` bookkeeping, at any status. A tool never sets `record.status` to either: it calls `take`. A background path pushes the owner's scope
  first; with none it sees no row and refuses. Tests that need a state no code path writes use `tests/register.force`.
- **A waiting change is announced to the people who may sign it** (ADR 0089). `change_store._store` calls
  `approvers.notify` on the transition into `awaiting_approval`, and a failure there never fails the save. Announced: a
  change, a plan with a change step, an Azure change or action; never a read or a rollback card. Told: whoever the
  deciding endpoint would let decide (`approvers.may_decide`: any enabled `network.approve` holder for a device record,
  the requester alone for an Azure one), as a bell item linking to `/approvals?change=<id>` and, when SMTP is
  configured, a mail through `mailer.send_to_user` to each of them but the requester, on a background thread, with no
  commands in it, at most one per approver in `approvers.MAIL_EVERY` (ten minutes), later changes batched into the
  next one if they still wait. `approvals_waiting` (`network.approve`) lists the same set for the caller, unexpired, oldest first.
  Neither decides anything: the approve endpoints keep every check.
- **Approval is not a tool.** No file under `tools/` may set an approval or name
  `PERM_NETWORK_APPROVE`; a test asserts it. The agent drafts, a person approves. The one exception is a
  standing read setting an administrator chose, applied by `change_store.approve_standing`.
- **There is no offline renderer.** Verified 2026-09-09 against the built image:
  `cisco.ios` resource modules refuse `connection: local` even for
  `state: rendered`, and `dellemc.enterprise_sonic` has no `rendered` state. So
  an entry either supports check mode -- and its commands come from the device --
  or it carries `reviewed_cli` a person wrote. The catalogue enforces exactly one
  of the two.

## Work Guidance

- Adding a capability means adding a reviewed playbook and a catalogue entry with
  a typed, constrained schema for every variable -- never a Python function that
  builds playbook YAML.
- Adding a condition means appending to `CONDITIONS` with its own refusal text.
  The deferred ServiceDesk change-record check is one entry, which is why the
  gate is a list.
- Keep refusal text actionable: it is read by the agent, and through it by a
  person who needs to know what to do next.

## Verification

- `pytest plugins/_ea_ansible -q`
- The executor image: `docker build -f executor/Dockerfile . ` from the
  repository root, with `--build-arg CATALOGUE_COMMIT="$(git rev-parse HEAD)"`.
  The build resolves a module and reads its collection list back rather than
  trusting an exit status.
- Live, once a device is available: a read per platform through the real
  executor. The SONiC leg ran 2026-09-19 against the community SONiC VS lab:
  the transport pipeline verified end to end, and the read entries' bugs it
  surfaced are fixed and recorded in
  [docs/plans/2026-09-19-network-catalogue-live-verification.md](../../docs/plans/2026-09-19-network-catalogue-live-verification.md).
  Cisco IOS ran 2026-10-02 against a Cisco 3725 (IOS 12.4(15)T14, NM-16ESW) -- reads, show,
  ports, backup and `changes/ios_interface_description` applied, verified and rolled back with
  the config hash identical afterwards; its bugs are fixed. `ios_vlans` fails on that hardware.
  nxos, eos, junos and Enterprise SONiC remain unverified against hardware, and so does
  whether their fixed entries return the right thing rather than merely resolving -- say so
  rather than implying otherwise.

## Child DOX Index

No child DOX files.
