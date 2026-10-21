---
name: netops-spanning-tree
description: "Check spanning tree for a loop, a broadcast storm, a wrong root bridge, blocked or inconsistent ports, topology changes. Use for a flooding network, MAC flapping, slow network after a switch was added."
triggers:
  - "spanning tree"
  - "stp problem"
  - "broadcast storm"
  - "network loop"
  - "root bridge"
  - "mac flapping"
  - "topology change"
---

# NetOps spanning tree

Read-only. A loop can take the management path down too: if a device stops answering, report that and ask the engineer for another path; do not keep requesting.

1. **Symptoms:** many users slow or down at once, high CPU on switches, one MAC seen on many ports, link lights busy with no real load. A storm is an emergency: say so first, then read.
2. **Scope:** which VLANs and which switches. `inventory_ports` `vlan=<id>` shows who carries it.
3. **Root:** on each switch read the root and summary. All switches must agree on one root per VLAN, and it should be the planned core, not an old access switch. A different root after a change is the usual cause of a slow network.
4. **Ports:** blocked and inconsistent ports. Blocked on the expected link is normal; blocked where nothing should be, or an inconsistent port (root, loop or type guard fired), is a lead.
5. **Topology changes:** count and age of the last change, and the port it came from. Frequent changes mean a flapping port: netops-interface-health on it.
6. **MAC flapping** in the log or the MAC table on the same VLAN points at the loop. Find the ports it moves between.
7. **Conclude:** root in the wrong place, a loop between two ports, or a flapping link, with the reads that show it.

| Read | ios | nxos | eos | junos | sonic_community |
|---|---|---|---|---|---|
| Summary | `show spanning-tree summary` | `show spanning-tree summary` | `show spanning-tree` | `show spanning-tree bridge` | n/a |
| Root | `show spanning-tree root` | `show spanning-tree root` | `show spanning-tree root` | `show spanning-tree bridge` | n/a |
| One VLAN | `show spanning-tree vlan <id>` | `show spanning-tree vlan <id>` | `show spanning-tree vlan <id>` | `show spanning-tree interface` | n/a |
| Blocked ports | `show spanning-tree blockedports` | `show spanning-tree blockedports` | `show spanning-tree blockedports` | `show spanning-tree interface` | n/a |
| Inconsistent | `show spanning-tree inconsistentports` | `show spanning-tree inconsistentports` | `show spanning-tree` | `show spanning-tree interface` | n/a |
| Log | `show logging` | `show logging last 50` | `show logging last 30 minutes` | `show log messages` | `show logging` |

Community SONiC has no spanning-tree read: say so, and use the MAC table and the log (netops-find-endpoint, netops-interface-health) to look for a loop instead.

Good: one agreed root, blocked ports where redundancy is expected, topology changes old and rare. Bad: two roots, a root with a high priority value on the wrong device, a rising change counter, loop or root guard messages, MAC move messages.

Stop and hand back: name the loop or the wrong root and the proof. Do not suggest shutting ports, changing priorities or clearing anything; those are changes (netops-change-checklist) and in a storm the engineer decides. On a storm the safest read-only help is to list which ports carry the traffic and where the suspect link is.

Never: assume the root from the device name, run more than a few reads against a struggling device, or give a command that is not a plain show.
