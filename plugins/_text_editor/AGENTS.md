# Text Editor Plugin DOX

## Purpose

- Own the native LLM-friendly text editing tool for reading, writing, and patching text files.

## Ownership

- `tools/text_editor.py` owns method dispatch and agent-facing tool behavior.
- `helpers/` owns file operations, patch requests, context patching, stale-read tracking, and patch state.
- `prompts/` owns read/write/patch success and error messages.
- `default_config.yaml`, `plugin.yaml`, `README.md`, `extensions/`, and `webui/` own defaults, metadata, docs, hooks, and config UI.

## Local Contracts

- Every action's `path` goes through `helpers.workdir.confine_path` before anything is opened: the calling account's working directory (relative paths are read from it), its uploads, downloads and skills, and the projects folder. Anything else, and any call without a user scope, is refused for every role. A write or a patch to a project's `AGENTS.md` (any folder of the project) is refused unless the account is an administrator (`helpers.workdir.cannot_change_project_instructions`); reading it is open to all.
- Preserve stale-read protection before patch operations.
- Validate patch structures before applying edits.
- A patch changes the lines it names and nothing else: every patch path reads through `file_ops._read_text` and
  writes through `file_ops._replace_file`, which keep the file's line ends (CRLF stays CRLF), its bytes that are
  not UTF-8, and its mode.
- Read back changed regions after writes or patches where the tool contract requires confirmation.
- Include the active agent context id in write/patch result metadata when available so canvas consumers can open the changed file in the correct chat context.

## Work Guidance

- Coordinate helper changes with prompt responses so the agent receives actionable edit feedback.

## Verification

- Smoke-test read, write, patch, stale-read rejection, and error handling after tool changes.

## Child DOX Index

No child DOX files.
