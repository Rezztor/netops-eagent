# Device inventory

The **Inventory** screen is five tabs: **Devices**, **Review**, **Health**, **Places**, **Sources**. It lists every network device this instance knows about. The agent works from the same list. An old address still opens the right tab: pending or conflicts opens Review; lifecycle, quality or stale opens Health; sites or racks opens Places.

Nothing edits a device directly. You **request a change**, and an engineer **approves** it on Review. Devices are never deleted.

## Devices

The header counts devices that are not decommissioned, and the sites on the instance. Decommissioned devices stay hidden unless the status filter asks for them.

Four tiles say what needs attention. Each opens the tab that holds the detail:

- **Waiting for your review** is pending changes plus open conflicts.
- **Lifecycle alerts** counts devices whose worst warranty, support or end-of-life state is expired or ending soon. The window is 90 days unless the plugin config says otherwise. A device that is both expired and ending soon counts once, as expired.
- **Incomplete records** is how many live records are missing a required field, and the average completeness.
- **Not seen for 7 days** (the stale window, unless config says otherwise) lists at most three names under the count.

Search, Place, Type and Status narrow the table. Place takes part of an ID or a name (`GV4`, `Gate Avenue`, `R02`) and shows the devices at or below the place you pick; *No place yet* lists the rest. Filter adds role, department, vendor, platform, tag and stale. Column sets are Overview, Hardware, Location (Site, Location, Building, Floor, Room, Rack and Place ID) and Lifecycle. A row opens a short summary; **Open full record** is the full page, and **Edit** is the add / edit page. Select rows and the toolbar becomes the bulk bar: Edit fields, Change status, Add tag, Export selected, Clear.

A count beside **Inventory** in the sidebar is the same number as the Review tab: pending changes plus open conflicts. It is read when you open or leave the screen.

**Add device** opens the add page. **Export** downloads CSV or JSON. **Import** opens Sources.

## The record

The record page has the name, type, role and status, and one line for where it sits. **Read from device** reads facts, then ports, for that one device. **Edit** opens the form. The menu holds **Decommission**. Overview holds the field groups; Ports shows what was read from the device's interfaces and parts; Backups holds its saved configurations; Changes holds that device's requests.

A source label appears only where a value did not come from a person. A value labelled *computed* is worked out when the page opens and is not stored: how you reach the device, whether SolarWinds has seen it recently, how complete the record is, and whether warranty, support or end of life is fine, ending soon, expired, or has no date.

A device also has a type (router, switch, firewall, wireless controller, access point, load balancer, or other), a part number, a base MAC, where it sits (one place picked from the list, and for a rack the lowest U it occupies and how many U tall), who looks after it (department, custodian, support contact, support contract) and four dates (purchase, warranty, support, end of life). Status also has *spare* and *faulty*. Role stays free text; the form suggests core, distribution, access, edge, WAN and data centre.

A record is complete when name, type, role, vendor, model, serial, management IP, place and status are set. The place counts once it reaches the required level, room by default (`required_place_level` in the plugin config); a device known only to be "at DIFC" is listed as incomplete until someone finds its room. Type *other* counts as empty. Only *active* devices are synced to the executor. The record lists the other devices sharing its HA group.

Saving an edit sends only the changed fields to Review, old to new, and says so. Add creates a request to add the device. A user who can approve also sees **Create and approve** on the add page: it sends the request and approves it in one step, and the audit record names you as both requester and approver. The approver may correct a value before approving, and may leave a note. You can approve your own request; the screen then says you are accountable for it, and the audit record names you as both requester and approver. If the device changed after the request was made, approving fails and the request is marked superseded. Request it again.

## Review

Pending changes and Conflicts are one inbox. Nothing in the inventory changes until a request is approved.

Pending changes groups requests by where they came from: a file import, a bulk edit, or a manual edit. **Approve all** or **Reject all** applies to the selected batch, optionally only those with confidence 0.8 or higher. Each card shows the kind, the device, old to new, the quote and where it was found, and the confidence. You can edit a value before approving. Each request is applied separately; one that went stale is marked superseded.

Conflicts keep accept, keep and pin. **Accept observed** applies it as your own approved change (the audit record names you). **Keep current** ignores that observed value until it changes. **Pin current** ignores the field for that source until you unpin it. These need `inventory.approve`. A conflict whose device changed meanwhile, or already holds the value, is marked superseded.

## Health

Three read-only sections. Decommissioned devices are left out.

- **Lifecycle.** Warranty, support and end-of-life dates that have passed or end within the window (30, 90 or 180 days). One row is one device and one date. A device name opens the device.
- **Data quality.** Average completeness, which required fields are missing most often, and the incomplete devices. **Fix …** opens Devices filtered to that gap.
- **Not seen recently.** Active devices no source has seen for the stale window, including never seen ones. **Propose decommission** requests retirement of the ones you select. Nothing is retired until an engineer approves.

## Places

A **place** is any spot in the tree: **site > location > building > floor > room > rack**. *Location* is only the second level. DIFC is a site, Gate Avenue a location (`DIFC-GA`), Gate Village Building 4 a building (`DIFC-GA-GV4`), and `DIFC-GA-GV4-L1-R02-RK01` a rack. A place's ID is its parent's ID, a dash and its own code (1 to 8 letters or digits, no spaces); it is permanent. Names can be changed, codes cannot. Every level exists: a place is exactly one level below its parent.

The tree is on the left, with how many devices are in each place and below it, and an inactive mark. The tree hides inactive places (and what is under them) until **Show inactive** is ticked. The selected place is on the right: its path, its ID, **Add child**, rename, **Mark inactive**, **Merge into** (administrators) and delete. A room shows its racks drawn to scale, 42 U unless a device sits higher, and the devices in the room but in no rack. **No place yet** lists devices with no place.

- **Who may create what.** An administrator (`inventory.places`) adds sites, locations and buildings and may delete a place at any level. Engineers (`inventory.manage`) add floors, rooms and racks, here or straight from the Place picker on a device. Creating a place is an audited change made at once; putting a device in a place is a request, approved on Review like any other.
- **Picking a place.** On a device the picker shows one level at a time, and a search box finds a room by any part of its ID or name. A level with only one choice fills itself in and says "only one". Typed text that matches nothing offers to create it, with the parents fixed. A device may stop at any level; a rack unit is asked only for a rack.
- **The code is suggested as you type the name** (in the Places screen and in the picker's create form), shown as the finished ID, and you can edit it before saving. It is short, upper-case letters and digits, and free among the places beside it: `Gate Avenue` gives `GA`, `Level 1` `L1`, `Ground Floor` `GF`, a third room `R03`, a rack `RK01`. A code or name already used under that parent is shown at once (with **Use it** in the picker). Once saved the code never changes; the name can.
- **Small sites.** **Add site** has a *Small site* tick: it creates the site with one location and one building (`Main`, code `M`, both editable), so the picker fills them in.
- **Inactive** hides a place from the picker and the search. Devices keep it. A place nothing ever pointed at can be deleted; otherwise mark it inactive.
- **Devices with no place** keep what the old site, building, floor, room and rack text said as one line in their custom field `old_place_text` (shown on the record as *Old place text*, with the rack unit if there was one). Pick the place from the list and request the change as usual.
- **Merging two places.** If one place was entered twice (`Gate Avenue` and `GATE AVE`), select the one to retire and choose **Merge into**: pick another place of the same level and read the preview. It lists the places that would be created under the target (a place with the same code is reused), the devices that would move and any waiting move that is redirected. **Merge** creates the missing places, sends one batch of requests to Review moving every device at or below the old place, and marks the old place and what is under it inactive; devices change only when you approve the batch. A place whose name is already used under the target by another code stops the merge until one is renamed. Needs `inventory.places`.

## Sources

Every source stages changes for Review. None of them writes to the inventory directly, and a value a person typed is never overwritten.

### Files

**Upload a file** (needs `inventory.manage`) takes a CSV, Excel, PDF or Word file, opens a new chat with it attached and asks the NetOps Operator to import it. You can also attach a file in any chat yourself. The agent reads a **redacted** copy (passwords, keys and community strings are replaced before it sees them), maps the columns and stages the devices as pending requests. Nothing is added until you approve:

- Each request carries the **quote** and location it came from; open a request to check it. A serial or address that is not in the file is dropped, a missing name rejects the row.
- Existing devices are matched by serial, management IP or name, so a re-import proposes only what differs. Blank cells never clear a field.
- Requests from one import are one batch on Review.
- **Download template** is a header row only (`inventory-template.csv`). The place is `place_id` followed by `site, location, building, floor, room, rack`; fill `place_id`, or the names or codes (a level with one choice may be left empty). There is no custom column. Dates in a file must become `YYYY-MM-DD` before they are staged; an ambiguous date (for example `01/02/2026`) is asked, not guessed. A cell such as `R07 U22` is a rack and a rack unit, not one string.
- **An import never creates a place.** A row naming a place that does not exist is staged without one and a warning names the first missing level and its parent; the agent lists every such place once. Add them under Places, then re-import or place the devices from Review.

Recent import batches are listed on the card.

### Bulk edit

Select devices on Devices and choose **Edit fields**. Set the fields that should be the same (type, role, status, place, ownership, the lifecycle dates, tags) and send one request. It becomes one batch on Review. Name, serial, management IP and asset tag cannot be set this way: each device has its own.

### SolarWinds

Inventory can be fed from SolarWinds Orion, **off by default and read-only**. It reads nodes only (name, address, vendor, model, version, serial, status and last poll) through fixed queries; it never reads SNMP communities, credentials or configuration text, never writes to SolarWinds, and never deletes anything.

- **Enable it**: an administrator sets `solarwinds.enabled: true` in the Device Inventory plugin config (`max_nodes` 2000, `stale_days` 7, `min_platform_confidence` 0.6) **and** configures the SolarWinds plugin.
- **What a run does**: fetches every node first (any failed page fails the whole run and nothing is stored), matches nodes to devices by serial, then SolarWinds id, then management IP, and stages one **batch** of requests on Review. SolarWinds may set vendor, model, software version, address and platform (and fill an empty serial) on a field that is empty or last set by SolarWinds; it never changes name, role, place, status, tags, HA group, asset tag or custom fields, and a value an engineer or an import set always wins. New devices arrive as *planned*.
- **Needs review**: a node whose name equals a device that does not match by serial or address (a possible duplicate), or that matches several devices, is listed instead of staged. Names alone never match.
- **Stale**: a device SolarWinds has not reported for `stale_days` is flagged *stale*; it is never proposed for removal.
- **First run checklist**: the Orion column names are not yet verified against a live instance. (1) Check the run's `missing_columns`. (2) Run **Preview (dry run)** and read the counts. (3) Work through *needs review*. (4) Only then **Run and stage**, and review the batch before approving it.

### Device reads

**Read from device** (or **Refresh all active devices**, or asking the NetOps Operator) reads facts through the Ansible executor in check mode, so nothing on the device changes. The device must already be in the executor inventory (**Sync to executor**, in the Devices toolbar overflow). Up to 10 devices per call; one failing does not stop the others. Each result lists the fields seen, requests made, conflicts, warnings and any error. A hostname that differs from the device name is a warning, never a change. The values become requests (source *facts*, one batch per refresh) or conflicts, never direct edits.

Collection runs for **ios, iosxe and sonic** only (the executor image has those collections). The normalisers read all eight platforms (ios, iosxe, nxos, eos, junos, fortios, panos, sonic), but those for nxos, eos, junos, fortios and panos are tested only against fixtures built from the vendors' documented output and are **unverified until run on a live device**. The only lab device is community SONiC, which returns empty facts. Per-vendor next step: add the vendor's Ansible collection to the executor image (nxos `cisco.nxos`, eos `arista.eos`, junos `junipernetworks.junos`, panos `paloaltonetworks.panos`, fortios `fortinet.fortios`), add a collect step to `reads/inventory_facts`, then check the normaliser against a real device's output.

### Older devices: the tag `legacy-ssh`

Some older software offers only SSH algorithms that are no longer considered safe: Cisco IOS 12.x, for example,
offers SHA-1 key exchange, `ssh-rsa` host keys and CBC ciphers and nothing newer. The product does not offer
those by default, so a read from such a device ends in a second with "The device and the executor share no SSH
algorithm".

To reach such a device, give it the tag `legacy-ssh` on its record. The next sync tells the executor to use the
older algorithms for that device only. Every other device is unaffected. Take the tag away when the device's
software has been upgraded.

### Ports, parts and backups

What a device reports about itself (interfaces, VLANs, neighbours, PoE, optics, modules, power supplies, licences) is **observed**: it is saved without approval, time-stamped, and the next read replaces the previous one. It never changes a declared field. On the **Ports** tab, an engineer with `inventory.manage` can mark a port as an uplink; a port whose neighbour is another inventory device is shown as an uplink too. Reading ports is supported for ios, nxos, eos, junos and community SONiC. The community SONiC reader was checked against the lab switch and the ios reader against one Cisco 3725; the nxos, eos and junos readers are written from documented output (the vendors' `| json` / `| display json` replies) and **have not been run against a device**. None of the three reads PoE, and junos gives no speed, mode or VLANs.

**Backups** keeps device configurations on the Ansible executor, encrypted there. The application shows dates, sizes and hashes, and a **redacted** view or diff (secrets are replaced before the text leaves the executor). **Back up now** needs `inventory.manage`; a configuration identical to the newest one is reported as unchanged and stores nothing new. The agent has no tool for backup text. Health lists active devices with no backup or one older than the stale window (7 days by default). Exporting the raw text is done on the executor host only.

Facts outrank SolarWinds; both are *automatic*. An automatic source may change a field only if it is empty or was last set by the same or a lower-ranked automatic source. A differing value a person (or an import) set is never overwritten: it becomes a conflict. Facts may touch vendor, model, serial, software version, HA group, part number, base MAC and the HA role and peer; SolarWinds vendor, model, software version, address, platform and an empty serial. Name, role, place, status, tags, asset tag and the other declared fields (ownership, dates, type) are never automatic.

## Cloud

When the Azure plugin is enabled (or has stored data), the Inventory screen has an **On-prem | Cloud** switch. On-prem
is the device inventory above, unchanged. Cloud shows Azure as a tree (subscription, resource group, category,
resource) with a count at every level, a **History** tab of what each pull added, changed and removed, and a
**Sources** card with the last pull, the next pull, the credential's expiry and **Pull now** (`inventory.manage`).
Cloud inventory is read only and **observed**: it has no Add, Edit or Review, and every view says "as of" the date of
the last pull. Setup, settings and what the agent can see: [Azure cloud inventory](azure.md).

## Rules

- Names are lower-case (`a-z 0-9 . _ -`) and unique. A serial of `N/A`, `unknown`, `-` or all one character is treated as unset. Serials are unique. A management IP is unique among devices that are not decommissioned.
- Custom fields cannot hold secrets (names containing password, secret, token, community or key are refused). Credentials live on the Ansible executor.

## Executor

Approving a change syncs **active** devices with an address to the Ansible executor's inventory. Hosts and groups entered by hand on the executor are left alone. If the executor is unreachable the approval still stands and the screen shows the sync error. **Sync to executor** shows a dry-run diff first.

## Permissions

`inventory.view` reads, `inventory.manage` requests changes and adds floors, rooms and racks, `inventory.places` (administrators) adds sites, locations and buildings, deletes places and merges them, `inventory.approve` decides requests, conflicts and syncs. Administrators hold all four; power users (network engineers) hold the other three; standard users none.

The agent can look devices up with `inventory_lookup` (read-only), including by place (any level, by ID or name: every device at or below it), type, role, department and by a warranty, support or end-of-life date running out. It is not shown custodian, support contact or custom fields. It can stage an import with `inventory_stage`. It cannot approve changes, and neither it nor an import ever creates a place.
