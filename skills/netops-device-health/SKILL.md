---
name: netops-device-health
description: "Check a device's own health: high CPU or memory, recent reboot or crash, power, fans, temperature, and what the logs say. Use for \"device slow\", \"high CPU\", \"it rebooted\", \"check the logs\"."
triggers:
  - "high cpu"
  - "memory high"
  - "device rebooted"
  - "why did it reload"
  - "check the logs"
  - "fan or power alarm"
  - "device health"
---

# NetOps device health and logs

Read-only. Say which device and when it was bad. Do not run more reads on a struggling device than needed: one request, five commands at most, then think.

1. **Look up** the device (`inventory_lookup`). Add SolarWinds `hardware_health` (CPU, fan, temperature, power, voltage) and `volume_usage` for history, and `active_alerts` for the node. SolarWinds may lag the device; say so.
2. **Uptime and reboot reason.** A recent reload explains many things: cause (power, crash, user), time, and whether a crash left a core file. Compare the reload time with when the complaint began.
3. **CPU.** Current and recent averages, and the top process. A short spike is normal; a sustained high value is not. The busiest process says which feature: routing protocol, ARP or MAC learning, logging, management access. High CPU with a broadcast or loop symptom: netops-spanning-tree.
4. **Memory.** Used against total, and the trend between two reads. Memory that only grows over days is a leak: note the software version and hand it to the researcher or the vendor case.
5. **Environment.** Power supplies, fans, temperature, optics. Any alarm or a failed redundant unit counts, even if traffic is fine.
6. **Logs.** Read the recent log once. Group by message type, give counts and first and last times. Look for interface flaps, protocol neighbour changes, authentication failures, config changes, hardware or parity errors. A burst at the complaint time matters; a steady old message does not.
7. **Conclude:** healthy, degraded or failing, with each number and message you used, and what would change your mind.

| Read | ios | nxos | eos | junos | sonic_community |
|---|---|---|---|---|---|
| Uptime, reload reason | `show version` | `show system reset-reason` | `show reload cause` | `show system uptime` | `show reboot-cause` |
| CPU | `show processes cpu sorted` | `show processes cpu` | `show processes top once` | `show chassis routing-engine` | `show processes cpu` |
| Memory | `show processes memory sorted` | `show system resources` | `show version` | `show system memory` | `show system-memory` |
| Power, fans, temperature | `show environment all` | `show environment` | `show environment all` | `show chassis environment` | `show environment` |
| Alarms | n/a, see the log | n/a, see the log | n/a, see the log | `show system alarms` | n/a, see the log |
| Log | `show logging` | `show logging last 50` | `show logging last 30 minutes` | `show log messages` | `show logging` |

Good: CPU and memory steady and well below the limits, redundant power and fans present, no new alarms. Bad: sustained CPU above about 80 percent, memory climbing between reads, a recent unexplained reload, a failed power supply or fan, a hardware error repeated in the log.

Stop and hand back when the evidence points at hardware (replace, vendor case), a software defect (version, upgrade) or a feature overloading the device. Config changes go through netops-change-checklist. Never reload, clear logs or counters, or tell the engineer to; never read config or core files in the hope of finding a cause.
