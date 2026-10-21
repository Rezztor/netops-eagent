---
name: azure-inventory
description: Answer questions about what exists in the Azure tenant (resources, counts, subnets, route tables, app services, what changed) from the stored inventory with the azure_inventory tool. Use when the user asks how many, which, where, whether something is configured, or what changed in Azure.
---

# Azure inventory

Domain knowledge for `azure_inventory`, not a restatement of its arguments (the tool's own prompt has those). It reads a stored snapshot, never Azure itself, and cannot change anything.

## Rules for every answer

- Say the as-of date the answer starts with, and name any subscription it says was not read. Offer `azure_pull` for fresh data; call it only when the user asks.
- "Configured" is what a person set up: which route table is attached to a subnet, which routes it holds. The routes Azure actually applies (built-in, learned from a gateway) are not stored. Say "configured" and name that gap.
- Counts are exact; a list is capped at 25. Count first when the question is "how many".

## Which arguments answer which question

| The user asks | Call |
| --- | --- |
| How many app services in this subscription and resource group? | `type: app service`, `subscription`, `resource_group`, `count_by: state` (the list shows web apps and function apps by label) |
| Which of them are stopped? | the same, `state: stopped` |
| What is the VNet? What is the subnet? | `type: virtual network` lists them; `resource: <vnet name>` for its address space, subnets and peerings |
| What route is configured on this subnet? Is it configured or not? | `resource: <vnet or subnet name>`: each subnet carries `route_table` with its routes, or `route_table: null` when there is none |
| Which subnets have no route table? | `type: subnet`, `without: route_table` (GatewaySubnet and AzureFirewallSubnet normally have none) |
| What is stopped or failed? | `state: stopped`, then `state: failed` |
| What has no owner tag? | `without: tags`, or `tag: owner` for the ones that have it |
| What changed since last week or the last pull? | `changed_since: last_pull` with `count_by: category`, then the list |
| What is in this resource group, by kind? | `resource_group`, `count_by: category` or `type` |
| Tell me about this one resource | `resource: <name or Azure ID>`; if the name is ambiguous the answer lists candidates: ask which |
| Is anything open to the internet? | `resource` pages show `exposure` reasons; it is a pointer to look, not a verdict |

If a resource is not found, try `query` with part of its name before concluding it does not exist, and check `include_removed: yes` for one that was deleted.
