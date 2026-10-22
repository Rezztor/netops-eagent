# Bundled Skills DOX

## Purpose

- Own bundled Rezztor EAgent skills and their agent-facing instructions.
- Keep skill workflows accurate, composable, and safe for runtime loading.

## Ownership

- Each direct skill directory owns its `SKILL.md` and any local supporting files.
- Plugin-distributed skills belong under the relevant plugin directory.
- User-local skills belong under `usr/skills/`.

## Local Contracts

- Every skill directory must include a `SKILL.md`.
- Skill instructions must be operational and scoped to the skill's purpose.
- Do not include secrets, private user data, or environment-specific credentials.
- Supporting files referenced by a skill must exist relative to that skill directory.
- `eagent-development` and the five `eagent-*-plugin*` skills are not in the image a customer gets (`.dockerignore`, 2026-10-12). No other skill may point at one of them; `tests/test_delivery_hygiene.py` checks.

## Work Guidance

- Keep skills focused on repeatable workflows that agents should actively follow.
- Prefer updating an existing skill over creating overlapping skill variants.
- When a skill refers to repository paths, commands, or plugin architecture, keep those references current with source and docs.

## Verification

- Run skill runtime/import tests after changing skill loading assumptions or skill format.
- Manually read changed `SKILL.md` files for broken relative references.

## Child DOX Index

Direct child DOX files:

| Child | Scope |
| --- | --- |
| [eagent-create-agent/AGENTS.md](eagent-create-agent/AGENTS.md) | Creating Rezztor EAgent agent profiles. |
| [eagent-create-plugin/AGENTS.md](eagent-create-plugin/AGENTS.md) | Creating or extending Rezztor EAgent plugins. |
| [eagent-debug-plugin/AGENTS.md](eagent-debug-plugin/AGENTS.md) | Diagnosing plugin loading, API, frontend, and extension issues. |
| [eagent-development/AGENTS.md](eagent-development/AGENTS.md) | Broad Rezztor EAgent framework development guidance. |
| [eagent-manage-plugin/AGENTS.md](eagent-manage-plugin/AGENTS.md) | Plugin install, update, scan, enable, disable, and removal workflows. |
| [eagent-plugin-router/AGENTS.md](eagent-plugin-router/AGENTS.md) | Routing plugin-related user requests to specialist skills. |
| [eagent-review-plugin/AGENTS.md](eagent-review-plugin/AGENTS.md) | Full plugin audit workflow and checklists. |
| [build-skill/AGENTS.md](build-skill/AGENTS.md) | Building and improving Rezztor EAgent skills. |
| [scheduled-tasks/AGENTS.md](scheduled-tasks/AGENTS.md) | Managing scheduled, planned, and adhoc tasks. |
| [netops-change-checklist/AGENTS.md](netops-change-checklist/AGENTS.md) | Drafting a network change for engineer approval. |
| [netops-inventory-import/AGENTS.md](netops-inventory-import/AGENTS.md) | Staging inventory devices from an attached file. |
| [netops-troubleshooting/AGENTS.md](netops-troubleshooting/AGENTS.md) | Read-only network fault triage workflow. |
| [netops-alert-triage/AGENTS.md](netops-alert-triage/AGENTS.md) | SolarWinds alert triage: group, find the root, classify. |
| [netops-change-runbook/AGENTS.md](netops-change-runbook/AGENTS.md) | Planning a multi-step change as one `net_plan` runbook with snapshots. |
| [netops-config-drift/AGENTS.md](netops-config-drift/AGENTS.md) | Backup coverage and configuration drift review. |
| [netops-core-services/AGENTS.md](netops-core-services/AGENTS.md) | Read-only NTP, DHCP and DNS checks from the network side. |
| [netops-device-health/AGENTS.md](netops-device-health/AGENTS.md) | Read-only CPU, memory, environment and log review of one device. |
| [netops-device-onboarding/AGENTS.md](netops-device-onboarding/AGENTS.md) | Bringing one device into Inventory so it can be read. |
| [netops-find-endpoint/AGENTS.md](netops-find-endpoint/AGENTS.md) | Locating the switch port of a host by IP or MAC. |
| [netops-firewall-path/AGENTS.md](netops-firewall-path/AGENTS.md) | Read-only firewall policy, ACL and NAT path analysis. |
| [netops-incident-notes/AGENTS.md](netops-incident-notes/AGENTS.md) | Incident notes, shift handover and blameless post-incident review. |
| [netops-interface-health/AGENTS.md](netops-interface-health/AGENTS.md) | Read-only diagnosis of a down, erroring or flapping interface. |
| [netops-lifecycle-review/AGENTS.md](netops-lifecycle-review/AGENTS.md) | End-of-life, support and software version review with sourced research. |
| [netops-loss-latency-mtu/AGENTS.md](netops-loss-latency-mtu/AGENTS.md) | Read-only search for packet loss, latency and MTU faults. |
| [netops-routing-reachability/AGENTS.md](netops-routing-reachability/AGENTS.md) | Read-only layer 3 reachability: route lookup, static, OSPF and BGP. |
| [netops-spanning-tree/AGENTS.md](netops-spanning-tree/AGENTS.md) | Read-only spanning tree and loop checks. |
| [netops-vlan-trunk/AGENTS.md](netops-vlan-trunk/AGENTS.md) | Read-only diagnosis of VLAN and trunk problems. |
| [netops-vpn-tunnel/AGENTS.md](netops-vpn-tunnel/AGENTS.md) | Read-only IPsec tunnel troubleshooting. |
