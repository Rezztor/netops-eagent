### net_apply

Run a change an engineer has already approved.

It takes a `change_id` and nothing else. Everything about what will run — the playbook, the devices, the variables — comes from the approved record, so there is no argument here through which you can alter what happens.

Before it runs, the plan is re-checked against the devices. If they have changed since approval, the change is marked superseded and refused: the commands that would run are no longer the commands anyone read. Draft it again.

Args:
- `action`: `apply` (default), `verify`, `rollback` (offer the undo card) or `close`
- `change_id` (required)

After a successful apply the entry's own `verify` (if it has one) runs through a read and the reply says
`Verified`, `Verify FAILED` or that nothing was checked. A card "Roll back?" then appears for any applied,
failed or unverified change, with the commands that undo it. **Do not run it unless the engineer approves it
on the card**; then call `net_apply` with the *rollback's* `change_id` (the reply names it) like any other
approved change. The rollback is verified too, and the original change is marked rolled back only when it
passes. If the change names no rollback, say so and stop: there is no reviewed way back.

`action=rollback` with the original `change_id` only puts that card on screen again (use it if the card
expired); it changes nothing on a device. `action=verify` re-checks, in check mode, whether the module still
has work to do. Use it after an apply that failed as well as one that succeeded.

If it is refused, read the reason and explain it. Do not retry, and do not look for another way to make the change.

~~~json
{
    "thoughts": ["The engineer approved change 7f3a. Applying it."],
    "headline": "Applying the approved VLAN change",
    "tool_name": "net_apply",
    "tool_args": {"action": "apply", "change_id": "7f3a2b19c4d5"}
}
~~~
