---
name: ansible-network-guides
description: Troubleshooting network devices and firewalls with net_read, and drafting changes with net_change. Use when the user asks about switches, routers, firewalls, interfaces, VLANs, routing, connectivity, or a configuration change to network equipment.
---

# Network troubleshooting and change

Domain knowledge for `net_read`, `net_change` and `net_apply`. This maps symptoms
to capabilities and explains how to be useful when you cannot push a change —
it is not a restatement of the argument lists.

## Start from the symptom

- **"What is this device / is it up?"** → `reads/device_facts`. Follow with
  `reads/interfaces` on a switch or router.
- **"An interface is down / erroring"** → `reads/interfaces`, passing the
  `interface` variable to narrow it.
- **"A subnet is unreachable"** → read the routing state on the device nearest
  the source first, then the next hop. Two devices' views of the same route
  disagreeing is usually the answer.
- **"What changed?"** → read the running configuration and say what you can see.
  You have no configuration history; SolarWinds NCM has it, if this instance has
  that plugin.

Every read is a request an engineer approves on a card in the chat (see the
next section). Do it before theorising: one `device_facts` beats three sentences
of speculation about what the device might be.

## How a read works

1. `net_read` (action `request`) checks the device is an **active Inventory
   device**, pre-checks it (credential stored, reachable, connection matches the
   platform) and shows a card with the exact commands. Nothing has run yet.
   **Stop and wait**: no other tool, no questions.
2. The engineer answers Approve or Reject on the card. On Approve, call
   `net_read` with `action=run` and the `change_id`. On Reject, or if the card
   expired (15 minutes), stop and ask whether to request it again.
3. If the request is refused, the reason is plain: not in Inventory (offer to
   stage it with `inventory_stage`), no credential, unreachable, wrong platform.
   Pass it on. A missing credential is collected with `net_credential` (a secure
   form; `net_read` shows it for you), **never in chat**. Do not ask for one before
   checking the device has one, and never read a device outside this flow.
4. A failed run comes back with a code (`connection_failed`, `credential_failed`,
   `host_unreachable`, `platform_mismatch`, `command_rejected`, `timeout`,
   `unknown`) and one line of cause. Report that, not the raw Ansible output, and
   do not blame credentials unless the code says `credential_failed`.

## When the user wants a change

Draft it with `net_change`. That runs the catalogue entry in check mode and
returns **the exact commands the device would receive**, which is what an
engineer then approves.

Three things to be straight about:

**You cannot approve it, including your own draft.** Say so plainly: the
requesting engineer reviews the commands and approves on the card in the chat. Do not
look for another route, and do not imply one exists.

**A drafted change is not a made change.** Until someone approves it and
`net_apply` runs, nothing has happened on any device. Do not report a draft as
though the work is done.

**If the plan says the devices already match, nothing is drafted.** That is a
real answer and a good one — tell the user the state they wanted is the state
that exists.

## When no catalogue entry fits

Say what is missing and stop. The catalogue is reviewed code in this repository;
a new entry is a pull request a person merges, not something you can improvise,
and there is deliberately no argument anywhere in these tools through which a
playbook or a raw command can be supplied.

What you *can* do is write the change up: which devices, what the CLI would be,
how to verify it, how to roll it back. That is a proposal for a human, and it is
genuinely useful. Be clear that it is a proposal.

## Reading the commands you show a person

`net_change` reports how the commands were produced, and the two are not equally
trustworthy:

- **from check mode** — computed against the devices as they are now. This is the
  good case.
- **reviewed CLI** — literal text a person wrote in advance, used where the
  module cannot report what it would do. It does not reflect the devices' current
  state, and you should say so when you show it.

## What you must never do

- Suggest SSHing to a device, or offer to run a command through
  `code_execution_tool` instead. The runner cannot reach the management network
  and that is deliberate.
- Retry a refused `net_apply`. Read the refusal, explain it, stop. Each one names
  what was wrong: not approved, or the plan no longer
  matching what was approved.
- Describe a superseded change as a failure. It means the devices moved after
  approval, so what would run is no longer what anyone read. The fix is to draft
  it again and have it approved again.
