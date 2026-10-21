---
name: netops-routing-reachability
description: "Trace why a destination is unreachable at layer 3: route lookup, static routes, OSPF neighbours, BGP sessions and received prefixes, asymmetry. Use for \"cannot reach subnet\", OSPF or BGP neighbour down, missing routes."
triggers:
  - "cannot reach subnet"
  - "route missing"
  - "ospf neighbor down"
  - "bgp neighbor down"
  - "bgp session"
  - "static route"
  - "no route to host"
---

# NetOps routing reachability

Read-only. You need the source, the destination address and the direction that fails. Ask for them. No ping or traceroute exists here: the path is read hop by hop from routing tables.

1. **Look up** the source side gateway and each device on the likely path (`inventory_lookup`). A bundle first: `bundle=routing_info` per device and platform (routes summary, routed interfaces, OSPF, BGP).
2. **Route lookup** for the destination on the device nearest the source. Note the next hop and the protocol that supplied it. No route: go to step 4. A default route where a specific one is expected: go to step 4.
3. **Follow the next hop.** Repeat the lookup on the next device. Stop at the first device whose answer disagrees with the one before (a different next hop, no route, a loop back). Check the return path from the destination side too: asymmetry looks like a one-way failure.
4. **Protocol, from state:**
   - **Static:** is the route in the table? If not, its next hop is unreachable or the route is not configured; read the connected interface to the next hop.
   - **OSPF:** neighbours not in Full. Stuck in Init, 2-Way or ExStart/Exchange points at hello or area or timer or MTU mismatch, authentication, or a blocked link. Compare the interface settings of both ends.
   - **BGP:** session state. Idle or Active: the peer is unreachable or misconfigured (address, AS, authentication). Established with zero prefixes received: a filter on the peer, or the peer sends nothing. Prefixes received but not in the table: policy, a worse path, or next hop unreachable.
5. **Conclude:** the first hop where the path breaks and why, with the reads that show it.

| Read | ios | nxos | eos | junos | sonic_community |
|---|---|---|---|---|---|
| Route to prefix | `show ip route <prefix>` | `show ip route <prefix>` | `show ip route <prefix>` | `show route <prefix>` | `show ip route <prefix>` |
| Routes summary | `show ip route summary` | `show ip route summary` | `show ip route summary` | `show route summary` | `show ip route` |
| Connected ports | `show ip interface brief` | `show ip interface brief` | `show ip interface brief` | `show interfaces terse` | `show ip interfaces` |
| OSPF neighbours | `show ip ospf neighbor` | `show ip ospf neighbors` | `show ip ospf neighbor` | `show ospf neighbor` | n/a |
| OSPF interfaces | `show ip ospf interface brief` | `show ip ospf interface brief` | `show ip ospf interface brief` | `show ospf interface brief` | n/a |
| BGP sessions | `show ip bgp summary` | `show ip bgp summary` | `show ip bgp summary` | `show bgp summary` | `show ip bgp summary` |
| BGP prefix | `show ip bgp <prefix>` | `show ip bgp <prefix>` | `show ip bgp <prefix>` | `show route <prefix>` | `show ip bgp network <prefix>` |
| ARP | `show ip arp` | `show ip arp` | `show ip arp` | `show arp no-resolve` | `show arp` |

Community SONiC has no OSPF read: say so and ask the engineer for the neighbour state.

Good: each hop has a route to the destination and an adjacent next hop in ARP, neighbours are Full or Established, prefix counts are steady. Bad: no route, a next hop with no ARP entry, a neighbour that is not Full or Established, a session that restarted recently (check uptime), a prefix count that dropped.

Stop and hand back at the first failing hop. Say whether it looks like an addressing, adjacency, policy or physical fault and send physical to netops-interface-health. A fix is a change: netops-change-checklist, and only if a catalogue entry fits.

Never: reset a neighbour, clear a route or advise doing so; assume the return path matches the forward path; blame a peer you have not read.
