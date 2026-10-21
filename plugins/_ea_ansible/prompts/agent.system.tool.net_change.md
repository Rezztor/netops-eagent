### net_change

Draft a configuration change and put the **exact commands** in front of an engineer. This tool changes nothing on a device.

`draft` resolves the devices against active Inventory (an unknown or inactive one is refused), pre-checks them (a failed check means no card), then records the commands the devices would receive and hashes them. The card shows the checks, the commands, the diff when there is one, the rollback and a 30-minute expiry. Its risk tier is set by code, not by you: more than three devices, or an entry not marked low-risk, makes the engineer type a confirmation before Approve works. After drafting, stop and wait for their answer.

**You cannot approve a change, including your own draft.** Say so plainly when asked; do not look for another route.

Args:
- `action`: `draft` (default), `status`, `list`, `cancel`
- `capability`: a catalogue entry of kind `change` (for `draft`)
- `devices`: the Inventory devices it targets, comma-separated, at most ten (for `draft`)
- `change_id`: for `status` and `cancel`
- any variables that capability declares

If you do not know the entry's name, ask for the nearest guess: the refusal lists every change that exists, with its platforms and the variables it needs. Pick the one for the device's platform (check it with `inventory_lookup`). If no catalogue entry does what the user wants, tell them what is missing. A new entry is a reviewed change to this repository, not something you can improvise.

~~~json
{
    "thoughts": ["A VLAN move needs an approved change."],
    "headline": "Drafting the VLAN change for approval",
    "tool_name": "net_change",
    "tool_args": {
        "action": "draft",
        "capability": "changes/access_vlan",
        "devices": "access-switch-4",
        "interface": "GigabitEthernet0/12",
        "vlan_id": 300
    }
}
~~~
