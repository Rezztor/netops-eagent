# Usage Guide

This guide is the practical tour of Rezztor EAgent after installation. It explains
what you can do in the Web UI and what to try first.


## Basic Operations

Rezztor EAgent is built around a chat, a working Linux environment, and a Web UI that
lets you watch and steer the work.

Common places to start:

- **Overview:** what needs doing. Administrators see a setup checklist; engineers
  see changes waiting for their approval and their recent conversations.
- **Chat:** conversations with the agent.
- **Files:** your working directory.
- **Skills:** instructions the agent can load on demand.
- **My schedules:** your own reminders and unattended work.
- **Network** and **Inventory:** what ran on your devices, and the devices themselves.
- **Approvals:** for those who may approve network changes, every change waiting for
  their signature. The bell and an email point here when one starts to wait.
- **Drift:** which devices' configuration changed, and whether an approved change explains it.
- **Settings:** your preferences, and for administrators the instance's configuration,
  licence, updates, backup, restart and support bundle (under **Product**).

Administrators also see an **Administration** group: **Users**, **Plugins**,
**Models** (which model answers, and the provider API keys), **System** (everyone's
scheduled tasks, MCP servers, agent profiles, projects, memory and knowledge) and
**Activity**.

The chat input has buttons to attach files and, where the browser offers it, to
dictate. While the agent works, **Pause**, **Nudge** and **Stop** appear above the
input. **Inspect context**, **Compact**, **Export**, **Reset** and **Delete
conversation** are in the row under the conversation.

Use **Settings -> Restart** when you need the framework to reload after a setting
that is read at startup.

## Plugins

Plugins add integrations, tools, and automation helpers.

Open **Plugins** in the sidebar to see what is installed and to enable or disable
each one.

There is no install button and no plugin hub. Installing arbitrary third-party
plugins into an agent that can execute code is a supply-chain risk the platform
declines to carry, so a capability is added by writing it into the repository,
not by an install button. A hand-installed deployment loads the new images and
sets `REZZTOR_VERSION`; an appliance installed from the ISO takes one signed
file an administrator supplies. That is a deliberate constraint, not a missing
feature.

To write one, see [Create a Plugin](create-plugin.md). Plugins in this fork
contribute tools, prompts, API endpoints and lifecycle extensions; any interface
they need is built into the React application rather than shipped as plugin
markup.

## Device Inventory

Open **Inventory** in the sidebar for the devices this instance knows about. A
count beside it is the proposals and conflicts waiting for review. To bring
devices in from a spreadsheet, open the **Sources** tab and **Upload a file**: a
new chat opens with the file attached and the assistant stages the devices for
your review. See [Device inventory](inventory.md).

## Skills, Agent Profiles, And Model Presets

The row above the conversation lets you shape the current chat without opening
the full Settings screen.

### Skills

Skills are focused instructions Rezztor EAgent loads when a task needs them. There
is no skill picker in the chat: to have the agent follow a particular skill, ask
for it by name. The **Skills** screen lists what is installed. See the
[Skills guide](skills.md).

### Agent Profiles

Agent Profiles change the role, tone, and prompt instructions for the selected
chat.


Choose the profile for the current chat from the row above the conversation.

Use **System -> Agent profiles** (administrators) to edit profiles and to change the
default profile for new Global chats.

See the [Agent Profiles guide](agent-profiles.md).

### Model Presets

Model Presets are named shortcuts for model choices. Use them for setups like
`Best`, `Balanced`, `Fast Cheap`, or a model name you can spot quickly.

Choose the model for the current chat from the menu above the conversation, when
your administrator allows it. The presets are listed on the **Models** screen.


See the [Model Presets guide](model-presets.md).

## File Attachments

Attach files when the agent should read, summarize, transform, or organize them.


You can attach one file or several files, then describe what should happen:

```text
Read these PDFs and create a short comparison table.
```

```text
Move these files into a clean folder structure and explain what changed.
```

Attached files are visible in the chat input before you send the message, so you
can remove mistakes before Rezztor EAgent starts working.

## Tool Usage

You usually do not need to name tools. Say what you want done and Rezztor EAgent will
choose whether it needs the browser, code execution, files, knowledge, plugins,
or another available capability.

Good prompts are specific about the desired result:

```text
Research three deployment options for this app. Cite sources and finish with a recommendation.
```

```text
Open the attached CSV, find the main trend, and create a chart I can edit later.
```

```text
Inspect this repository and propose the safest first improvement before changing files.
```

### Agent-To-Agent Communication

Rezztor EAgent instances can communicate through A2A when you want multiple
instances to collaborate.

Use A2A when you have a clear reason to split work across Rezztor EAgent instances,
such as a specialist server, a remote machine, or a project-specific agent. See
[A2A Setup](a2a-setup.md).

### Multi-Agent Cooperation

Inside a single Rezztor EAgent instance, the main agent can create subordinate agents
to investigate focused parts of a larger job.


This is useful for research, code review, comparison work, and tasks where one
agent should gather information while another keeps the main plan moving.

## Projects

Projects tell Rezztor EAgent what world it is working in. Use one when a chat needs
its own files, instructions, memory, secrets, or model settings.


The simple flow:

1. Open **System** in the sidebar and find the **Projects** card.
2. Click **Create project**.
3. Give it a clear title.
4. Add a short description.
5. Write practical instructions.
6. Save it.
7. There is no project picker in the chat interface yet. Ask the agent to load the
   project, or set the default in the Projects card.


Good project instructions tell Rezztor EAgent what should be different in that
workspace:

```markdown
When this project is active:
- Explain steps in plain language before technical detail.
- Prefer screenshots, checklists, and concrete examples.
- Keep generated files inside this project unless I ask otherwise.
- Ask before using credentials, private data, or external accounts.
```

Use projects for client work, code repositories, research topics, recurring
reports, and any workflow where context matters.

See the [Projects guide](projects.md) for the full screenshot walkthrough.

## Tasks And Scheduling

Tasks let Rezztor EAgent run work later, repeatedly, or on demand.

Use tasks for:

- morning or weekly reports;
- monitoring a source and summarizing changes;
- recurring cleanup or export jobs;
- project-specific checks;
- manual batch jobs you want to run again.

Open **My schedules** in the sidebar for your own tasks. Administrators see
everyone's on **System**, in the **Scheduled tasks** card.

The interface lists tasks, runs them on demand, and deletes them. **It cannot create
one.** Ask the agent to do it instead -- it has a `scheduler` tool for exactly this --
or write to `usr/scheduler/tasks.json` directly. A task carries:


When creating a task, focus on four things:

- **Name:** what you will recognize later.
- **Type:** scheduled, planned, or ad-hoc.
- **Project:** optional, but recommended when the task needs specific context or secrets.
- **Prompt:** the actual work Rezztor EAgent should perform.


Example:

```text
Name: Weekly docs review
Type: Scheduled
Project: Documentation
Prompt: Check the docs project for stale screenshots, broken links, and confusing sections. Summarize what needs attention.
```

Project-scoped tasks inherit project instructions, variables, secrets, files, and
memory. That means you can improve task behavior later by improving the project
instead of repeating every rule in every task.

## Secrets And Variables

Use **Secrets** for sensitive values such as API keys, tokens, passwords, and
credentials.

Do not type a password or key into chat. A message that contains something that
looks like one ("the password is ...", `enable secret ...`, an SNMP community, a
private key, a bearer token) has the value replaced with `[REDACTED:...]` before
it is stored or reaches the model, and you see a warning. Device credentials are
given only through the secure credential form.

Use **Variables** for non-sensitive settings such as regions, URLs, usernames,
formats, or feature flags.

Refer to them by name in chat:

```text
Use the project GITHUB_TOKEN to check repository status.
```

Do not paste credentials into chat messages or public files. Keep your own copy
of important secrets because backups may not include every secret.

## Voice Interface

Rezztor EAgent supports text-to-speech and speech-to-text through built-in voice plugins:

- `_kokoro_tts` provides container-side Kokoro speech synthesis when enabled.
- `_whisper_stt` provides local Whisper transcription and adds the microphone control when enabled.
- Browser-native `speechSynthesis` remains the fallback output path when `_kokoro_tts` is disabled.

Configure the providers in the Whisper STT and Kokoro TTS plugin settings.

Use speech when you want to listen while doing something else, dictate a prompt,
or make the interface more accessible.


Speech-to-text settings live in the Whisper STT plugin card and include model size,
language code, voice message handling, silence threshold, and recording behaviour.

The microphone button in the chat input is the browser's own dictation, offered only
where the browser has it; in Chrome and Edge it sends your voice to the browser
vendor's speech service, not to this instance. Nothing in the web application calls the
Whisper and Kokoro plugins yet, so their settings have no visible effect.


> [!IMPORTANT]
> Whisper STT and Kokoro TTS operate locally within the Docker/container runtime when their plugins are enabled.
> Browser fallback TTS runs locally in the browser. No voice path requires OpenAI APIs.

## File Browser

The File Browser lets you inspect and manage files inside the Rezztor EAgent
environment.


Use it to:

- upload files;
- download generated work;
- create folders;
- rename or delete files;
- open editable text files;
- inspect the project or `/eagent/usr` workspace.

For file-based work, prefer `/eagent/usr` or a project workspace. Avoid storing
important work only in temporary directories.

## Memory Management

Memory is where Rezztor EAgent keeps useful remembered information from conversations
and imported knowledge. It is powerful, but it is not magic. Long-term AI memory
still needs curation; this is not fully solved even by the largest AI labs and
companies.

Open **System** and find the **Memory and knowledge** card to review what is
stored, add documents to the shared knowledge base, and rebuild its index.

Searching, editing and removing individual memories is not in the interface -- ask the
agent, which has `memory_load`, `memory_save`, `memory_delete` and `memory_forget`
tools for it. The card is a dashboard over stored
entries.


The controls let you choose a memory directory, filter by area, set a result
limit, search, adjust match threshold, and clear filtered results.


Use memory deliberately:

- keep durable facts and useful patterns;
- remove old test data;
- edit memories that became inaccurate;
- use project memory for project-specific context;
- create a backup before large cleanup.

If Rezztor EAgent does something unexpected, repeats a wrong behavior, or seems to
remember the wrong thing, Memory is one of the first places to look. A stale or
incorrect memory can poison the processing instead of helping it.

Click a memory row to inspect its full content and metadata. From the detail
view you can copy, edit, or delete the entry.


For a practical cleanup checklist, see the [Memory Guide](memory.md).

## Backup And Restore

Backups protect your chats, projects, knowledge, memory, settings, skills, and
workspace files.

Create a backup before:

- major updates;
- plugin experiments;
- bulk memory cleanup;
- moving to a new machine;
- deleting or reorganizing important project files.

Open **Settings -> Backup** to create or restore a backup.

**A backup is encrypted with a passphrase you type** (at least 12 characters,
entered twice). The file holds the instance's keys (`usr/.env`), the password
hash of every account and every user's data, so it is unreadable without the
passphrase. The passphrase is not stored anywhere and **cannot be recovered**:
without it the backup cannot be restored. Keep it apart from the file, in a
password manager or a safe. The downloaded file ends in `.rzbak`.

To restore, choose the file, enter the passphrase and press **Inspect**. A wrong
passphrase and a damaged or incomplete file give the same plain refusal and
nothing is written. A `.zip` backup made before backups were encrypted still
restores; the screen says it was not protected, so take a new backup afterwards.

Restore only writes inside `usr/`. An archive entry that points anywhere else
(an absolute path, a `..`, a symbolic link, or a path outside the instance the
backup was taken from) is refused and reported instead of written, and
**Remove files not in the backup** never deletes outside `usr/` either. A backup
made with custom patterns outside `usr/` restores only its `usr/` part.

The backup carries `usr/.env`, so the keys and tokens saved there come back with
a restore. Anything you keep outside `usr/` does not; keep a separate secure copy
of credentials you depend on.

## Next Steps

- Quick Start
- [Projects guide](projects.md)
- EAgent CLI Connector
- [Skills guide](skills.md)
- [Agent Profiles guide](agent-profiles.md)
- [Model Presets guide](model-presets.md)
- [MCP Setup](mcp-setup.md)
- [Troubleshooting](troubleshooting.md)
