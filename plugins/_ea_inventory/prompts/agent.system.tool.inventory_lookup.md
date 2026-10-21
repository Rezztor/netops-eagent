### inventory_lookup

Look up devices in the instance's inventory. Read-only. Use it to find out what exists before reading or changing a device; it is the list the executor's inventory is generated from, so a device missing here is not reachable. You cannot add or change inventory: tell the user to request the change on the Inventory screen, where an engineer approves it.

What comes back: name, vendor, platform, model, serial, status, site, place ID, location, building, floor, room, rack, rack unit, management IP, software version, HA group, tags, device type, role, department, and the warranty, support and end-of-life dates. On an appliance that keeps a list of its networks, each device also has `reached_through`: the appliance's own network its management IP is in (for example `Interface 2, routed`), or empty when no network of the appliance holds that address. That is the answer to "which interface is this device reached through"; your own sandbox's interfaces say nothing about it. It never returns `custodian`, `support_contact` or `custom` (people's names and free text stay off this tool). Do not ask it for those, and do not invent them.

Args (all optional): `query` (free text over name, vendor, model, serial, IP, tags, part number, MAC, place, department, support contract), `name` (exact), `vendor`, `platform`, `site` (site name or code), `place` (a place ID at any level, such as `DIFC-GA` or `DIFC-GA-GV4-L1-R02`, or a place name such as `Gate Avenue`: every device at or below it), `status`, `device_type`, `role`, `department` (the last three are case-insensitive equality). A place is any level of site > location > building > floor > room > rack. `expiring_within_days` (a whole number) keeps devices whose warranty, support or end-of-life date is already past or falls within that many days; decommissioned devices are left out. `expiring_kind` (`warranty`, `support` or `eol`) narrows that to one date, and needs `expiring_within_days`. At most 25 devices come back with the total.

~~~json
{
    "thoughts": ["I need the Cisco switches at the Jakarta site."],
    "headline": "Looking up devices in the inventory",
    "tool_name": "inventory_lookup",
    "tool_args": {
        "vendor": "cisco",
        "site": "Jakarta"
    }
}
~~~
