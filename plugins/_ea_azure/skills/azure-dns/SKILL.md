---
name: azure-dns
description: "Find why a name does not resolve in Azure: private DNS zone, VNet link, private endpoint record. Use for \"DNS does not resolve\", \"private endpoint name\", \"wrong IP for the name\"."
triggers:
  - "dns does not resolve"
  - "private endpoint dns"
  - "name does not resolve"
---

# Azure DNS

Read-only. Ask for the name, the client VM or VNet, and the answer they expected.

1. `azure_inventory` for the client VNet (`dns_servers`: custom, or empty meaning Azure DNS), the private zone, and the private endpoint.
2. `azure_read` `private_dns_links` (vnet, registration, state) and `private_dns_records` (the zone's A, AAAA and CNAME rows; find the name). `private_endpoint` for `dns` (fqdn and addresses) and connection status.
3. Compare, and say which check failed: name in the right `privatelink` zone; zone linked to the client VNet; A record equal to the endpoint NIC address; custom DNS forwarding to 168.63.129.16.
4. No read resolves a name from inside the VNet. Ask the engineer for `nslookup` from the VM and label it their evidence.
5. A DNS security-policy or firewall DNS-proxy log is not in the catalogue. Ask them to paste those rows if they have them. Do not invent a query tool.

Conclude: missing link, missing or wrong record, forwarding gap, or a public answer.

Creating a link or a record is a change card with a CR or SR number. Deleting a zone, a link or a record is a suggestion with a runbook, never a tool call.
