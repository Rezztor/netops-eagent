---
name: netops-core-services
description: "Check the network side of NTP, DHCP and DNS: clock sync, relay and snooping, bindings, resolver settings. Use for \"wrong time\", logs out of order, clients get no address, names do not resolve."
triggers:
  - "ntp not syncing"
  - "clock is wrong"
  - "dhcp not working"
  - "no ip address"
  - "dns not resolving"
  - "dhcp relay"
  - "time drift"
---

# NetOps NTP, DHCP and DNS checks

Read-only. You can see what the network devices say. You cannot test a name lookup or ask a DHCP server for a lease; for that ask the engineer to run it from an affected host and paste the result.

1. **Define it:** which clients or devices, which VLAN or site, since when, and whether new or old clients fail.
2. **NTP** (devices): read clock and NTP state on the affected device and one healthy peer. Synchronised, the stratum, the chosen server and a small offset are good. Unsynchronised, no server reachable, or a large offset is bad. Wrong time breaks log correlation, certificates and tokens. Compare the two devices' time.
3. **DHCP**, in this order:
   - the client's VLAN exists and its port is up (netops-vlan-trunk);
   - the gateway interface has the DHCP relay (helper) address, and it matches the server;
   - the relay or snooping counters rise when clients ask; no rise means requests never arrive;
   - bindings or leases exist for the VLAN's range (when the device itself serves DHCP), and the pool is not exhausted;
   - snooping or trust settings are not dropping offers on an uplink.
4. **DNS:** read the device's resolver settings and domain. Which resolver the clients get is a DHCP option: confirm it in the server's or relay's settings if the device shows it. SolarWinds `ip_lookup` shows whether an address is in use. A name that works by address but not by name is DNS, not routing.
5. **Conclude:** which service, which hop, and the evidence. A server being down or a scope being full is outside what you can read: name the server and hand over.

| Read | ios | nxos | eos | junos | sonic_community |
|---|---|---|---|---|---|
| Clock | `show clock` | `show clock` | `show clock` | `show system uptime` | `show clock` |
| NTP state | `show ntp status` | `show ntp peer-status` | `show ntp status` | `show ntp status` | `show ntp` |
| NTP servers | `show ntp associations` | `show ntp peers` | `show ntp associations` | `show ntp associations` | `show ntp` |
| DHCP relay | `show ip interface <intf>` | `show ip dhcp relay` | `show ip dhcp relay counters` | `show dhcp relay statistics` | n/a |
| DHCP snooping | `show ip dhcp snooping` | `show ip dhcp snooping` | `show ip dhcp snooping` | `show dhcp-security binding` | n/a |
| Leases | `show ip dhcp binding` | `show ip dhcp relay statistics` | `show dhcp server` | `show dhcp server binding` | n/a |
| Resolver | `show hosts` | `show hosts` | `show hosts` | `show configuration system name-server` | n/a |

Community SONiC has reads for the clock and NTP only; for DHCP and DNS ask the engineer for the relay and resolver settings.

Good: NTP synchronised on both devices with offsets near zero, relay present and counters rising, bindings for the VLAN. Bad: unsynchronised or no server reachable, clocks that differ, relay missing or pointing at the wrong server, counters flat, a snooping drop count rising on an uplink.

Stop and hand back: say whether the fault is on the device (NTP source, relay, snooping) or beyond it (the server, the scope, the resolver). Config fixes go through netops-change-checklist.

Never: invent an address for a server, test by changing a setting, or assume the resolver from a hostname.
