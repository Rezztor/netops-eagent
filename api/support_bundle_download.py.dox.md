# support_bundle_download.py DOX

## Purpose

- Own `GET /api/support_bundle_download?file=<name>`: hand one support bundle to an administrator, as a file.

## Ownership

- `SupportBundleDownload` — looks the name up with `update_spool.bundle_path` and sends the file.

## Runtime Contracts

- Administrator-only: `required_permission()` returns `rbac.PERM_SETTINGS_MANAGE`, and the endpoint is listed in `helpers/api_policy.py`.
- Only a bundle the host lists in its state, under the host's own name pattern; anything else is a plain 404 with a sentence. The name is never used as a path before that check.
- The bundle was made and cleaned on the host (`deploy/iso/host/rezztor-update`, "the support bundle"). This handler reads it and changes nothing.
- Every download writes one audit event, `support_bundle.download`, with the file and who downloaded it.

## Verification

- `pytest tests/test_update_api.py`.

## Child DOX Index

No child DOX files.
