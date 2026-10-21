# Skills

Skills are focused instructions Rezztor EAgent can load when a task needs them.

Most of the time, you do not need to think about skills. Ask for the work you
want, and Rezztor EAgent can load a matching skill on demand.

The **Skills** screen in the sidebar lists the skills installed on this instance.
There is no skill picker in the chat.

## Ask For A Skill By Name

When you want the agent to follow a particular skill, say so in the chat:

```text
Use the vlan-change skill for this.
```

The agent loads it and follows it for the rest of the task.

## When To Ask For A Skill

Ask for a skill when the current chat should follow the same special procedure.

Good examples:

- creating an Agent Profile;
- reviewing a plugin;
- following a writing format;
- working with a repeated data-cleaning recipe;
- keeping a project-specific checklist visible during a long chat.

Do not ask for a skill just because it might be useful someday. A lighter prompt
is usually easier for the agent to follow.

## Skills, Profiles, And Projects

| Control | What it changes |
| --- | --- |
| **Skills** | Adds a specific procedure the agent follows. |
| **Agent Profiles** | Changes the broader role and behavior of the chat. |
| **Projects** | Adds workspace, files, memory, secrets, and project instructions. |

If Rezztor EAgent keeps following a procedure you no longer want, tell it to stop
using that skill, or start a new conversation.

The Agent Editor can also set a profile-wide Skills visibility policy. That
policy controls discovery and new loading; it does not erase skill text already
stored in a chat's history. See [Agent Profiles](agent-profiles.md#choose-capability-access).

## Creating Skills

This page is about using skills in the Web UI.

If you want to write or contribute a skill, see
[Contributing Skills](../developer/contributing-skills.md).
