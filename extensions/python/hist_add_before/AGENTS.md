# History Add Before Extensions DOX

## Purpose

- Own preprocessing before messages are added to agent history.

## Ownership

- Ordered Python files own history content masking before persistence or model reuse.

## Local Contracts

- Preserve secret and sensitive-content masking before history storage.
- A masking failure is logged and the content is replaced by a refusal (`WITHHELD` in `_10_mask_content.py`); unmasked content never reaches history.
- Do not remove fields required by downstream history organization or replay.

## Work Guidance

- Coordinate history mutation changes with message persistence and plugin-owned history consumers.

## Verification

- Test message history insertion for masked content after changes.

## Child DOX Index

No child DOX files.
