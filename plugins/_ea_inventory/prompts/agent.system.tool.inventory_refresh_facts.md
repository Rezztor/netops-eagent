### inventory_refresh_facts

Read live facts (vendor, model, serial, software version, HA role) from devices through the Ansible executor in check mode, then stage differences for an engineer. Nothing on a device changes, and values an engineer set by hand are never overwritten: a disagreement becomes a conflict for them to decide. You cannot approve.

Args: `devices` (optional name or list; omit for every eligible active device, at most 10 per call), `dry_run` (optional, stores nothing and shows what would happen).
- Covers ios, iosxe and sonic today; other platforms come back as skipped. A device must be in the executor inventory (an engineer syncs it from Inventory).
- The result is per device: `status`, `fields_seen`, `proposals`, `conflicts`, `unchanged`, `pinned`, `warnings`, `error`. A failed device does not stop the others. Empty facts from a community SONiC are normal; report them as such.
- Report requests and conflicts as pending decisions, never as changes made.

~~~json
{
    "thoughts": ["The engineer asked me to refresh sw-jkt-01."],
    "headline": "Refreshing device facts",
    "tool_name": "inventory_refresh_facts",
    "tool_args": {"devices": ["sw-jkt-01"], "dry_run": true}
}
~~~
