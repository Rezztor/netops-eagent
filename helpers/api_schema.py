"""Generate an OpenAPI document from the ApiHandler classes.

The API has no router: `helpers.api.register_api_route` registers one Flask rule,
`/api/<path:path>`, and resolves the handler by looking for a matching file under
`api/`, `plugins/<name>/api/`, or `usr/api/`. There is therefore no route table to
read -- the filesystem *is* the route table, and this module walks it.

What the document can state precisely is routing and security: every handler
declares its methods and its authentication, CSRF, API-key and loopback
requirements as classmethods, and the permission it needs comes either from the
handler or from `helpers.api_policy`. All of that is introspectable, and all of it
is what a frontend developer actually needs to call an endpoint correctly.

What the document cannot state precisely is request and response bodies.
`helpers.api.Input` is `dict` and `Output` is `dict | Response`; no handler
declares a typed model. Rather than invent shapes that would drift from the code
the moment either side changed, endpoints without a declared model are documented
as accepting and returning a free-form object. Handlers opt in to a real body
schema by overriding `input_model()` / `output_model()` (see `ApiHandler`), and
this generator emits the precise schema for those. The `x-body-schema` extension
on each operation says which of the two you are looking at, so a consumer can
tell a documented shape from an undocumented one instead of trusting both
equally.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, TypedDict

from helpers import api_policy, files, rbac
from helpers.print_style import PrintStyle

OPENAPI_VERSION = "3.1.0"

# Bumped when the shape of the generated document changes in a way a consumer
# would notice. Independent of the product version.
SCHEMA_REVISION = 1

_SECURITY_SESSION = "sessionCookie"
_SECURITY_CSRF = "csrfToken"
_SECURITY_API_KEY = "mcpApiKey"


class EndpointSpec(TypedDict):
    """Everything introspectable about one API endpoint."""

    path: str  # URL path, e.g. "/api/settings_get"
    endpoint: str  # dispatch key, e.g. "settings_get"
    source: str  # "core" | "plugin" | "user"
    plugin: str  # owning plugin for source == "plugin", else ""
    handler: str  # class name
    module: str  # repo-relative file that defines it
    methods: list[str]
    requires_auth: bool
    requires_csrf: bool
    requires_api_key: bool
    requires_loopback: bool
    required_permission: str | None
    permission_source: str  # "handler" | "policy" | "none"
    summary: str
    input_model: Any  # type[BaseModel] | None
    output_model: Any  # type[BaseModel] | None


def _modules_in(folder: Path, recursive: bool = False) -> list[Path]:
    """The handler files in a folder, by their `.py` names, source or compiled."""
    from helpers import modules

    return sorted(Path(p) for p in modules.list_modules(str(folder), recursive=recursive))


def _handler_classes(path: Path):
    """Load the ApiHandler subclasses defined in one file, or [] if it fails.

    A handler that cannot be imported must not take the schema endpoint down with
    it, so failures are reported and skipped.
    """
    from helpers.api import ApiHandler
    from helpers.modules import load_classes_from_file

    try:
        classes = load_classes_from_file(str(path), ApiHandler)
    except Exception as exc:  # noqa: BLE001 - report and continue
        PrintStyle.warning(f"api_schema: could not load {path}: {exc}")
        return []
    # Mirror the dispatch rule: a class that never implemented `process` is a
    # base, not an endpoint, and is answered with 404.
    return [c for c in classes if not getattr(c.process, "__isabstractmethod__", False)]


def _summary_for(handler_cls: type) -> str:
    doc = inspect.getdoc(handler_cls) or ""
    first = doc.strip().split("\n\n", 1)[0].replace("\n", " ").strip()
    return first


def _optional_model(handler_cls: type, name: str):
    getter = getattr(handler_cls, name, None)
    if not callable(getter):
        return None
    try:
        model = getter()
    except Exception:  # noqa: BLE001 - a broken declaration must not break the doc
        return None
    return model or None


def _spec_for(handler_cls: type, endpoint: str, source: str, plugin: str, module: Path) -> EndpointSpec:
    declared = handler_cls.required_permission()
    policy = api_policy.required_permission_for(endpoint)
    if declared:
        permission, permission_source = declared, "handler"
    elif policy:
        permission, permission_source = policy, "policy"
    else:
        permission, permission_source = None, "none"

    try:
        rel = str(module.relative_to(files.get_abs_path("")))
    except ValueError:
        rel = str(module)

    return EndpointSpec(
        path=f"/api/{endpoint}",
        endpoint=endpoint,
        source=source,
        plugin=plugin,
        handler=handler_cls.__name__,
        module=rel,
        methods=sorted(handler_cls.get_methods()),
        requires_auth=bool(handler_cls.requires_auth()),
        requires_csrf=bool(handler_cls.requires_csrf()),
        requires_api_key=bool(handler_cls.requires_api_key()),
        requires_loopback=bool(handler_cls.requires_loopback()),
        required_permission=permission,
        permission_source=permission_source,
        summary=_summary_for(handler_cls),
        input_model=_optional_model(handler_cls, "input_model"),
        output_model=_optional_model(handler_cls, "output_model"),
    )


def collect_endpoints() -> list[EndpointSpec]:
    """Walk the same directories `register_api_route` resolves against.

    Ordering matches dispatch precedence: core, then plugins, then the user
    fallback. A `usr/api/` file only ever shadows a core one, so a duplicate
    endpoint key is reported rather than silently listed twice.
    """
    from helpers import plugins

    specs: dict[str, EndpointSpec] = {}

    def add(spec: EndpointSpec) -> None:
        existing = specs.get(spec["endpoint"])
        if existing:
            PrintStyle.warning(
                f"api_schema: {spec['endpoint']} defined twice "
                f"({existing['module']} and {spec['module']}); keeping the first"
            )
            return
        specs[spec["endpoint"]] = spec

    core_dir = Path(files.get_abs_path(files.API_DIR))
    for file in _modules_in(core_dir):
        for handler_cls in _handler_classes(file):
            add(_spec_for(handler_cls, file.stem, "core", "", file))

    for plugin_dir in sorted(Path(p) for p in plugins.get_plugin_roots()):
        for plugin_path in sorted(plugin_dir.iterdir()) if plugin_dir.is_dir() else []:
            api_dir = plugin_path / "api"
            if not api_dir.is_dir():
                continue
            # rglob, not glob: `_dispatch` splits the path with maxsplit=2, so the
            # handler segment may itself contain slashes. That is how
            # plugins/_ea_connector/api/v1/<name>.py is reachable as
            # /api/plugins/_ea_connector/v1/<name>. Globbing one level deep would
            # silently omit 28 live endpoints, including unauthenticated ones.
            for file in _modules_in(api_dir, recursive=True):
                if file.name == "__init__.py":
                    continue
                rel = file.relative_to(api_dir).with_suffix("")
                endpoint = f"plugins/{plugin_path.name}/{rel.as_posix()}"
                for handler_cls in _handler_classes(file):
                    add(_spec_for(handler_cls, endpoint, "plugin", plugin_path.name, file))

    user_dir = Path(files.get_abs_path(files.USER_DIR, files.API_DIR))
    if user_dir.is_dir():
        for file in _modules_in(user_dir):
            for handler_cls in _handler_classes(file):
                add(_spec_for(handler_cls, file.stem, "user", "", file))

    return [specs[key] for key in sorted(specs)]


def _model_schema(model, components: dict[str, Any]) -> dict[str, Any] | None:
    """Inline a pydantic model's JSON Schema, hoisting its $defs into components."""
    if model is None:
        return None
    try:
        schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
    except Exception as exc:  # noqa: BLE001
        PrintStyle.warning(f"api_schema: {getattr(model, '__name__', model)} schema failed: {exc}")
        return None
    for name, defn in (schema.pop("$defs", None) or {}).items():
        components.setdefault(name, defn)
    name = getattr(model, "__name__", None)
    if name:
        components.setdefault(name, schema)
        return {"$ref": f"#/components/schemas/{name}"}
    return schema


def _operation(spec: EndpointSpec, method: str, components: dict[str, Any]) -> dict[str, Any]:
    security: list[dict[str, list[str]]] = []
    # A required permission implies a session even when the handler's own
    # requires_auth() is False: requires_permission() checks is_authenticated()
    # itself and denies 401 before testing the permission.
    session_required = spec["requires_auth"] or bool(spec["required_permission"])
    if session_required:
        entry: dict[str, list[str]] = {_SECURITY_SESSION: []}
        if spec["requires_csrf"]:
            entry[_SECURITY_CSRF] = []
        security.append(entry)
    if spec["requires_api_key"]:
        security.append({_SECURITY_API_KEY: []})

    request_schema = _model_schema(spec["input_model"], components)
    response_schema = _model_schema(spec["output_model"], components)
    documented = bool(request_schema or response_schema)

    responses: dict[str, Any] = {
        "200": {
            "description": "Success",
            "content": {
                "application/json": {
                    "schema": response_schema or {"type": "object", "additionalProperties": True}
                }
            },
        }
    }
    if session_required:
        responses["401"] = {"description": "Not authenticated"}
    if spec["required_permission"]:
        responses["403"] = {"description": f"Requires the {spec['required_permission']} permission"}
    if spec["requires_loopback"]:
        responses["403"] = {"description": "Callable only from the loopback interface"}
    responses["404"] = {"description": "Endpoint not found, or the referenced object is not visible to you"}
    responses["500"] = {"description": "Unhandled error; body is plain text"}

    operation: dict[str, Any] = {
        "operationId": f"{method.lower()}_{spec['endpoint'].replace('/', '_')}",
        "summary": spec["summary"] or spec["endpoint"],
        "tags": [spec["plugin"] if spec["plugin"] else spec["source"]],
        "responses": responses,
        "security": security,
        "x-handler": spec["handler"],
        "x-module": spec["module"],
        "x-source": spec["source"],
        "x-required-permission": spec["required_permission"],
        "x-permission-source": spec["permission_source"],
        "x-requires-csrf": spec["requires_csrf"],
        "x-requires-loopback": spec["requires_loopback"],
        # "declared" means a handler published a model; "free-form" means the
        # body is undocumented, not that it is empty.
        "x-body-schema": "declared" if documented else "free-form",
    }

    if method in ("POST", "PUT", "PATCH"):
        operation["requestBody"] = {
            "required": False,
            "content": {
                "application/json": {
                    "schema": request_schema or {"type": "object", "additionalProperties": True}
                }
            },
        }
    return operation


def build_openapi(version: str = "") -> dict[str, Any]:
    """Build the OpenAPI document for this instance."""
    specs = collect_endpoints()
    components: dict[str, Any] = {}

    paths: dict[str, Any] = {}
    for spec in specs:
        operations = {
            method.lower(): _operation(spec, method, components) for method in spec["methods"]
        }
        paths[spec["path"]] = operations

    documented = sum(
        1 for s in specs if s["input_model"] is not None or s["output_model"] is not None
    )

    return {
        "openapi": OPENAPI_VERSION,
        "info": {
            "title": "Rezztor EAgent API",
            "version": version or "unversioned",
            "x-schema-revision": SCHEMA_REVISION,
            "description": (
                "Generated from the ApiHandler classes at runtime, so it always "
                "matches the endpoints this instance actually serves.\n\n"
                "Routing and security are authoritative. Request and response "
                "bodies are only authoritative where an operation reports "
                "`x-body-schema: declared`; the rest are free-form objects that "
                "no handler has described yet.\n\n"
                f"{documented} of {len(specs)} endpoints declare a body schema."
            ),
        },
        "servers": [{"url": "/"}],
        "components": {
            "securitySchemes": {
                _SECURITY_SESSION: {
                    "type": "apiKey",
                    "in": "cookie",
                    "name": "session",
                    "description": "Flask session cookie set by POST /login.",
                },
                _SECURITY_CSRF: {
                    "type": "apiKey",
                    "in": "header",
                    "name": "X-CSRF-Token",
                    "description": (
                        "Token from GET /api/csrf_token. Required on every "
                        "authenticated endpoint; the session cookie alone is not enough."
                    ),
                },
                _SECURITY_API_KEY: {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "MCP server token, for the /mcp surface only.",
                },
            },
            "schemas": components,
        },
        "paths": paths,
        "x-permissions": {
            "all": sorted(rbac.ALL_PERMISSIONS),
            "roles": {
                role: {
                    "title": meta["title"],
                    "description": meta["description"],
                    "permissions": sorted(meta["permissions"]),
                }
                for role, meta in sorted(rbac.ROLES.items())
            },
            "default_role": rbac.DEFAULT_ROLE,
        },
    }
