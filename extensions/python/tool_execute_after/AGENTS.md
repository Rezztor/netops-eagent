# Tool Execute After Extensions DOX

## Purpose

- Own backend processing immediately after tool execution.

## Ownership

- Ordered Python files own post-tool secret masking and future tool-result postprocessing.
- `_20_redact_network_output.py` runs `_ea_inventory` `redact_text` over every `net_*` and `inventory_*` tool result, so a new network tool cannot forget (`tests/test_redact_network_output.py`).

## Local Contracts

- Mask secrets before tool results reach history, UI, or model-visible context.
- Do not alter tool `break_loop` or response semantics unless the hook contract owns that behavior.

## Work Guidance

- Coordinate with tool implementations and history hooks when changing tool result data.

## Verification

- Smoke-test tool execution with sensitive output after changes.

## Child DOX Index

No child DOX files.
