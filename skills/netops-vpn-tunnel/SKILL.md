---
name: netops-vpn-tunnel
description: "Troubleshoot a site-to-site IPsec tunnel that is down or passes no traffic: IKE phase 1, phase 2, proxy IDs, routes. Use for \"VPN down\", \"tunnel flapping\", \"IKE not establishing\", \"tunnel up but no traffic\"."
triggers:
  - "vpn down"
  - "tunnel down"
  - "ipsec problem"
  - "ike phase 1"
  - "tunnel flapping"
  - "tunnel up but no traffic"
---

# NetOps IPsec tunnel

Read-only. Get both tunnel ends, which device holds each, and what changed lately on either side. Ask for the peer's owner if the far end is not ours.

Reads exist for ios and junos here. **FortiGate and Palo Alto cannot be read by the agent yet:** give the engineer the commands below to run and paste, and remove any pre-shared key first; you will not repeat one.

1. **Ends and path:** `inventory_lookup` both ends (platform, HA). Is the peer address reachable by routing (netops-routing-reachability) and not blocked by an upstream filter on UDP 500, UDP 4500 and ESP?
2. **Phase 1 (IKE):** does a security association exist, in what state? None, or stuck in a first state: the peer does not answer, the proposals do not match (encryption, hash, group, lifetime), the pre-shared key or identity differs, or NAT-T is needed. Read the log for the negotiation error and quote it.
3. **Phase 2 (IPsec):** is there an SA for the traffic? Phase 1 up and phase 2 missing: proxy IDs or traffic selectors differ between the ends, or a transform mismatch. Compare local and remote selectors on both sides exactly.
4. **Traffic:** packets encrypted and decrypted counters. Encrypted rising and decrypted flat: the far side drops it. Neither rising: nothing is routed into the tunnel (route, policy or NAT exemption missing). One direction only: a return-path problem.
5. **Flapping:** compare the lifetime and uptime, DPD or keepalive messages, rekey failures at the same interval, and the underlay link (netops-interface-health).
6. **Conclude:** which phase fails, why, and the proof from both ends if you have both.

| Read | ios | junos | fortios (engineer runs) | panos (engineer runs) |
|---|---|---|---|---|
| IKE state | `show crypto isakmp sa` | `show security ike security-associations` | `diagnose vpn ike gateway list` | `show vpn ike-sa` |
| IPsec state | `show crypto ipsec sa` | `show security ipsec security-associations` | `diagnose vpn tunnel list` | `show vpn ipsec-sa` |
| Log | `show logging` | `show log messages` | `diagnose vpn ike log-filter dst-addr4 <ip>` | `show log system` |
| Route to peer | `show ip route <peer>` | `show route <peer>` | `get router info routing-table details <ip>` | `show routing route destination <ip>` |

Good: an IKE SA and an IPsec SA that stay up, encrypted and decrypted counters both rising, matching selectors. Bad: no phase 1 SA, a proposal or identity mismatch in the log, phase 2 selectors that differ, a rekey that fails, decrypted counters flat.

Stop and hand back: name the failing phase and the mismatch, and whose side it is. A change on our side goes through netops-change-checklist (only with a catalogue entry); a change on the peer's side is a message to their owner, which you may draft. Never ask for or show a pre-shared key, and never suggest clearing a security association or restarting the tunnel.
