# Response Stream Chunk Extensions DOX

## Purpose

- Own handling of incremental assistant response chunks.

## Ownership

- Ordered Python files own chunk-level response masking.

## Local Contracts

- A masking failure is logged (`PrintStyle.error`, exception type only), never swallowed; history is still masked downstream by `hist_add_before`.
- Mask secrets before response chunks reach UI or persisted logs.
- Keep chunk processing compatible with final response stream masking.

## Work Guidance

- Keep per-chunk work lightweight for streaming responsiveness.

## Verification

- Smoke-test streaming responses with sensitive patterns after changes.

## Child DOX Index

No child DOX files.
