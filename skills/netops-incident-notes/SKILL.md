---
name: netops-incident-notes
description: "Keep incident notes, write a shift handover, or draft a blameless post-incident review from what the chat actually shows. Use when an outage is running, a shift ends, or an incident needs a write-up."
triggers:
  - "incident notes"
  - "shift handover"
  - "hand over to the next shift"
  - "post-incident review"
  - "write up the outage"
  - "timeline of the incident"
---

# NetOps incident notes, handover and review

Writing only: no device is touched. Everything goes in the chat for the engineer to copy into the ticket; never say a ticket was filed. Take facts from tool results in this conversation (name the tool and the `change_id` of the card). Mark anything else as **assumption**. Do not invent times, names or causes.

## Severity (say it, with the reason)

Impact times urgency. Many users or a critical site down now: high. One user or a spare path: low. When unsure, treat it as the higher one and say so. A high-severity incident should have a named owner; ask who.

## Mode 1: notes while it is running

Reply with a short block each time something changes:

- **Now:** symptom, scope, since when (device local time or UTC, say which).
- **Timeline:** one line per event: time, what, evidence.
- **Theories:** each one marked open, ruled out (by which read) or confirmed.
- **Done / next:** reads run, changes approved, the next safe read.
- **Owner and ask:** who owns it, what you need from the engineer.

Mitigate before you diagnose when a safe, reviewed step exists (a rollback card). Do not mix diagnosis and fix: a fix goes through netops-change-checklist.

## Mode 2: shift handover

Fields, in this order; write "none" rather than skip one:

1. Open incidents: severity, last action, owner, what is waiting on whom.
2. Pending cards: reads, changes or plans not yet approved, and when they expire (reads 15 minutes, changes 30).
3. Changes made this shift and their verify result; anything rolled back.
4. Alerts: acknowledged, suppressed (with the end time), flapping.
5. Freezes and maintenance windows coming up.
6. Watch items: what could come back and what to look at first.
7. Vendor or support cases open, with their numbers.
8. Ask the incoming engineer to confirm they have read it. Do not call it handed over until they do.

## Mode 3: post-incident review

Blameless: describe what happened and what the evidence showed, not who should have known. Never write "human error" as a cause.

1. Summary and impact (who, what, how long).
2. Timeline with evidence per line.
3. How it was found (monitoring or a person) and how long to detect, to act, to restore.
4. Cause and contributing factors, with confidence and what would change your mind.
5. What worked, what slowed us down.
6. Actions: each with an owner and a due date to be set by the engineer; you propose, they assign.
7. Trigger check: a rollback, a manual find or a long outage deserves a review; say if this one does.

Never: blame a person, state a cause without evidence, file or send anything, copy credentials into notes.
