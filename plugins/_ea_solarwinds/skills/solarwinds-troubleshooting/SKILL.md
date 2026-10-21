---
name: solarwinds-troubleshooting
description: Troubleshoot network and systems issues using live SolarWinds data via the solarwinds_lookup tool -- device status, alerts, interfaces, hardware health, configuration, NetFlow and connectivity. Use when the user asks to check, investigate, or troubleshoot something against SolarWinds, or asks about a device's status, alerts, config, traffic or connections.
---

# SolarWinds Troubleshooting

Domain knowledge for using `solarwinds_lookup`, not a restatement of its
argument list -- that's in the tool's own prompt. This is about which
capability actually answers the question, and how to read what comes back.

## Start with the symptom, not the capability

A user rarely says "run node_status." They say "is the branch router down" or
"why is this slow." Map the symptom to a sequence, not a single call:

- **"Is X up / down / unreachable"** → `node_status`. If it's down, follow with
  `recent_events` on the same device -- the event log usually says *when* and
  often *why* (reboot, interface flap, polling failure).
- **"Something's slow" / "high utilisation"** → `interface_status` first (a
  saturated link is the most common cause), then `netflow_top_talkers` on the
  same device if the interface looks busy but nothing obvious explains it --
  that's the one capability that shows *what* is consuming the bandwidth, not
  just that it's consumed.
- **"Can't reach device A from device B"** → `device_neighbors` on both ends
  tells you the physical path between them; `netflow_by_ip` on the source
  address tells you whether traffic is even leaving the network in that
  direction. Together they usually localise a break to one hop.
- **"Where is this laptop/phone/MAC plugged in"** → `device_port_location`,
  not `device_neighbors` -- neighbors is device-to-device (routers, switches);
  port location is endpoint-to-switchport.
- **"What changed"** → `device_config` for the most recent archived config,
  and say plainly that it is the last *archived* snapshot, not necessarily
  the live running config at this exact second -- NCM backs up on its own
  schedule, not on demand.
- **"Is this IP free / what subnet is it in"** → `ip_lookup`.
- **A named alert, or "what's on fire right now"** → `active_alerts`, without
  a `node` argument for an instance-wide view, with one to scope it to a
  device already under discussion.

## Reading severity and status honestly

`Severity` on an alert and `Status` on a node or interface are SolarWinds'
own values -- report them as SolarWinds reports them (e.g. "Critical",
"Warning", "Down") rather than translating into a different scale. Don't
invent urgency language SolarWinds itself didn't use.

## When a capability isn't enabled

If `solarwinds_lookup` reports that a capability isn't enabled on this
instance (NetFlow/NTA, UDT, IPAM and similar are commonly not licensed on
every deployment), say so plainly and move on with what *is* available rather
than treating it as a failure to retry. It usually means the organisation's
SolarWinds instance doesn't have that module -- not that something is wrong.

## What this tool cannot do

Nothing here changes SolarWinds. It cannot acknowledge an alert, push a
configuration, or trigger a fresh config pull from a device -- `device_config`
reads whatever NCM already archived, never a live one. If a task genuinely
needs to change something, say plainly that this tool can only read, and that
making a change is outside what it's for.

## Naming a device

Most capabilities match a device by a name fragment against SolarWinds' own
`Caption` field, or by exact IP -- the fragment match is loose, so an exact
case or full hostname isn't required. If a user gives a nickname that doesn't
obviously match anything, try `node_search` first with the fragment they gave
rather than guessing a full hostname.
