# netops-vlan-trunk Skill DOX

## Purpose

- Read-only diagnosis of VLAN and trunk problems.

## Ownership

- `SKILL.md` owns the trigger metadata and procedure.

## Local Contracts

- The agent drafts and explains; the requesting engineer approves and is accountable.
- Name only real tools (`net_read`, `net_change`, `net_apply`, `net_plan`, `inventory_*`, `solarwinds_lookup`); no invented commands, devices or credentials.
- Every command in a table cell is a plain `show` accepted by `reads/show`; commands only an engineer runs sit in a column headed "engineer runs".
- Keep it under about 70 lines. Update the skill list in the profile specifics when it is added or renamed.

## Verification

- `pytest tests/test_netops_profiles.py`

## Child DOX Index

No child DOX files.
