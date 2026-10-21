# Auto Skills

Turns a completed task into a reusable skill, for the person who did the work.

When someone finishes a substantial task, a background pass over the transcript asks
whether it taught a repeatable procedure. If it did, the result is written as a
`SKILL.md` in that user's own skills directory, where their agent can load it next
time. Everyone else's agent is unaffected.

Making a skill available to everyone is a separate, deliberate step: an administrator
reads it on the Skills screen and promotes it into `usr/skills`.

## Why the two tiers

`usr/skills` is instance-wide. Every skill in it is listed in every account's system
prompt, and its body is loaded into the conversation on demand. Writing to it
automatically, from content a model produced out of one person's conversation, would
be a way to put instructions in front of other people's agents — the same exposure
`helpers/api_policy.py` already gates knowledge import for.

So induced skills go somewhere private by default. `usr/users/<id>/skills/` sits
beside the chats, memory and workdir that are already per-user, and goes with the
account when it is deleted.

## What it will not do

- Write a skill from a task that failed, or that the agent never finished.
- Write one from a task shorter than `min_tool_calls` tool calls.
- Revise a skill a user wrote themselves; it only ever edits what it created,
  tracked in a per-user ledger.
- Write a skill containing a value registered as a secret, or one whose name is not
  a valid skill name, or one that overlaps a skill that already exists.
- Grow a user's library past `max_personal_skills`. The catalog is rebuilt into
  every system prompt, so an unbounded library is a per-turn cost.
- Generate or execute scripts. A skill here is instructions.

Any of those is a discard, not a repair: a skill that had to be corrected to be safe
is one nobody reviewed.

## Removing one

A user can remove any skill this plugin wrote for them, from the Skills screen —
the row is marked `Personal` and carries a `Forget` button. That needs no
administrator: the skill is in their own directory and nobody else's agent reads
it, and it was applied without being asked for, so undoing it is theirs to do.

Forgetting also withdraws any proposal still waiting in the promotion queue. The
queue holds its own copy, taken at capture time, so without that step a user
could discard a skill and an administrator could still publish it to the whole
instance afterwards.

Once a skill has been promoted it belongs to the instance. Removing it from
`usr/skills` is an administrator's decision, through the Skills screen's own
delete control.

## Settings

On the Skills screen, for administrators. `mode` is the one that matters:

| mode | Effect |
|---|---|
| `personal` | Write the skill for the user who did the work, and queue it for review |
| `review` | Queue it for review only; write nothing |
| `off` | Capture nothing — the shipped default |

It ships `off` on purpose. Both other modes spend a utility-model call on every
qualifying turn, and `personal` adds to what the agent reads on every turn after
that, so it is a cost an operator should take on deliberately rather than
inherit from an upgrade.

The size ceilings and the review timeout are in `default_config.yaml` and not exposed
in the interface. They are safety limits rather than preferences.

## Where things live

```
usr/users/<id>/skills/<name>/SKILL.md        the user's own induced skills
usr/users/<id>/skills/<name>/.versions/      previous revisions, hidden from discovery
usr/users/<id>/.auto_skills/ledger.json      what this plugin created, from what, and
                                             how often its author has since loaded it
usr/plugins/_ea_auto_skills/promotions/<id>/ proposals awaiting an administrator
usr/skills/<name>/                           promoted, and read by everyone
```

The promotion queue is deliberately not under a directory named `skills`: skill
discovery globs `usr/plugins/*/skills`, and a proposal written there would go live
the moment it was queued.

## What the ledger counts

Every turn already knows which skills it read -- that evidence is gathered for the
reviewer and was previously thrown away with it. It is now kept: when a turn ends, each
skill this plugin wrote for that user gains a load, and a second count if the turn ran
to completion.

Two numbers rather than one, because they answer different questions. Loads say a skill
is being reached for. Completions say the tasks that reach for it tend to finish. A
skill with many loads and few completions is not evidence of a good skill.

Only skills in that user's ledger are counted. A bundled or promoted skill is not this
plugin's to keep records about.

The count is a receipt, not a control: nothing reads it to decide anything. It appears
on a promotion so an administrator can see whether the author actually uses what they
are being asked to give everybody.
