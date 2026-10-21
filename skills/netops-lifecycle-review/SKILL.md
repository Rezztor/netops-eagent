---
name: netops-lifecycle-review
description: "Review end-of-life, warranty, support and software versions across Inventory, with sourced vendor research. Use for audits, upgrade planning, \"what is going out of support\" or \"are we on a safe version\"."
triggers:
  - "end of life"
  - "end of support"
  - "warranty expiring"
  - "software versions"
  - "which devices need upgrading"
  - "lifecycle review"
---

# NetOps lifecycle and version review

Read and research only. You report; the engineer decides, edits dates and plans upgrades. Inventory dates are what people recorded: say so, and treat a missing date as unknown, not as fine.

1. **Scope:** all devices, one site, one vendor or one role. Ask if unclear.
2. **Dates.** `inventory_lookup` with `expiring_within_days` (start at 90, then 180) and `expiring_kind` (`warranty`, `support` or `eol`). Results are capped at 25 with the total: narrow by `site`, `vendor` or `role` and repeat until you have them all. Past dates are expired.
3. **Gaps.** Count active devices with no end-of-life, warranty or support date (filter by vendor, then compare with the total). Report the number; do not fill dates in. The Inventory Health tab lists them too.
4. **Versions.** Group by vendor, platform, model and `software_version` from the lookup. Many versions of one model means drift. The recorded version can be stale: for the few devices that matter, confirm with one approved read (table below) and quote the output.
5. **Research.** Hand the distinct model and version pairs to `network-researcher` (`call_subordinate`) or use `search_engine`: vendor end-of-sale, end-of-software-maintenance and last-date-of-support notices, security advisories, the vendor's recommended release. Ask for title, URL, version and date for each claim, and for conflicts or old pages to be flagged. A researcher cannot say a given device is affected: say which version or feature must be read from it, then read it.
6. **Rank.** Expired first, then ending within 90 days. Within each: firewalls, core and edge before access; devices with no HA partner before pairs.
7. **Report** a table: device, site, role, what ends, date, days left, running version, source, suggested action (renew, replace, upgrade plan, confirm version). Say what you could not verify.

| Version read | ios | nxos | eos | junos | sonic_community |
|---|---|---|---|---|---|
| Running version | `show version` | `show version` | `show version` | `show version` | `show version` |
| Modules and parts | `show inventory` | `show inventory` | `show inventory` | `show chassis hardware` | `show platform summary` |

Good: the dates and the running version come from named sources and agree. Bad: a recorded version that the device contradicts, an advisory that lists the version but no fix or workaround, an end-of-support date with no source. Report each as a finding for the engineer.

A real upgrade or replacement is a project: hand it back with the devices, the target release and the risks. A config change goes through netops-change-checklist or netops-change-runbook.

Never: guess that a device is or is not affected, quote a date without a source, claim compliance with a standard, edit Inventory, or recommend a release you have not seen named by the vendor.
