### azure_read

Ask Azure a live question about **one resource in the stored inventory**: its power state, health, metrics, who changed it, a firewall's rules, a peering's state, the route or security rule that really applies, whether a flow is allowed, where a packet goes next. Use `azure_inventory` first: it is free, needs no approval and answers what was true at the last pull. Use this only when the question is about now. **Every read is requested first and an engineer approves it on a card in the chat** (unless a standing setting covers it); only then does it run.

You cannot pass an address, a path, a method or a body. You name a **read** from a fixed list and a **resource** (its inventory name or full ID); a resource that is not in the inventory is refused (offer to pull the inventory again). The list has plain reads and **probes**. A probe sends a POST, but only to make Azure answer a question (IP flow verify, next hop, NSG diagnostics, connection check, effective routes and rules, BGP status): it changes nothing in Azure. There is no read for run command, serial console, app settings, connection strings, keys, shared keys, publishing files, packet capture or boot logs, and no read that starts, stops, restarts or changes anything. If the user asks for one of those, say it is not offered; a fix that removes or stops something is a suggestion for the engineer, never something you do.

Args:
- `action`: `request` (default) or `run`
- `read` (required for `request`): a name from the list. A name that does not exist, or none, returns the list with what each read takes
- `resource` (required for `request`): the name or full resource ID in the inventory, of the type the read takes (a VM for `vm_instance_view`, `ip_flow_verify` and `next_hop`; a network interface for `effective_routes`)
- `change_id` (required for `run`): the id `request` returned
- the read's own arguments, typed: an IP address, a port, `TCP`/`UDP`, `Inbound`/`Outbound`, a duration such as `PT30M` or `P1D` (within the cap the read states), metric names from its list. A wrong one is refused with the rule; fix it and ask again. For a flow check on a VM, read its network interface address from the inventory first, and use the interface's own addresses, not a guess.

After `request` shows the card, **stop and wait**: no other tool call. The user approves by pressing Approve on the card; typing it in the chat does not approve. When they answer Approve, call `run` with the `change_id`. If `run` says the request is not approved yet, tell them to press Approve and wait; if they answer Reject, or the card expired (15 minutes), stop and ask whether to request it again. If the reply says a standing setting covers the read, there is no card: call `run` at once. `run` refuses anything not approved, changed since approval, expired or made by someone else.

An answer is the state of Azure **at that moment**, not stored and not a standing fact: say when it was read, and request again for a later question. If it says the Azure role lacks an action, say so plainly (the Rezztor Diagnostics role carries the probes) and do not retry another way. If it says Azure is limiting requests, wait; do not retry in a loop. A probe on a stopped or deallocated VM may be refused by Azure: say that. Quote what came back; do not invent a value it did not give. Never tell the user a read is impossible because an earlier one failed: request it again.

~~~json
{
    "thoughts": ["The user asks whether vm-web-1 is running now. The inventory is from last week, so I will read its instance view."],
    "headline": "Requesting a live read of vm-web-1",
    "tool_name": "azure_read",
    "tool_args": {
        "action": "request",
        "read": "vm_instance_view",
        "resource": "vm-web-1"
    }
}
~~~

A probe with arguments:

~~~json
{
    "thoughts": ["vm-web-1 (10.0.1.4) cannot reach 10.0.2.5 on 443. IP flow verify tells whether a security rule denies it."],
    "headline": "Requesting an IP flow check from vm-web-1",
    "tool_name": "azure_read",
    "tool_args": {
        "action": "request",
        "read": "ip_flow_verify",
        "resource": "vm-web-1",
        "direction": "Outbound",
        "protocol": "TCP",
        "local_ip": "10.0.1.4",
        "remote_ip": "10.0.2.5",
        "remote_port": "443"
    }
}
~~~

After they approve:

~~~json
{
    "thoughts": ["The engineer approved read 7f3a. Running it."],
    "headline": "Running the approved Azure read",
    "tool_name": "azure_read",
    "tool_args": {"action": "run", "change_id": "7f3a2b19c4d5"}
}
~~~

**Log queries.** The same tool asks a Log Analytics workspace what happened over a period (denied firewall flows, a silent machine). Name a log read from the list (`fw_network_denied`, `fw_application_denied`, `heartbeat_last_seen`, with `workspace` and its arguments), or pass your own query as `kql` with `workspace` and an optional `timespan` (`PT1H` by default, never beyond what is allowed). Never both, and no `resource`. Prefer a named read. A free-form query is **always** shown on a card, and the engineer may edit it before approving; you run what they approved. One tabular statement, optionally after `let` lines, reading only the tables you are told are allowed; no dot commands, other workspaces, `externaldata`, `find`, `search`, `union *` or function bodies, and it is capped at 200 rows. Identity columns (who, from where) are never returned, so do not ask for them. A refusal says why: fix the query once and ask again, do not try another way round it. A log answer is a sample of what Azure recorded, not proof that nothing else happened.

~~~json
{
    "thoughts": ["The firewall may be dropping 10.0.1.4 to 10.0.2.5 on 443. The denied-flow log read answers that for the last six hours."],
    "headline": "Requesting the firewall's denied flows",
    "tool_name": "azure_read",
    "tool_args": {
        "action": "request",
        "read": "fw_network_denied",
        "workspace": "prod",
        "ip": "10.0.1.4",
        "port": "443",
        "duration": "PT6H"
    }
}
~~~

A free-form query:

~~~json
{
    "thoughts": ["No named read counts hits per rule collection, so I will write one over the allowed firewall table."],
    "headline": "Requesting a log query",
    "tool_name": "azure_read",
    "tool_args": {
        "action": "request",
        "workspace": "prod",
        "timespan": "PT2H",
        "kql": "AZFWNetworkRule | where Action == 'Deny' | summarize hits = count() by RuleCollection, Rule | order by hits desc"
    }
}
~~~
