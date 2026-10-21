---
name: netops-troubleshooting
description: "Structured triage for network faults with read-only checks: scope, severity, layers, recent changes, healthy-vs-failing comparison, evidence-backed conclusion. Use when something is down, slow, flapping or unreachable."
triggers:
  - "network is down"
  - "cannot reach"
  - "packet loss"
  - "interface flapping"
  - "troubleshoot"
  - "something is wrong with"
  - "users cannot connect"
---

# NetOps troubleshooting

Read-only. Never change anything while triaging; a fix goes through netops-change-checklist. This skill is the entry point: it frames the fault, then hands to the specialist skill below.

**Reads.** Every `net_read` is requested first and shown to the engineer as a card: after it appears, stop and wait; on Approve call `net_read` with `action=run` and the `change_id`; on Reject stop. Never read a device outside that flow, and never ask for credentials before a pre-check says one is missing (then the secure form appears). Prefer a bundle (`bundle=switch_info` or `routing_info`; one platform per request). When no fixed read answers, request `reads/show` with up to five plain `show` commands in the device's own syntax (look up its platform with `inventory_lookup`); the engineer may edit them on the card, and you run the same `change_id`.

1. **Scope:** what fails, for whom, since when, how many devices or sites, one way or both. Ask if unclear.
2. **Severity:** impact times urgency. Many users or a critical site now: high. When unsure, treat it as higher and say so. High severity: suggest netops-incident-notes alongside.
3. **Recent changes first:** `net_change` `action=list` (the NetOps Operator only; the troubleshooter asks the engineer), `solarwinds_lookup` (`active_alerts`, `recent_events`), and the device log (one approved read). Ask what changed. A fault that starts at a change time is a lead, not a proof.
4. **Divide and conquer:** start where the answer splits the problem: the route lookup at the source gateway, or the failing device's state. Then move down or up. Say which layer each check rules in or out: physical, link, VLAN and STP, IP and routing, policy and firewall, application.
5. **Compare** with a healthy peer or path using the same reads; list the differences.
6. **Conclude:** most likely cause, confidence, evidence (which tool output), whether it explains all the symptoms, what would change your mind, the next safe read-only check.
7. **Parallel checks:** only the NetOps Operator can delegate independent read-only checks to sub-agents; the troubleshooter has no such tool. Give each a narrow question.

| Symptom | Load |
|---|---|
| Port down, errors, flapping | netops-interface-health |
| VLAN not reaching, trunk | netops-vlan-trunk |
| Loop, storm, slow after a switch change | netops-spanning-tree |
| Subnet unreachable, OSPF or BGP down | netops-routing-reachability |
| Slow, loss, large packets fail | netops-loss-latency-mtu |
| Wrong time, no DHCP, no DNS | netops-core-services |
| Blocked by a firewall, NAT | netops-firewall-path |
| Tunnel down | netops-vpn-tunnel |
| Device slow, rebooted, logs | netops-device-health |
| Where is this host | netops-find-endpoint |
| Many alerts at once | netops-alert-triage |
| What changed, config differs | netops-config-drift |

Stop and escalate to the engineer when the evidence is thin, a read keeps failing, or the next step is a change: say what you know, what you need, and what you ruled out.

Never ask for or echo credentials, run a command that is not a plain `show`, or call a cause proven that only fits some of the symptoms.
