# Agent Profiles DOX

## Purpose

- Own bundled agent profiles, profile-specific prompts, and profile-local tools.
- Keep profile behavior understandable without requiring edits to core framework prompts.

## Ownership

- Each direct profile directory owns its `agent.yaml`, optional `prompts/`, optional `tools/`, and optional `extensions/`.
- `_example/` demonstrates profile layout, is not selectable, and should stay
  suitable as a reference.
- User-created local profiles belong under `usr/agents/`, not here, unless they are intended to ship with the product.

## Local Contracts

- `agent.yaml` is the profile entry point and must stay valid YAML.
- Profile prompt overrides should be narrow and named to match the core prompt they extend or replace.
- Profile-local tools must follow the same `Tool` contract as root `tools/`.
- Every selectable profile, `default` included, ships `plugins/_tool_access/config.json` (default block + allow-list); it, not the prompt, is the enforcement. `api/agent_profile_set.py` lets any account switch a chat to any listed profile and `call_subordinate` can delegate to one, so a profile with no policy would be the way around the others (audit 6 M1). `tests/test_tool_policy.py` holds this.
- Do not put secrets, provider API keys, local paths, or user-specific settings in bundled profiles.

## Work Guidance

- Prefer small profile-specific prompt files over duplicating large core prompts.
- Keep examples generic and runnable in a clean checkout.
- When changing profile behavior, check how the WebUI profile picker and backend profile loader discover profiles.

## Verification

- Run `pytest` or targeted tests covering profile loading when changing `agent.yaml` structure or profile discovery.
- Manually inspect YAML validity for changed profiles if no targeted test exists.

## Child DOX Index

Direct child DOX files:

| Child | Scope |
| --- | --- |
| [_example/AGENTS.md](_example/AGENTS.md) | Reference profile demonstrating profile-local prompts, tools, and extensions. |
| [primary/AGENTS.md](primary/AGENTS.md) | NetOps Operator: the default profile (id `primary`). |
| [network-troubleshooter/AGENTS.md](network-troubleshooter/AGENTS.md) | Read-only fault isolation. |
| [network-researcher/AGENTS.md](network-researcher/AGENTS.md) | Vendor docs, advisories, change-impact research; no device access. |
| [automation-engineer/AGENTS.md](automation-engineer/AGENTS.md) | Ansible/Jinja/Python and capability drafts; no device access. |
| [default/AGENTS.md](default/AGENTS.md) | Base profile metadata and inherited prompt specifics. |
