# Infection Check Plugin DOX

## Purpose

- Own prompt-injection safety checks over streamed reasoning/response content before tool execution.

## Ownership

- `helpers/checker.py` owns safety analysis orchestration and gate behavior.
- `extensions/` owns stream collection and tool-execution blocking hooks.
- `default_config.yaml`, `plugin.yaml`, `README.md`, and `webui/` own settings, metadata, behavior notes, and config UI.

## Local Contracts

- Preserve configured `thoughts` and `complete` analysis modes.
- Tool execution must wait for required safety verdicts.
- The check is advisory and fails open: a failure of the check itself lets the tool run and must leave a warning in the chat log (`checker.gate`). Never describe it as an enforcement layer.
- The tool gate file must sort before the core `tool_execute_before/_10_unmask_secrets.py` (it is `_09_infection_check.py`): hooks run in file-name order across folders, and after unmasking the arguments sent to the check model would carry plaintext secrets (audit 6 M5).
- Do not log or expose chain-of-thought or sensitive prompt content beyond intended warning flows.

## Work Guidance

- Keep termination and clarification loops bounded and explicit.

## Verification

- Smoke-test ok, clarify, and terminate verdicts around tool execution after changes.

## Child DOX Index

No child DOX files.
