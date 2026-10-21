---
name: netops-firewall-path
description: "Work out whether a firewall, ACL or NAT is blocking or rewriting a flow: zone, route, policy hit, NAT, session. Use for \"is this traffic allowed\", blocked application, NAT or port-forward questions."
triggers:
  - "is this traffic allowed"
  - "firewall blocking"
  - "firewall rule"
  - "nat problem"
  - "port forward"
  - "acl blocking"
  - "policy hit"
---

# NetOps firewall policy and NAT path

Read-only. Get the flow first: source, destination, protocol and port, direction, and which firewall(s) sit between. Ask for what is missing; never assume a rule exists.

Reads exist today for ios, nxos, eos, junos and SONiC only. **FortiGate and Palo Alto firewalls cannot be read by the agent yet.** For those, give the engineer the exact commands below to run themselves and paste the output back (remove any key or password first: you will not repeat one). Label everything pasted as the engineer's evidence.

1. **Find the firewalls** on the path: `inventory_lookup` with `device_type=firewall` and the `site`. Ask which pair carries this flow. An HA pair: both members matter; ask which is active.
2. **Routing first.** Is there a route for the destination on the firewall (netops-routing-reachability)? Traffic with no route never reaches a policy.
3. **Zones and policy.** Which ingress and egress zone or interface does the flow use, and which rule matches first, top to bottom? A deny, an implicit deny at the end, or a rule above the intended one is the usual answer.
4. **NAT.** Is there a translation for the flow, in which direction, and does the policy match the pre-NAT or post-NAT address (the platform decides)? A missing return translation looks like a one-way failure.
5. **Session.** Does a session exist for the flow, which rule and NAT it used, and are its counters moving in both directions? A session with traffic only one way means the return path is wrong.
6. **ACLs on routers and switches** along the path: hit counters and the direction applied.
7. **Conclude** with the first element that drops or rewrites the flow, the rule or entry name, and the evidence. If there is no read, say which command the engineer should run next.

| Read | ios | nxos | eos | junos (SRX) | fortios (engineer runs) | panos (engineer runs) |
|---|---|---|---|---|---|---|
| Rules and hits | `show access-lists` | `show ip access-lists` | `show ip access-lists` | `show security policies hit-count` | `diagnose firewall iprope list` | `show running security-policy` |
| Policy for the zones | n/a | n/a | n/a | `show security policies from-zone <a> to-zone <b>` | `show firewall policy` | `test security-policy-match source <ip> destination <ip> destination-port <n> protocol <p>` |
| NAT | `show ip nat translations` | n/a | n/a | `show security nat source rule all` | `show firewall central-snat-map` | `show running nat-policy` |
| Session | n/a | n/a | n/a | `show security flow session source-prefix <ip>` | `diagnose sys session list` | `show session all filter source <ip> destination <ip>` |
| HA state | n/a | n/a | n/a | `show chassis cluster status` | `get system ha status` | `show high-availability state` |
| Route lookup | `show ip route <ip>` | `show ip route <ip>` | `show ip route <ip>` | `show route <ip>` | `get router info routing-table details <ip>` | `test routing fib-lookup virtual-router <vr> ip <ip>` |

Good: a route, an allow rule matching the flow, the right NAT entry, a session with counters in both directions. Bad: no route, a deny hit or implicit deny, a rule order problem, NAT on the wrong side, a session with one-way counters, an HA pair where the passive unit holds the path.

Stop and hand back with the element and the evidence. A rule or NAT change is a change: netops-change-checklist, only with a catalogue entry; otherwise a written proposal naming the rule, the zones and the rollback. Security questions (should this be allowed?) belong to the owner of the policy, not to you.

Never: say a flow is allowed without a policy match, suggest disabling a rule or the firewall, run a packet capture or a debug, or repeat a key from pasted output.
