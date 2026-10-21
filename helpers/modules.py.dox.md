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
- `_import_compiled(abs_path: str, file: str) -> ModuleType`
- `exists(path: str) -> bool`
- `list_modules(folder: str, pattern: str=..., recursive: bool=...) -> list[str]`
- `import_module(file_path: str) -> ModuleType`
- `load_classes_from_folder(folder: str, name_pattern: str, base_class: Type[T], one_per_file: bool=...) -> list[Type[T]]`
- `load_classes_from_file(file: str, base_class: type[T], one_per_file: bool=...) -> list[type[T]]`
- `purge_namespace(namespace: str)`
- Notable constants/configuration names: `T`.

## Runtime Contracts

- **A module is named by its `.py` path whether or not its source is shipped.** A delivered image carries the product's own modules compiled (one `<name><extension suffix>` file where `<name>.py` was), and every caller keeps building and passing `.py` paths. `find` turns such a path into the file that holds the module: the compiled module when there is one, otherwise the source, otherwise `""`. Compiled first is the order `import` itself uses, so a tree never runs one module from two files, and a `.py` left behind by an update does not bring the old code back. `exists` is `os.path.exists` plus that rule; `list_modules` lists a folder's modules once each, as `.py` paths, sorted, and returns nothing for a folder that is not there.
- **Under `usr/` only source counts**, judged on the real path, so a link from the product's tree into `usr/` does not change it. That tree is written at run time, and the checks on what may be put there (`helpers/projects.py` on an imported repository, the upload rules) look for `.py`; a compiled module accepted there would pass them unseen.
- `import_module` loads whichever file `find` gives, under the name the `.py` path gives. **A compiled file is loaded once per process** (`_import_compiled`): it keeps its globals in the shared library, so a second load turns the functions of the first module object to the second's variables (measured with Nuitka 4.3: after loading a file twice, `a.add(2)` appended to `b.STATE`). The one object is also what `import` gives under the file's dotted name: an `import` that came first is reused, and a load by path is registered in `sys.modules` under the dotted name and on its package. The module is named by the last part of its path when it is loaded by path first and by its dotted name when an `import` got there first, so a discovered module that declares an `@extensible` function must not be imported by name anywhere (a test holds this). `purge_namespace` leaves compiled modules in `sys.modules` for the same reason. Source files behave as before: a fresh module on every call. The short name is never put in `sys.modules`, where `plugins` (from `api/plugins.py`) would shadow the package of that name.
- **`import_module` refuses any file whose real path lies under `usr/users/`** (`ImportError`), the tree that holds uploads, workdirs and everything an agent writes for an account. It is defence in depth behind `Agent.get_tool`'s name check (audit 6 C1): no discovery root is there, so nothing legitimate is lost. A wider "only known roots" rule was not taken, because tests and plugin loaders import from temporary trees.
- A file whose name has a period in it (`prompts/agent.system.main.tips.py`) can only be source: a compiled module's name is one identifier.
- Discovery sites must not test for a `.py` file themselves (`os.path.exists`, `Path.is_file`, `glob("*.py")`); they ask this module. `tests/test_compiled_modules.py` covers the rule.
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
