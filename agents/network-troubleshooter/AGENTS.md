# Network Troubleshooter Agent Profile DOX

## Purpose

- Own the bundled Network Troubleshooter profile: read-only fault isolation.

## Ownership

- `agent.yaml` owns title, description and delegation context.
- `prompts/agent.system.main.specifics.md` owns the role and rules.
- `plugins/_tool_access/config.json` owns the enforced tool allow-list (default block).

## Local Contracts

- The prompt is guidance; the allow-list is the enforcement. Change both together.
- Never allow net_change, net_apply, code execution or sub-agents.
- The agent drafts and explains; the requesting engineer approves and is accountable for execution.
- Keep the specifics file under about 60 lines. No secrets, hostnames or IPs.

## Verification

- `tests/test_netops_profiles.py` and `tests/test_default_prompt_budget.py`.

## Child DOX Index

No child DOX files.
