---
name: netops-alert-triage
description: "Triage SolarWinds alerts: group them, find the root device, tell real from flapping or noise, and hand back. Use when alerts are firing, an alert storm hits, or someone asks what is on fire."
triggers:
  - "alert storm"
  - "what's alerting"
  - "what is on fire"
  - "solarwinds alert"
  - "node down alert"
  - "too many alerts"
---

# NetOps alert triage

Read-only. `solarwinds_lookup` cannot acknowledge, mute or change anything: say so if asked. Report severity and status in SolarWinds' own words.

1. **See all of it.** `solarwinds_lookup` `active_alerts` without `node`. Group by site, by device, by alert name and by start time. Say the count and the oldest.
2. **Find the root.** Many alerts in the same minute usually share one parent. Look for a down node, a down uplink or a down site router first: `node_status`, then `device_neighbors` for what hangs off it. A child alert under a down parent is a symptom, not a second fault. A parent that is only "Unreachable" may mean its parent is down: go up one level.
3. **Time.** `recent_events` on the root: when it started, reboot, interface flap, polling failure. Compare with changes in this chat and with known maintenance. Ask the engineer about windows.
4. **Classify** each group, and say which rule you used:
   - **Real:** state confirmed by a second source (events, `interface_status`, neighbours, or an approved device read).
   - **Flapping:** the same alert clears and returns; count the cycles. Volatile metrics such as CPU often flap around the threshold.
   - **Suppressed or acknowledged:** ask who and until when. An acknowledgement is not a resolution, and when a suppression ends the alert fires again at once if the condition still holds.
   - **Noise:** a threshold nobody acts on, or a duplicate. Do not dismiss it; list it for the owner to tune.
5. **Confirm the real ones on the device.** One approved `net_read` of the affected device: `bundle=switch_info` or the interface read for the symptom (see netops-interface-health, netops-device-health). A SolarWinds view can lag; say how old it is.
6. **Hand back** a short table: group, root device, class, evidence, next safe step and who should act. Offer netops-troubleshooting for a real fault and netops-incident-notes when it is a live incident.

| Confirm on the device | ios | nxos | eos | junos | sonic_community |
|---|---|---|---|---|---|
| Port state | `show interfaces status` | `show interface status` | `show interfaces status` | `show interfaces terse` | `show interfaces status` |
| Neighbours | `show cdp neighbors` | `show cdp neighbors` | `show lldp neighbors` | `show lldp neighbors` | `show lldp table` |
| Log | `show logging` | `show logging last 50` | `show logging last 30 minutes` | `show log messages` | `show logging` |

Good: one root with its children explained, evidence from two sources. Bad: alerts you cannot attach to a root, a status the device contradicts, an alert for a device not in Inventory (say so; offer netops-device-onboarding).

If a SolarWinds module is not enabled, say so and carry on with what is. Do not retry it.

Never: say an alert is resolved because it was acknowledged, advise muting or deleting alerts as the fix, or read a device outside the card flow.
