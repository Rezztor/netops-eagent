---
name: azure-webapp-down
description: "Find why an Azure web app is down or slow, from site state, health, metrics and detectors. Use for \"web app down\", \"app service 500\", \"site slow\"."
triggers:
  - "web app down"
  - "app service 500"
  - "site slow"
---

# Azure web app down

Read-only. Ask which site, since when, and whether it is every request or one path.

1. `azure_inventory` for the site, its plan, VNet integration and private endpoint. Say the as-of line.
2. `azure_read` `site_state`, `resource_health`, and `service_health` for the region.
3. `site_metrics` for the last hour (`Http5xx`, `HttpResponseTime`, `Requests`) and `plan_metrics` (`CpuPercentage`, `MemoryPercentage`). The catalogue keeps at most one day.
4. `site_detectors` for availability and performance names. Do not run a detector execute call; it is not a catalogue read.
5. `activity_log` on the site and the plan (at most 7 days, `P7D`): restart, deploy, scale, setting change. Quote `time`, `who`, `operation` and `status`. A person's sign-in name the tool withheld stays withheld.
6. HTTP and console logs are not in the catalogue. Ask the engineer to paste redacted rows. Never Kudu, log stream, or app settings.
7. Conclude: platform, plan saturation, an application error, or a recent change, with the read that showed it.

Restarting the site, scaling in, or swapping a slot is a suggestion with a runbook. Scaling out is a change card with a CR or SR number. You never stop, restart, scale in or swap it.
