# Commands Plugin DOX

## Purpose

- Own the built-in slash command manager and chat composer slash/reference picker.
- Keep file-backed `/command` discovery consistent across project, global, and plugin-provided scopes.

## Ownership

- `plugin.yaml` owns the built-in `_commands` plugin metadata.
- `helpers/commands.py` owns command name sanitization, argument parsing, scope resolution, file persistence, plugin command discovery, and command invocation resolution.
- `api/commands.py` owns the Commands API actions used by the WebUI.
- `webui/` owns the manager/editor modal stores, HTML surfaces, and thumbnail asset.
- `commands/` owns bundled read-only slash command definitions shipped by `_commands`, including `/stop` agent-run control.
- `extensions/` owns the chat composer slash and `@` reference picker plus incoming-message command resolution.
- `extensions/python/startup_migration/` owns one-time migration from the legacy community `commands` plugin namespace.
- `skills/commands-create-slash-command/` owns the agent-facing authoring workflow for reusable slash commands.
- `tests/` owns regression coverage for parsing, CRUD, scope precedence, plugin-distributed commands, legacy migration, and skill discovery.

## Local Contracts

- `/profile "Title" "instructions"` creates an agent profile only for a caller with `plugins.manage`, and `scope_info` makes the commands folder (`ensure_directory`) only for one: a profile is every account's to load, and a folder under the projects folder is a project in every account's list.
- Saving, deleting and duplicating a command need `plugins.manage`: commands are shared by every account and a script command runs in the web server. Listing and resolving stay open to every signed-in account.
- `/chats` lists the caller's own loaded conversations, and `/chat <id>` answers another account's id as it answers one that does not exist.
- The plugin identity is `_commands`; user-created command files live under `usr/plugins/_commands/commands/` or `usr/projects/<project>/.eaproj/plugins/_commands/commands/`.
- Each command is one `.command.yaml` config plus one same-directory `.txt` text template or `.py` script hook.
- Project commands override global commands, global commands override bundled `_commands/commands/` defaults, and bundled defaults override other plugin-distributed commands with the same name.
- Bundled `_commands/commands/` definitions and commands contributed by other plugins are read-only from this manager.
- The manager lists bundled commands separately; editing one copies it unchanged into the selected project or global scope under the same name, then edits that higher-precedence override.
- Bundled command files use canonical command names only; do not ship alias-only built-ins such as `/img` for `/attach`.
- Command configs may set `webui_hidden: true` to stay resolvable but be omitted from the chat composer picker.
- Commands contributed by enabled plugins live in their `commands/` directory and must not be rediscovered through the generic plugin-distributed path from `_commands` itself.
- On startup, `_commands` copies legacy `usr/plugins/commands` command and skill files into `usr/plugins/_commands` without overwriting existing files, copies scoped legacy command folders to `_commands`, and disables the legacy `commands` plugin roots to prevent duplicate WebUI popovers.
- Script commands must expose `run(payload)` and return a string or a dict with `text` and optional `effects`; `show_markdown` effects render as auto-dismissing toast notifications.
- Script payloads set `context.agent` from `AgentContext.get_agent()`, not a non-existent `primary` attribute; `/rename` and other agent-aware scripts depend on it.
- Script commands may emit `send_message` with `text` to submit the rendered composer text immediately after command resolution.
- Commands accept prefix syntax (`/goal objective`) and exact postfix syntax (`objective /goal`); ordinary mid-sentence mentions are not invocations. The composer picker opens only for prefix syntax, while postfix commands resolve when sent.
- Composer `@` selections insert plain references only: `@[./path]`, `@[./folder/]`, `@[agent/profile]`, `@[skill/name]`, or `@[mcp/server]`. They never load content, activate skills, call MCP, or delegate by themselves.
- Selected reference icons may use the composer highlight color while their labels keep the normal text color; serialized prompt text remains unchanged.
- File and folder references stay inside the active chat workdir and list one directory at a time through the existing file-browser and chat-path APIs. Profile and effective MCP server references reuse their scoped catalogs; skill references use only entries visible in the active chat scope.
- WebUI sends resolve through the picker effect path, while backend-originated messages resolve before reaching the agent.
- `/stop` uses the same shared cancellation operation as the composer Stop button, including progress cleanup and terminal logging.
- `/profile` opens Manage agents without arguments, keeps existing profile
  selection, and creates through Agent Editor when given a name and instructions.
- `/permissions` opens Edit agent for the current WebUI profile; the EAgent Connector
  owns its separate Textual permissions screen for the same command name.

## Work Guidance

- Keep the command storage and route namespace aligned with `_commands`.
- Preserve unknown command config keys when editing commands.
- Keep built-in source files immutable; user edits must be same-name scope overrides.
- Keep WebUI paths pointed at `/plugins/_commands/...`.

## Verification

- Run `pytest plugins/_commands/tests` after backend or command contract changes.

## Child DOX Index

No child DOX files.
