---
name: azure-who-changed
description: "Say who changed an Azure resource and when, from the stored change records and the activity log. Use for \"who changed this\", \"what changed yesterday\", \"who deleted that\"."
triggers:
  - "who changed this"
  - "what changed yesterday"
  - "who deleted that"
---

# Azure who changed

Read-only. Ask which resource, and the window they care about.

1. `azure_inventory` on the resource. `changes` is this product's own diff (at, change, fields). `azure_says.azure_change_records` is Azure's own 14-day record from the last pull (time, change type, operation, changed paths). A user is "a user (the name is shown on the Inventory screen)". Point the engineer there. Do not ask for the name and do not guess it. An application id stays as the tool returned it. Say the pull time.
2. `azure_read` `activity_log` on the same resource. The catalogue allows at most 7 days (`P7D`). Quote `time`, `who`, `operation` and `status`, and say the window. Do not ask for a longer window than the tool accepts.
3. Older than that, a workspace export is not in the catalogue. Say so. Do not invent a query.
4. Answer with the time, the caller the tool actually returned (`who`, or the withheld user line), the operation, and which source each line came from (our diff, stored record, or activity log).

Nothing in this skill changes the resource. Reverting, deleting or restoring is a suggestion with a runbook for the engineer, never a tool call. Say what the records show and stop.
