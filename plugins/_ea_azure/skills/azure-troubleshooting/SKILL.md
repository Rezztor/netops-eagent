---
name: azure-troubleshooting
description: "Start an Azure fault from the chat: a VM that cannot reach another, DNS, a peering, a web app down, a firewall drop, on-prem reach, or who changed a resource. Use for \"cannot reach\", \"Azure down\", \"who changed this\"."
triggers:
  - "azure down"
  - "cannot reach in azure"
  - "who changed this resource"
---

# Azure troubleshooting

Read-only. Ask what fails, for whom, since when, and the two ends (names, not a guess). Load one specialist skill and stop there.

| The fault | Skill |
| --- | --- |
| VM or subnet cannot reach an address | azure-vm-reachability |
| A name does not resolve | azure-dns |
| A peering is down or passes nothing | azure-peering |
| A web app is down or slow | azure-webapp-down |
| Who changed this, and when | azure-who-changed |
| Azure Firewall dropped the flow | azure-firewall-drop |
| Azure subnet cannot reach on-premises | azure-onprem-reach |

For a service-specific cause (App Service, Functions, Container Apps, AKS, storage, Event Hubs or Service Bus), also load `azure-microsoft-guidance` and read Microsoft's file for that service; its translation rules apply.

`azure_inventory` is the stored picture (say its as-of line). `azure_read` is one named read from `reads.yaml` on a stored resource. A diagnostics read (effective routes, IP flow verify, next hop, connection check, gateway BGP) needs the Rezztor Diagnostics role; a 403 means that role is missing, not that the path is blocked. Network Watcher checks: first 1,000 a month free, then 0.001 USD each. Say so before the first one.

A fix that deletes, stops, deallocates, restarts, resizes, scales in or swaps anything is a suggestion with a runbook for the engineer. Never a tool call. An add or a widen still needs a change document and a CR or SR number on the approval card before anything runs, and you do not run it.

Never: Run Command, Kudu, log stream, app settings, a VPN shared key, packet capture, or a person's sign-in name the tool withheld.

Azure is reached only through the `azure_*` tools, under the product's own read-only identity. Never install a cloud CLI (az, azd, the Az PowerShell module), never run `az login` or `Connect-AzAccount`, never start a sign-in and never ask the engineer to sign in or to enter a device code. If a tool is missing a value such as a subscription, ask for that value: the azure_* tools take a subscription by its name or its GUID from the stored inventory (azure_inventory names both).
