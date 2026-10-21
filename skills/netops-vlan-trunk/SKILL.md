---
name: netops-vlan-trunk
description: "Find why a VLAN does not reach a host or a trunk drops traffic: VLAN missing, trunk allowed list, native mismatch, wrong access VLAN. Use for \"cannot reach on VLAN\", \"VLAN not passing\", trunk problems."
triggers:
  - "vlan not working"
  - "trunk problem"
  - "native vlan mismatch"
  - "vlan missing"
  - "which ports carry vlan"
  - "host in the wrong vlan"
---

# NetOps VLAN and trunk problems

Read-only. Get the VLAN id, the two end devices and the access or trunk ports between them. Ask if any is missing.

1. **Look up** every device on the path (`inventory_lookup`: platform, role). `inventory_ports` with `vlan=<id>` lists every device and port the inventory last saw carrying the VLAN; a switch missing from the path is the first lead. It is stored data: say how old it is.
2. **VLAN exists** on each switch: `bundle=switch_info` (VLANs, port status, neighbours) or the VLAN row below. A VLAN missing, suspended or with a different name on one switch breaks the path there.
3. **Access port:** is the host's port in the right VLAN, up, and not err-disabled? Check the switchport row.
4. **Trunk:** for each link on the path, on both ends: trunking, allowed VLANs include the id, native VLAN equal on both ends, and the VLAN active (not pruned) on the trunk.
5. **MAC learning:** is the host's MAC in the table on the right port and VLAN? If the MAC is not learned at all, the problem is at or below the access port. If it is learned on an unexpected port, you found a loop or a wrong path.
6. **Gateway:** the VLAN's interface (SVI or IRB) up, with the expected address. Routing from there is netops-routing-reachability.
7. **Conclude:** the first hop where the VLAN stops, with the read that shows it.

| Read | ios | nxos | eos | junos | sonic_community |
|---|---|---|---|---|---|
| VLAN list | `show vlan brief` | `show vlan brief` | `show vlan` | `show vlans` | `show vlan brief` |
| Trunks | `show interfaces trunk` | `show interface trunk` | `show interfaces trunk` | `show ethernet-switching interface` | `show vlan brief` |
| Port mode | `show interfaces switchport` | `show interface switchport` | `show interfaces switchport` | `show ethernet-switching interface` | `show vlan brief` |
| MAC in VLAN | `show mac address-table vlan <id>` | `show mac address-table vlan <id>` | `show mac address-table vlan <id>` | `show ethernet-switching table` | `show mac` |
| Port state | `show interfaces status` | `show interface status` | `show interfaces status` | `show interfaces terse` | `show interfaces status` |

Good: VLAN present everywhere on the path, allowed lists include it, native VLANs match, MAC learned on the expected port. Bad:

- **VLAN absent on a switch in the path**, or missing from a trunk's allowed list: traffic stops there.
- **Native VLAN differs between the ends:** untagged frames land in the wrong VLAN; the log or neighbour table may warn of it.
- **Host port in another VLAN or in a voice VLAN only:** wrong access assignment.
- **A MAC that keeps moving between ports:** a loop or duplicate address, see netops-spanning-tree.

Stop and hand back when you have the first failing hop. A fix (add the VLAN, change the allowed list or the access VLAN) is a change: draft it with netops-change-checklist, and only if a catalogue entry exists. Never guess a VLAN id; never edit trunks yourself.
