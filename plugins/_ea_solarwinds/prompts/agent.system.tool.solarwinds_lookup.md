### solarwinds_lookup
Read live data from SolarWinds. Read-only — this tool cannot change anything
in SolarWinds, and cannot reach any instance other than the one configured for
this deployment.

Args:
- `capability`: one of the names below
- the arguments that capability needs, as its own named args (not nested)

If a capability's module isn't enabled on this SolarWinds instance, the tool
says so plainly — that is not an error to retry or work around, it means the
feature genuinely isn't available here.

Capabilities:
- `node_status` (`node`) — is this device up? vendor, IP, last response time
- `node_search` (`fragment`) — which nodes match a name/IP/vendor fragment
- `node_inventory` (`node`) — full inventory for a device
- `active_alerts` (`node` optional) — what's currently alerting, and how severely
- `recent_events` (`node`) — what happened on this node recently
- `interface_status` (`node`) — are this device's interfaces up, utilisation
- `volume_usage` (`node`) — how full are this device's disks/volumes
- `application_status` (`node`) — are this device's monitored (SAM) applications healthy
- `hardware_health` (`node`, `category` optional) — CPU, fan, temperature, power supply, voltage
- `device_config` (`node`, `before` optional) — newest archived configs, at most 5 per call, each cut to 200 lines; pass `before=<DownloadTime of the last one>` for older ones; never a live pull
- `netflow_top_talkers` (`node`) — which IP pairs move the most traffic through this device
- `netflow_by_ip` (`ip`) — what is this IP sending or receiving
- `device_neighbors` (`node`) — what is this device directly connected to, and on which port
- `device_port_location` (`identifier` — a MAC or an IP) — which switch/port it's plugged into
- `ip_lookup` (`ip`) — is this IP in use, which subnet/VLAN

Name a device by whatever you have — hostname fragment, full name, or IP; most
capabilities match loosely. There is no capability that takes a raw query: if
none of the above answers the question, say so rather than trying to invent
one.

~~~json
{
    "thoughts": ["The user wants to know if core-switch-1 is up."],
    "headline": "Checking core-switch-1 in SolarWinds",
    "tool_name": "solarwinds_lookup",
    "tool_args": {
        "capability": "node_status",
        "node": "core-switch-1"
    }
}
~~~
