# skills_scan.py DOX

## Purpose

- Own the `skills_scan.py` API endpoint.
- Provide scan target discovery and uploaded skills archive preparation for the Settings > Skills scanner.
- Keep this file-level DOX profile synchronized with `skills_scan.py` because this directory is intentionally flat.

## Ownership

- `skills_scan.py` owns the runtime implementation.
- `skills_scan.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Classes:
- `SkillsScan` (`ApiHandler`)
  - `async process(self, input: dict[str, Any], request: Request) -> dict[str, Any] | Response`

## Runtime Contracts

- HTTP handlers must derive from `helpers.api.ApiHandler`; WebSocket handlers must derive from `helpers.ws.WsHandler`.
- The JSON request action `targets` returns existing installed skill roots that contain at least one `SKILL.md`.
- Multipart requests with `skills_file` accept only `.zip` uploads, extract them into `tmp/skill_scans`, discover contained `SKILL.md` folders, and return their count and archive-relative paths (`skills[].relative_path`). No absolute path is returned: the extraction is removed before the response leaves, on success and on failure, because nothing reads it afterwards (no agent tool may open a file under `tmp/`).
- Uploaded archives are not imported, installed, or executed by this endpoint.
- Temporary uploaded zip files under `tmp/uploads` and the extraction under `tmp/skill_scans` are deleted after extraction or failure.
- Update this file whenever request payloads, authentication or CSRF requirements, response shapes, route side effects, or WebSocket event contracts change.
- Observed side-effect areas: filesystem reads, filesystem writes, filesystem deletion.
- Imported dependency areas include: `__future__`, `helpers`, `helpers.api`, `helpers.skills_import`, `pathlib`, `shutil`, `time`, `typing`, `uuid`, `werkzeug.datastructures`, `werkzeug.utils`.

## Key Concepts

- Installed target discovery uses `helpers.skills.get_skill_roots()` and filters to roots where `discover_skill_md_files()` finds skills.
- Uploaded zip preparation uses `extract_skills_zip()` so zip entries remain bounded to the temp extraction root.
- Installed-target response paths are local absolute paths, while `display_path` provides normalized `/eagent/...` style display when possible.

## Work Guidance

- Preserve authentication, CSRF, loopback, and API-key checks unless the endpoint contract explicitly changes.
- Do not execute uploaded files or scan targets in this endpoint.
- Never leave an extraction behind: each one can be up to the archive limit (`helpers/archive_limits.py`).

## Verification

- Run endpoint-specific or API tests for changed behavior; smoke-test uploaded zip and installed-skill scan modal flows when practical.

## Child DOX Index

No child DOX files.
