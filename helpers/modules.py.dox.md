# modules.py DOX

## Purpose

- Own the `modules.py` helper module.
- This module imports modules and classes dynamically with namespace cache control.
- Keep this file-level DOX profile synchronized with `modules.py` because this directory is intentionally flat.

## Ownership

- `modules.py` owns the runtime implementation.
- `modules.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Top-level functions:
- `find(file_path: str) -> str`
- `exists(path: str) -> bool`
- `list_modules(folder: str, pattern: str=..., recursive: bool=...) -> list[str]`
- `import_module(file_path: str) -> ModuleType`
- `load_classes_from_folder(folder: str, name_pattern: str, base_class: Type[T], one_per_file: bool=...) -> list[Type[T]]`
- `load_classes_from_file(file: str, base_class: type[T], one_per_file: bool=...) -> list[type[T]]`
- `purge_namespace(namespace: str)`
- Notable constants/configuration names: `T`.

## Runtime Contracts

- **A module is named by its `.py` path.** `find` returns the absolute path when the file is there and `""` otherwise; `exists` is `os.path.exists` on the resolved path; `list_modules` lists a folder's `.py` files, sorted, as paths, and returns nothing for a folder that is not there. Callers go through these three rather than their own `.py` test, so one rule decides what counts as a module.
- `import_module` loads the file under the name its path gives, as a fresh module on every call. The short name is never put in `sys.modules`, where `plugins` (from `api/plugins.py`) would shadow the package of that name.
- **`import_module` refuses any file whose real path lies under `usr/users/`** (`ImportError`), the tree that holds uploads, workdirs and everything an agent writes for an account. It is defence in depth behind `Agent.get_tool`'s name check (audit 6 C1): no discovery root is there, so nothing legitimate is lost. A wider "only known roots" rule was not taken, because tests and plugin loaders import from temporary trees.
- Discovery sites must not test for a `.py` file themselves (`os.path.exists`, `Path.is_file`, `glob("*.py")`); they ask this module.
- Helper modules own reusable framework APIs and must preserve public callers unless all callers, tests, and docs are updated together.
- Update this file whenever public functions, classes, persistence behavior, path/security assumptions, side effects, or cross-module contracts change.
- Observed side-effect areas: filesystem reads, filesystem deletion.
- Imported dependency areas include: `fnmatch`, `helpers.files`, `importlib`, `importlib.util`, `inspect`, `os`, `re`, `sys`, `types`, `typing`.

## Key Concepts

- Important called helpers/classes observed in the source: `TypeVar`, `get_abs_path`, `os.path.basename.replace`, `importlib.util.spec_from_file_location`, `importlib.util.module_from_spec`, `spec.loader.exec_module`, `import_module`, `inspect.getmembers`, `to_delete.sort`, `importlib.invalidate_caches`, `ImportError`, `os.path.join`, `os.path.basename`, `issubclass`, `os.listdir`, `name.startswith`, `n.count`, `fnmatch`, `file_name.endswith`.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve public helper APIs used by core code and plugins unless every caller is updated.
- Keep path, auth, secret, persistence, network, and subprocess behavior explicit and bounded.
- Prefer adding cohesive helper functions here only when behavior is reused across modules.

## Verification

- Run targeted tests for changed helper behavior; run security regressions for auth, filesystem, WebSocket, tunnel, upload, or secret-handling helpers.
- Related tests observed by source search:
  - `tests/test_ea_connector_prompt_gating.py`
  - `tests/test_browser_agent_regressions.py`
  - `tests/test_docker_release_plan.py`
  - `tests/test_error_retry_plugin.py`
  - `tests/test_file_tree_visualize.py`
  - `tests/test_git_version_label.py`
  - `tests/test_mcp_handler_multimodal.py`
  - `tests/test_memory_quality.py`

## Child DOX Index

No child DOX files.
