---
name: azure-onprem-reach
description: "Find why an Azure subnet cannot reach on-premises. Gateway, routes and peering flags first. Use for \"cannot reach on-prem\", \"vpn tunnel down\", \"subnet cannot reach on-prem\"."
triggers:
  - "cannot reach on-prem"
  - "vpn tunnel down"
  - "subnet cannot reach on-prem"
---

# Azure on-prem reach

Read-only. Ask which subnet, an on-prem prefix or IP, and a VM in the subnet if they have one.

1. `azure_inventory` for the subnet route table, the virtual network, the peering to the hub, the gateway and the connection. Say the as-of line.
2. `azure_read` `vnet_peerings` on the spoke and the hub. The spoke needs `use_remote_gateways`. The hub needs `allow_gateway_transit`. A flag the read did not return is unknown, not false. `vpn_connection` for `status`, `ingress_bytes` and `egress_bytes`. `vpn_gateway` for type and BGP ASN.
3. `effective_routes` on a NIC in the subnet. The on-prem prefix should be there with source VirtualNetworkGateway. A route with source User covering that prefix overrides it. BGP propagation is not in this read. If the prefix is missing and no user route explains it, say so and hand that question to the engineer.
4. `vpn_bgp_peers`, `vpn_learned_routes`, and `vpn_advertised_routes` (needs the on-prem peer IP). A 403 means the Rezztor Diagnostics role is missing, not that the tunnel is down. Without it, `vpn_gateway_metrics` for `BgpPeerStatus`, `BgpRoutesLearned`, `TunnelIngressBytes` and `TunnelEgressBytes`. Those gateway reads document no charge.
5. `next_hop` from a VM in the subnet to an on-prem IP, and `ip_flow_verify` if a security group may be the drop. Both are Network Watcher checks: first 1,000 a month free, then 0.001 USD. Say so before the first one.
6. TunnelDiagnosticLog, IKEDiagnosticLog and RouteDiagnosticLog: `azure_read` with a free-form `kql` and a `workspace`, over `AzureDiagnostics` (`| where Category in ("TunnelDiagnosticLog", "IKEDiagnosticLog", "RouteDiagnosticLog")`), `timespan` `PT1H`. The query is always shown on a card and the engineer approves it; if the gateway logs to a resource-specific table the settings do not allow, or no workspace is set up, say so and ask the engineer to paste the last hour only then. Never ask for the VPN shared key.
7. Conclude: tunnel down, route not learned, route not propagated, peering flag, security group, or the on-prem side. Hand the on-prem side back with the evidence.

Restarting or deleting the gateway or a connection is a suggestion with a runbook. You never stop, restart or delete it. Adding a route or a connection is a change card with a CR or SR number, not a call you make.
