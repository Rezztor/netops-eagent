# Reasoning Stream End Extensions DOX

## Purpose

- Own finalization of reasoning stream content.

## Ownership

- Ordered Python files own final reasoning masking and end-of-stream cleanup.

## Local Contracts

- A masking failure is logged (`PrintStyle.error`, exception type only), never swallowed; history is still masked downstream by `hist_add_before`.
- Preserve final masking even if earlier chunk masking missed content.
- Keep end-state consistent with reasoning stream log entries.

## Work Guidance

- Coordinate final masking changes with chunk masking and UI rendering.

## Verification

- Smoke-test reasoning stream completion with sensitive-content cases.

## Child DOX Index

No child DOX files.
