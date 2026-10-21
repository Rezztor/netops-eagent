---
name: netops-interface-health
description: "Diagnose an interface that is down, erroring, flapping or saturated using read-only counters and logs. Use for port down, CRC or input errors, link flaps, wrong speed or duplex, a bad optic or cable."
triggers:
  - "interface down"
  - "port is down"
  - "link flapping"
  - "crc errors"
  - "input errors"
  - "port not coming up"
  - "speed duplex mismatch"
---

# NetOps interface health

Read-only. Layer 1 and 2 only. Name the interface and the device on both ends; ask for the far end if the engineer has not given it.

1. **Look up** the device (`inventory_lookup`) for platform and role. `inventory_ports` gives the last observed port state, uplink and neighbour; say how old `observed_at` is, it is not live.
2. **Status.** Request `net_read` with `reads/show` and the status commands below (or `reads/interfaces` on ios and SONiC with the `interface` variable). One platform per request. Stop at the card; on Approve run with the `change_id`.
3. **Two samples.** Counters are only useful as a change. Read the counters once, ask the engineer to approve a second read after a few minutes (say the interval you want), and compare. A large old total that does not move is history, not the fault.
4. **Both ends.** Repeat on the neighbour (`inventory_ports` or the LLDP/CDP line names it). A one-sided error points at the receiver's side: optic, cable or port on that end.
5. **Logs.** One read of the log around the first complaint: link up and down events, err-disable, flap damping, power or optic messages. Count the flaps.
6. **Add SolarWinds** `interface_status` and `recent_events` for utilisation and the event time when it is available.
7. **Conclude** with the table below and say what would change your mind.

| Read | ios | nxos | eos | junos | sonic_community |
|---|---|---|---|---|---|
| Status of all ports | `show interfaces status` | `show interface status` | `show interfaces status` | `show interfaces terse` | `show interfaces status` |
| One port in detail | `show interfaces <intf>` | `show interface <intf>` | `show interfaces <intf>` | `show interfaces <intf> extensive` | `show interfaces counters` |
| Error counters | `show interfaces counters errors` | `show interface counters errors` | `show interfaces counters errors` | `show interfaces <intf> extensive` | `show interfaces counters errors` |
| Optic power | `show interfaces transceiver` | `show interface transceiver details` | `show interfaces transceiver` | `show interfaces diagnostics optics <intf>` | `show interfaces transceiver status` |
| Log | `show logging` | `show logging last 50` | `show logging last 30 minutes` | `show log messages` | `show logging` |

Logs can be long (Junos `show log messages` returns the whole file): read them once and summarise.

Reading it:

- **Down / notconnect:** no cable or far end off. **err-disabled:** a protection fired (the log says which). **Administratively down:** someone shut it; find out who and why before anything else.
- **CRC, FCS, input errors rising:** cable, optic or duplex. Rising on one end only: that end's receiver. **Output drops rising with a busy port:** congestion, see netops-loss-latency-mtu.
- **Speed or duplex not auto on both ends:** a mismatch gives late collisions and slow throughput.
- **Optic power far below or above its thresholds:** dirty or failing optic or fibre.
- **Several flaps close together:** physical first. A flap at the same minute on many ports: look up the stack for a power or switch event.

Stop and hand back when the evidence says a cable, optic or port must be swapped or a change is needed: say which, with the proof, and use netops-change-checklist for any config. Never clear counters, shut or enable a port, or tell the engineer to; those are changes.
