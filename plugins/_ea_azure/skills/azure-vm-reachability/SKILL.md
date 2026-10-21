---
name: azure-vm-reachability
description: "Find why one Azure VM cannot reach another address. Inventory first, then IP flow verify and next hop. Use for \"VM cannot reach\", \"NSG blocking\", \"no route in Azure\"."
triggers:
  - "vm cannot reach"
  - "nsg blocking"
  - "no route in azure"
---

# Azure VM reachability

Read-only. You need both ends, the port and the protocol (TCP or UDP). Ask if either is missing.

1. `azure_inventory` for both VMs, their NICs, subnet and NIC security groups, route tables and peerings. Say the as-of line.
2. `azure_read` `vm_instance_view` and `resource_health` on each VM. A stopped VM cannot be probed. Starting it is a change the operator drafts (a card and a CR or SR number), not a call you make and not a suggestion.
3. `ip_flow_verify` on the source VM (outbound) and the destination VM (inbound): direction, protocol TCP or UDP, local and remote IP and port. It names the security-group rule. It does not see Azure Firewall.
4. `next_hop` from the source to the destination IP. Unexpected hop: `effective_routes` on the source NIC. A surprising rule name: `effective_security_rules` on that NIC.
5. Next hop is a firewall or appliance: say so and load azure-firewall-drop. A peering is on the path: load azure-peering.
6. `connection_check` on the source VM only when the steps above say allowed (protocol Tcp or Icmp). It can need the Network Watcher agent. Never install that agent.
7. Conclude with the first thing that drops, the rule or route name, and the read it came from.

A security-group or route fix that removes or narrows a rule is a suggestion: the portal steps, what to check first, how to confirm. You do not change it. Adding a route is a change card with a CR or SR number, not a call you make.

Never: reset a NIC, restart the VM, or deallocate it. Those are suggestions with a runbook, never actions.
