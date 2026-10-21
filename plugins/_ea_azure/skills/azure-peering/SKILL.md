---
name: azure-peering
description: "Find why an Azure virtual-network peering is not passing traffic. Use for \"peering disconnected\", \"peering initiated\", \"spoke cannot reach the hub\"."
triggers:
  - "peering disconnected"
  - "peering initiated"
  - "vnet peering down"
---

# Azure peering

Read-only. Ask which two networks, and a VM on each side if traffic is the complaint.

1. `azure_inventory` for both networks, then `azure_read` `vnet_peerings` on each. Use the fields it returns: `state`, `sync`, `allow_forwarded`, `allow_gateway_transit`, `use_remote_gateways`, `remote_address_space`. Overlap is that space against the inventory address space. A flag the read did not return is unknown, not false.
2. Initiated means the other side is missing. Disconnected often follows an address-space change.
3. Traffic still fails with both sides Connected: `next_hop` and `ip_flow_verify` between a VM on each side (azure-vm-reachability steps 3 and 4). Through a hub firewall, `allow_forwarded` must be on for the spoke, then load azure-firewall-drop. To on-prem through the hub gateway, load azure-onprem-reach.
4. Conclude with the flag or the hop that fails, and the read that showed it.

Disconnected is fixed by delete and re-create on both sides. That is a suggestion with a runbook (what to note first, the portal steps, how to confirm). You never delete a peering. A flag that only allows more traffic is a change card with a CR or SR number, not a call you make.
