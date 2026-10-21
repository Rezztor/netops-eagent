# skills_import.py DOX

## Purpose

- Own the `skills_import.py` helper module.
- This module plans and imports skill bundles into user, project, or profile scopes.
- Keep this file-level DOX profile synchronized with `skills_import.py` because this directory is intentionally flat.

## Ownership

- `skills_import.py` owns the runtime implementation.
- `skills_import.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Classes:
- `ImportPlanItem` (no explicit base class)
- `ImportResult` (no explicit base class)
- Top-level functions:
- `_is_within(child: Path, parent: Path) -> bool`
- `_derive_namespace(source: Path) -> str`
- `_candidate_skill_roots(source_dir: Path) -> List[Path]`: Heuristics to find likely skill roots inside a repo/pack:
- `_safe_extract_zip(zip_path: Path, target: Path) -> None`
- `extract_skills_zip(zip_path: Path, tmp_subdir: str=..., prefix: str=...) -> tuple[Path, Path]`: Extract a zip into a temp folder inside Rezztor EAgent's tmp directory and return the scan/import root plus cleanup root.
- `build_import_plan(source: Path, dest_root: Path, namespace: Optional[str]=...) -> Tuple[List[ImportPlanItem], Path]`: Build a copy plan for importing skills from a source folder.
- `_one_folder_name(name: str, what: str) -> str`: the name when it is a single folder name, `ValueError` otherwise.
- `_resolve_conflict(dest: Path, policy: ConflictPolicy, dry_run: bool=False) -> Tuple[Path, bool]`: Returns (final_dest_path, should_copy). Deletes nothing on a dry run.
- `get_project_skills_folder(project_name: str) -> Path`: Get the skills folder path for a project.
- `get_agent_profile_skills_folder(profile_name: str) -> Path`
- `get_project_agent_profile_skills_folder(project_name: str, profile_name: str) -> Path`
- `resolve_skills_destination_root(project_name: Optional[str], agent_profile: Optional[str]) -> Path`
- `import_skills(source_path: str, namespace: Optional[str]=..., conflict: ConflictPolicy=..., dry_run: bool=..., project_name: Optional[str]=..., agent_profile: Optional[str]=...) -> ImportResult`: Import external Skills into usr/skills/<namespace>/...
- Notable constants/configuration names: `PROJECT_SKILLS_DIR`.

## Runtime Contracts

- `import_skills` deletes nothing when `dry_run` is set (the preview endpoint), refuses a `namespace` or `agent_profile` that is not a single folder name before anything is unpacked, and removes its unpacked copy of the pack under `tmp/skill_imports` when it is done. `tests/test_skills_import_safety.py` covers this.
- Helper modules own reusable framework APIs and must preserve public callers unless all callers, tests, and docs are updated together.
- Update this file whenever public functions, classes, persistence behavior, path/security assumptions, side effects, or cross-module contracts change.
- Observed side-effect areas: filesystem reads, filesystem writes, filesystem deletion, plugin state.
- Imported dependency areas include: `__future__`, `dataclasses`, `helpers`, `helpers.skills`, `os`, `pathlib`, `shutil`, `stat`, `tempfile`, `time`, `typing`, `zipfile`.

## Key Concepts

- Zip extraction rejects absolute paths, backslash paths, path traversal, and symlink entries before extraction.
- Important called helpers/classes observed in the source: `dataclass`, `strip`, `plugins.is_dir`, `Path`, `base_tmp.mkdir`, `time.strftime`, `target.mkdir`, `_candidate_skill_roots`, `Path.expanduser`, `resolve_skills_destination_root`, `dest_root.mkdir`, `build_import_plan`, `ImportResult`, `child.resolve.relative_to`, `direct.is_dir`, `discover_skill_md_files`, `plugins.iterdir`, `files.get_abs_path`, `zipfile.ZipFile`, `archive.extractall`, `shutil.rmtree`.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve public helper APIs used by core code and plugins unless every caller is updated.
- Keep path, auth, secret, persistence, network, and subprocess behavior explicit and bounded.
- Prefer adding cohesive helper functions here only when behavior is reused across modules.

## Archive Limits

- `_safe_extract_zip` unpacks through `archive_limits.extract_zip`, so a zip over the default size limits is refused. `tests/test_archive_limits.py`.

## Verification

- Run targeted tests for changed helper behavior; run security regressions for auth, filesystem, WebSocket, tunnel, upload, or secret-handling helpers.
- No direct test reference was found by name search; choose the nearest behavioral test or perform a focused smoke check.

## Child DOX Index

No child DOX files.
