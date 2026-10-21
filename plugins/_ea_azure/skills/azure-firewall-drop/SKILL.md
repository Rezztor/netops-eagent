---
name: azure-firewall-drop
description: "Find why Azure Firewall dropped traffic. Inventory and rule groups first, then the firewall log reads. Use for \"firewall dropped\", \"firewall deny\", \"no rule matched\"."
triggers:
  - "firewall dropped"
  - "firewall deny"
  - "no rule matched"
---

# Azure Firewall drop

Read-only. Ask for the source, the destination, the port and the protocol. Ask if any is missing.

1. `azure_inventory` for the firewall, its policy, subnet and public IPs, and route tables that send `0.0.0.0/0` or the destination to its private IP. Say the as-of line.
2. `azure_read` `firewall_get`, then `firewall_policy` and `firewall_rule_groups` on the policy. Azure evaluates DNAT, then network, then application, each by priority, and a match terminates. A flag the read did not return is unknown, not false.
3. `diagnostic_settings` on the firewall. No workspace: say so and stop the log part. Adding a setting is a change card with a CR or SR number. You do not add it.
4. Deny rows: `azure_read` `fw_network_denied` (with `ip`, `port`, `duration`) and `fw_application_denied` (with `ip`), each with the `workspace` from the settings. Each is approved on a card like any read. They count the denies by source, destination, port and rule; `ActionReason` "Default Action" means no rule matched. A name that is not set up as a workspace, a table the settings do not allow, or no log app key is a plain refusal: say it, and do not try another way. The reads cover the structured firewall tables only; if the firewall logs to the older `AzureDiagnostics` table, a free-form `kql` over it is allowed (the engineer reads and may edit it on the card). Ask the engineer to paste rows only for a log no read covers.
5. No rows: say the Network Watcher price first (first 1,000 checks a month free, then 0.001 USD), then `next_hop` from the source VM. Then `firewall_metrics` for `NetworkRuleHit`, `FirewallHealth` and `SNATPortUtilization`. `ip_flow_verify` does not see the firewall.
6. Conclude with the rule (group, collection, rule) or "no rule matched", and the read or the pasted lines it came from.

A fix that removes, narrows or disables a rule is a suggestion with a runbook. The engineer does it in the portal. Adding a rule, or a source, destination, port or FQDN, is a change card with a CR or SR number. You do not run either.

Never delete a rule, and never stop or deallocate the firewall. Those are suggestions, never a tool call. Never packet capture or a shared key.
