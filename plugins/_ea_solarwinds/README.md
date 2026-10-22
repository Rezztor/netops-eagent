# SolarWinds

Lets the agent read live data from one SolarWinds instance — device status,
alerts, configuration, hardware health, NetFlow, and connectivity. Read-only.
It cannot change anything in SolarWinds, and cannot reach any instance other
than the one an administrator configures.

## Why a fixed set of capabilities, not a query box

The agent never writes SWQL. It names a **capability** —
`node_status`, `active_alerts`, `netflow_by_ip` — and this plugin resolves
that to one fixed query template with bound parameters. There is no argument
on the agent's side that becomes query text directly, the same design
`helpers/mailer.py` uses for email: a recipient is an account, never an
address; here, a query is a capability, never raw SWQL.

Read-only is structural, not a setting. SolarWinds splits reads and writes
into different URL paths — `/Query` and `/Invoke/{entity}/{verb}` — and this
plugin's HTTP client only has a method for the first. The write path isn't
disabled; it was never implemented, so there is nothing a bug or a bad prompt
could accidentally reach.

## Built broad, on purpose

This plugin serves different customer organisations with different
SolarWinds setups — some license NetFlow (NTA), User Device Tracker, or IP
Address Manager; some don't. The capability list is not narrowed to what any
one instance happens to have. Calling a capability whose module isn't
installed on a given instance reports that plainly — "isn't enabled on this
SolarWinds instance" — rather than a raw error, and rather than the
capability being missing from the build in the first place.

## What it can do

| Capability | Answers |
|---|---|
| `node_status` | Is this device up? Vendor, IP, last response time. |
| `node_search` | Which nodes match a name/IP/vendor fragment? |
| `node_inventory` | Full inventory for a device. |
| `active_alerts` | What's currently alerting, and how severely? |
| `recent_events` | What happened on this node recently? |
| `interface_status` | Are this device's interfaces up? Utilisation? |
| `volume_usage` | How full are this device's disks/volumes? |
| `application_status` | Are this device's monitored (SAM) applications healthy? |
| `hardware_health` | CPU, fan, temperature, power supply, voltage. |
| `device_config` | Newest *archived* configs (NCM), at most 5 per call, each cut to 200 lines (`before` pages to older ones) — never a live pull. |
| `netflow_top_talkers` | Which IP pairs move the most traffic through this device? |
| `netflow_by_ip` | What is this IP sending or receiving? |
| `device_neighbors` | What is this device directly connected to? |
| `device_port_location` | Which switch/port is this MAC or IP plugged into? |
| `ip_lookup` | Is this IP in use? Which subnet/VLAN? |

## Configuration

Settings → SolarWinds. Host, port, TLS verification, username, timeout, row
cap and per-conversation rate limit are ordinary plugin settings. The
password is not: it is written to `usr/.env` and never appears in a settings
response — the interface shows only whether one is set.

## Out of scope, by design

- Any write path — see the "read-only, structural" note above.
- NCM script execution, and a later device-communication bridge for pushing
  changes directly. Both stay out of this plugin on purpose.
- A raw SWQL escape hatch. The entire design exists to not need one.

## Where things live

```
helpers/client.py         the one HTTP call; no method reaches /Invoke/
helpers/capabilities.py   every SWQL template; the only place SWQL is built
helpers/policy.py         per-conversation rate limit
helpers/config.py         settings resolution; the password is not here
tools/solarwinds_lookup.py the tool; routes every exception to a clear message
api/solarwinds_config.py  Settings card get/save
skills/solarwinds-troubleshooting/SKILL.md  which capability answers what
```
