# Default Agent Profile DOX

## Purpose

- Own base profile metadata and default prompt specifics inherited by specialized profiles.
- Provide the shared behavior layer for bundled and custom profiles.

## Ownership

- `agent.yaml` owns default profile metadata.
- `prompts/agent.system.main.specifics.md` owns default profile-specific system prompt content.
- `plugins/_tool_access/config.json` owns the profile's tool policy: a general assistant (search, skills, documents, code execution and editing, memory, asking the user) with device, cloud, scheduler, A2A, subordinate, parallel, mail and MCP tools blocked, the same tools the NetOps profiles block. Any account can switch a chat to this profile, so it must never fall back to the plugin's allow-everything default.
- Additional prompt overrides under this directory become shared defaults unless a child profile overrides them.

## Local Contracts

- Keep default behavior broad, framework-compatible, and safe for inheritance.
- Avoid role-specific instructions that belong in specialist profiles.
- Prompt filenames must match the framework prompt override names they target.

## Work Guidance

- Prefer small, explicit prompt changes with clear inheritance impact.
- Check bundled specialist profiles after changing default behavior.

## Verification

- Manually inspect YAML and prompt rendering assumptions after edits.
- Run prompt/profile tests when changing inherited prompt behavior.

## Child DOX Index

No child DOX files.
