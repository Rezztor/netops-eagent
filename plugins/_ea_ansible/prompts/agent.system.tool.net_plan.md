### net_plan

Run several steps as one runbook with **one approval**. Use it when a task is a read, then a change, then a read to confirm; a single read or change still goes through net_read or net_change. This tool changes nothing until an engineer approves.

`request` takes up to eight ordered `steps`. Each step is validated exactly as it would be alone (active Inventory devices only, the same patterns and pre-check): a read (`capability`, `bundle`, or free-form `commands`) or a change (`capability` and its variables), with its own `devices`. One card shows every step's commands, the checks and the rollback of each change; the plan's risk is its riskiest step, and the engineer may have to type a confirmation. A plan always asks, whatever the read-approval setting says. After requesting, stop and wait.

`run` (only after the engineer approved the card) executes the steps in order and **stops at the first failure or failed verify**. It then puts one rollback card on screen for the changes already applied, undone in reverse order. Do not run it unless the engineer approves that card too.

**You cannot approve a plan.** Say so plainly when asked.

Args:
- `action`: `request` (default) or `run`
- `steps`: for `request`, a list of objects, each with `devices` (comma-separated) and either `capability` plus its variables, `bundle`, or `capability: "reads/show"` with `commands`
- `change_id`: for `run`

~~~json
{
    "thoughts": ["Add the VLAN, and read it back, under one approval."],
    "headline": "Requesting a 3-step plan",
    "tool_name": "net_plan",
    "tool_args": {
        "action": "request",
        "steps": [
            {"devices": "lab-sonic", "capability": "reads/show", "commands": ["show vlan brief"]},
            {"devices": "lab-sonic", "capability": "changes/sonic_community_vlan_add", "vlan_id": 100},
            {"devices": "lab-sonic", "capability": "reads/show", "commands": ["show vlan brief"]}
        ]
    }
}
~~~
