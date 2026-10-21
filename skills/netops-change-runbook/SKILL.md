---
name: netops-change-runbook
description: "Plan a multi-step network change as one runbook (net_plan) with before and after snapshots, checks and rollback. Use for a change window, several devices, or a risky change that needs proof it worked."
triggers:
  - "maintenance window"
  - "plan the change"
  - "before and after"
  - "pre-check and post-check"
  - "runbook for the change"
  - "change plan"
---

# NetOps change runbook

You plan; the requesting engineer approves the whole runbook once, on one card, and is accountable. You cannot approve it. Nothing runs until they press Approve.

1. **Classify.** Standard: a low-risk catalogue entry done before. Normal: everything else, planned. Emergency: service is down now; the card and rollback still apply, only the planning is shorter. Say which, and why.
2. **Pre-checks before you plan** (each `net_read` is its own card; stop and wait each time):
   - `inventory_lookup` for platform, role, HA group and status of every target.
   - `solarwinds_lookup` `active_alerts` on each target. An open critical alert on a target: stop and tell the engineer.
   - HA pair: read the partner too. Pair not healthy: stop.
   - Backup age: Inventory Health lists devices with no recent backup. Old or missing: tell the engineer to run **Back up now** first.
3. **Build the plan** with `net_plan` `request`, at most eight steps, in this order:
   - a read that is the **before snapshot**: `bundle=switch_info` and `routing_info` (one platform per step), plus the commands below if the change touches that area;
   - the change steps, each a catalogue capability with its variables and its own verify and rollback;
   - the **after snapshot**: the same reads again, same commands, same order.
4. **Show** the engineer what the card carries: every step's exact commands, the checks, the rollback of each change, the riskiest tier. A high-risk plan needs the typed confirmation; never type it for them. Name the window and the blast radius in your own words (see netops-change-checklist).
5. **Stop.** On Approve call `net_plan` `run` with the `change_id`. On Reject or expiry, ask whether to plan again. Edit the plan only by requesting a new one.
6. **Read the result.** The run stops at the first failure or failed verify and puts one rollback card on screen for what was applied, undone in reverse order. Report it and run it only if the engineer approves that card.
7. **Compare snapshots.** List what is the same, what changed as intended, and what changed without a reason. Lost neighbours, fewer ports up, missing VLANs, fewer routes or a new log burst count as unintended until explained.

Extra snapshot commands, only when the change touches that area:

| Area | ios | nxos | eos | junos | sonic_community |
|---|---|---|---|---|---|
| Spanning tree | `show spanning-tree summary` | `show spanning-tree summary` | `show spanning-tree` | `show spanning-tree bridge` | n/a, no STP read |
| Trunks | `show interfaces trunk` | `show interface trunk` | `show interfaces trunk` | `show ethernet-switching interface` | `show vlan brief` |
| Logs | `show logging` | `show logging last 50` | `show logging last 30 minutes` | `show log messages` | `show logging` |

Good: after matches before except the intended difference, verify says `Verified`, no new alerts on the target. Bad: any unexplained difference, `Verify FAILED`, a neighbour or alert that was not there before.

Stop and hand back when: a pre-check fails, a change has no rollback, the devices do not match the plan at run time (it is marked superseded: plan again), or the engineer cannot be reached to approve.

Never run a change outside the catalogue, never put two platforms in one read step, never claim success without the after snapshot, never ask for credentials in chat.
