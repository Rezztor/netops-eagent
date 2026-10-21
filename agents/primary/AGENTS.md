# NetOps Operator (primary) Profile DOX

## Purpose

- Own the default user-facing profile, id `primary`, titled NetOps Operator.
- The settings default stays `primary`; renaming the id breaks it.

## Ownership

- `agent.yaml` owns title, description and delegation context.
- `prompts/agent.system.main.specifics.md` owns the NetOps role and rules; `prompts/agent.system.tool.response.md` owns the response format.
- `plugins/_tool_access/config.json` owns the enforced tool allow-list (default block).

## Local Contracts

- The prompt is guidance; the allow-list is the enforcement. Change both together.
- The agent drafts and explains; the requesting engineer approves on the card in the chat and is accountable for execution.
- Keep `response` and the loop-required tools reachable; `refused` stays allowed so refusals read correctly.
- Do not store user-specific preferences, provider settings or secrets in this profile.

## Verification

- `tests/test_netops_profiles.py` and `tests/test_default_prompt_budget.py`.

## Child DOX Index

No child DOX files.
