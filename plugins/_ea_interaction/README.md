# Interaction Cards

Lets the agent ask you a question and give you answers to click, instead of
describing the question in prose and hoping your reply has the shape it expected.

```
Which switches should I gather facts from?

  [ Core switches        ]  [ Access switches            ]
    core-sw-1, core-sw-2       14 devices in access_switches
  [ Something else…      ]
```

Clicking sends what you would have typed. Nothing waits, nothing times out — the
agent's turn ends when it asks, and your answer starts the next one.

**"Something else…"** puts the question in the composer with room to type, so a
written answer is still an answer to *that* question.

A card stops being clickable once the conversation moves past it. It stays
readable, because what was asked is part of the record.

It can also stop and ask before doing something:

```
Restart the interface on core-sw-1?
The port drops for roughly ten seconds.

  [ Approve ]  [ Reject ]  [ Something else… ]
```

Silence is not approval. If nobody answers, the thing does not happen.

## What this is not

**It cannot authorise anything.** Clicking a card sends a message, and the server
cannot tell that apart from someone typing the same word — so a card must never be
the thing that grants permission. An approval that carries authority (approving
the commands a network change will push, say) calls a permission-checked endpoint
instead, and that is deliberately a separate piece of work rather than a flag on
this one.

## Status

Questions, and approvals that ask rather than authorise, are in this plugin.
Endpoint-backed approvals are separate: a card cannot grant a permission.
