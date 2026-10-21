---
name: netops-config-drift
description: "Review configuration backups and drift: which devices lack a recent backup, what changed since the last one, whether the change was an approved one. Use for \"what changed\", unsaved config, backup gaps, audits."
triggers:
  - "config drift"
  - "what changed on"
  - "unsaved config"
  - "backup status"
  - "config differs"
  - "who changed the config"
---

# NetOps config backup and drift review

Read-only. The agent has no tool that returns saved backup text. Backups live on the executor and an engineer views a redacted copy or diff on the device record (Backups tab) and starts one with **Back up now**. You find gaps, compare, and explain; you never copy or repeat a secret.

1. **Scope:** one device, a site, or all. For gaps, send the engineer to Inventory, Health: it lists active devices with no backup or one older than the stale window (7 days by default). Report the names you are given; do not guess them. For "what changed across the estate", send them to the **Drift** page: every device whose last two backups differ, marked as matching an approved change or unexplained, with the redacted diff.
2. **What was approved.** `net_change` `action=list` (NetOps Operator only; otherwise ask the engineer) shows the changes drafted and applied through this platform, with their status and who approved. Anything on a device that is not on that list was made some other way.
3. **What the device says.** Read the device's own change record with one approved `net_read` (table below): last commit or archive log, and whether running config differs from the saved one. A difference is unsaved work, which a reload would lose.
4. **What the archive says.** `solarwinds_lookup` `device_config` returns the last archived configuration. It is a snapshot from SolarWinds' schedule, not live: say its age.
5. **Compare** by time. Match each change on the device to an approved change or a known ticket. Group the rest as unexpected, newest first, with time and (where the log shows it) the account that made it.
6. **Report:** devices with no or old backup, devices with unsaved config, changes with an approved record, changes without one. Offer netops-incident-notes if an unexpected change lines up with an outage.

| Read | ios | nxos | eos | junos | sonic_community |
|---|---|---|---|---|---|
| Unsaved vs saved | `show archive config differences nvram:startup-config system:running-config` | `show running-config diff` | `show running-config diffs` | `show system commit` | n/a, no diff read |
| Change history | `show archive log config all` | `show accounting log` | `show configuration sessions` | `show system commit` | `show logging` |

Good: a backup newer than the stale window, no unsaved config, every change matched to an approved record. Bad: no backup, config differs from saved, a change with no record, a change made outside any window.

Stop and hand back with the list and the evidence. A restore or a save is a change: netops-change-checklist, only with a catalogue entry. Treat a change with no record as a finding for the owner, not as proof of wrongdoing.

Never: print full configurations into the chat, repeat a secret or key, or run a save, restore or backup yourself.
