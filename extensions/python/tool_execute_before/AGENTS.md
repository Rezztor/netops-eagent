# Tool Execute Before Extensions DOX

## Purpose

- Own backend processing immediately before tool execution.

## Ownership

- Ordered Python files own the licence gate, prior tool-output replacement, parallel recursion guards, and secret unmasking before execution.
- `_05_licence_restricted.py` refuses the tools in `helpers.license_entitlements.RESTRICTED_TOOLS` (drafting or applying a device or cloud change) while the installation is restricted. It runs first so nothing is unmasked for a call that will not run.

## Local Contracts

- `_10_unmask_secrets.py` resolves `§§secret(KEY)` only for a tool in `helpers.secrets.SECRET_RESOLVING_TOOLS` (code execution, `input`, the text editor), and only in top-level string arguments. For any other tool the alias stays as written and the agent gets one warning line in its history (ADR 0086).
- Preserve safety checks and do not expose secrets to logs or unrelated tools.
- Keep ordering stable where replacement must occur before unmasking or execution.

## Work Guidance

- Coordinate with secret handling, tool argument preparation, and plugin tool gates.

## Verification

- Smoke-test tool execution with masked secret arguments and prior-output references after changes.

## Child DOX Index

No child DOX files.
