---
name: netops-loss-latency-mtu
description: "Find where packet loss, high latency or an MTU black hole comes from using drops, errors, queues, utilisation and MTU settings. Use for \"slow\", \"packet loss\", \"works small but not large\", VPN or file transfers stalling."
triggers:
  - "packet loss"
  - "high latency"
  - "slow network"
  - "mtu problem"
  - "large packets fail"
  - "drops on the interface"
  - "jitter"
---

# NetOps loss, latency and MTU

Read-only. The agent cannot ping or trace: those are not `show` commands. Where a ping or a test from a host would settle it, ask the engineer to run it and paste the result; treat the paste as their evidence, quoted as such.

1. **Define it:** which source and destination, one way or both, constant or in bursts, since when, all traffic or only large packets, only one application. Loss from one source only is a path or host fault; loss for everyone is a shared link or device.
2. **Path:** from the routing reads (netops-routing-reachability) list each device and link between the two. Work on the hops, not the whole path.
3. **Per hop, the same three reads** (one platform per request): the interface detail (rate, errors, drops, MTU), the error counters, and the queue or policy drops. Read twice a few minutes apart; only what rises counts.
4. **Where is the loss?** Output drops on a busy interface: congestion. Input errors or CRC: physical (netops-interface-health). Queue or policy drops: QoS or a policer. Neither: the device itself (netops-device-health).
5. **Utilisation:** `solarwinds_lookup` `interface_status` (and `netflow_top_talkers` if NetFlow is enabled) show how busy the link is and what fills it. Sustained use near the line rate matches loss and added latency.
6. **MTU:** compare the MTU on both ends of every link and on tunnels and VLAN interfaces on the path. A smaller value in the middle plus the don't-fragment bit explains "small works, large stalls". Giant or fragment counters rising at a hop confirm it.
7. **Conclude:** the first hop with the evidence, and what would change your mind.

| Read | ios | nxos | eos | junos | sonic_community |
|---|---|---|---|---|---|
| Port detail, rate, drops | `show interfaces <intf>` | `show interface <intf>` | `show interfaces <intf>` | `show interfaces <intf> extensive` | `show interfaces counters` |
| Errors | `show interfaces counters errors` | `show interface counters errors` | `show interfaces counters errors` | `show interfaces <intf> extensive` | `show interfaces counters errors` |
| Queue and policy drops | `show policy-map interface <intf>` | `show queuing interface <intf>` | `show interfaces counters discards` | `show interfaces queue <intf>` | `show queue counters` |
| IP MTU | `show ip interface <intf>` | `show interface <intf>` | `show interfaces <intf>` | `show interfaces <intf>` | `show interfaces status` |
| CPU | `show processes cpu sorted` | `show processes cpu` | `show processes top once` | `show chassis routing-engine` | `show processes cpu` |

Good: drops and errors flat between two reads, MTU equal at every hop, utilisation well below the line rate. Bad: output drops rising on a busy link, an MTU smaller than the rest, errors on one hop, CPU high with drops on control traffic.

Stop and hand back: name the hop and the kind of loss. Bandwidth, QoS and MTU fixes are changes (netops-change-checklist, only with a catalogue entry); otherwise write it up as a proposal. Never claim a path is clean from counters alone; say what the counters cannot show.

Never: guess an MTU, call a link fine on a single read, or ask the engineer for credentials.
