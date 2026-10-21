---
name: netops-change-checklist
description: "Draft one network change for engineer approval: exact commands, rollback, blast radius, window, verify. Use when asked to change, configure, push or roll back anything on a switch, router, firewall or other network device."
triggers:
  - "change the config on"
  - "push this change"
  - "draft a change"
  - "roll back the change"
  - "add a vlan"
  - "set the description"
---

# NetOps change checklist

You draft; the requesting engineer approves on the card in the chat and is accountable for the execution. A draft is not a change: nothing has happened on a device until `net_apply` reports it.

1. **Look up** each target with `inventory_lookup`: platform, role, site, HA group, status. A device that is not active is refused; offer to stage it (netops-device-onboarding).
2. **Read first.** `net_read` the current state of every target (a bundle, or `reads/show`). Each read is approved on a card: stop, wait, then `action=run` with the `change_id`. Quote what you found. For a risky change take the snapshot in netops-change-runbook.
3. **Draft** with `net_change` using a catalogue capability. If none fits, say what is missing and stop. You may write the change up as a proposal (devices, commands, verify, rollback), labelled as a proposal. Never invent commands, devices or entry names; a wrong name returns the list of real ones.
4. **Show** the exact commands per device and the rollback. The card carries them, the diff and the pre-check. Say whether the commands came from check mode (computed from the device) or reviewed CLI (written in advance, not from the current state). No rollback entry: say so and stop. If the plan shows the device already matches, nothing is drafted: tell the engineer that state already exists.
5. **Blast radius:** devices, interfaces, VLANs, routes or policies touched; what traffic or users could drop; what depends on it; whether the target is one of an HA pair.
6. **Window:** when it should run and whether it can wait for a maintenance window. Never suggest running through a user-impacting hour.
7. **Approval:** hand the draft to the engineer. A high-risk change (more than three devices, or an entry not marked low-risk) needs them to type the confirmation shown; never type it for them. The card expires after 30 minutes: draft again. Do not approve, apply or retry around a refusal.
8. **Apply** with `net_apply` for the approved `change_id` only. A `superseded` result means the device moved after approval: draft again, do not call it a failure.
9. **Verify:** the apply reports `Verified` or `Verify FAILED` from the entry's own check; add a `net_read` of the changed state and report evidence. After a failed verify the "Roll back?" card is already on screen: tell the engineer and run the rollback only once they approve it.
10. **Close out:** say what changed, the evidence, and what was not checked.

Several steps in one go (read, change, read, or more than one device group): use `net_plan`, see netops-change-runbook.

Never ask for or echo credentials; a missing one is collected with the secure form (`net_credential`), not in chat. Never suggest SSH or `code_execution_tool` as a way around the tools.
