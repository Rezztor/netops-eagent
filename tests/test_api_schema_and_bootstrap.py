"""Stage 2 contract tests: the generated OpenAPI document and /api/bootstrap.

The point of these is drift. The schema is generated from the handler classes, so
it cannot go stale on its own -- but it can start *describing the wrong thing* if
the generator's idea of how dispatch works diverges from `register_api_route`.
And `/api/bootstrap` exists to replace nine values that `serve_index` substitutes
into index.html, so it is only correct for as long as it covers all nine.
"""

from __future__ import annotations

import ast
import shutil
import sys
import threading
from pathlib import Path

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers import api_policy, api_schema, rbac  # noqa: E402
from helpers.api import PASSWORD_CHANGE_EXEMPT_ENDPOINTS  # noqa: E402


@pytest.fixture(scope="module")
def document():
    return api_schema.build_openapi(version="test")


@pytest.fixture(scope="module")
def endpoints():
    return api_schema.collect_endpoints()


# --------------------------------------------------------------------------
# document shape
# --------------------------------------------------------------------------


def test_document_is_structurally_a_valid_openapi_document(document):
    assert document["openapi"] == api_schema.OPENAPI_VERSION
    assert document["info"]["title"]
    assert document["paths"]

    for path, operations in document["paths"].items():
        assert path.startswith("/api/"), path
        assert operations, f"{path} has no operations"
        for method, op in operations.items():
            assert method in ("get", "post", "put", "patch", "delete"), (path, method)
            assert op["operationId"], path
            assert op["summary"], path
            assert "200" in op["responses"], path
            assert op["x-body-schema"] in ("declared", "free-form"), path


def test_operation_ids_are_unique(document):
    seen = [op["operationId"] for ops in document["paths"].values() for op in ops.values()]
    duplicates = {name for name in seen if seen.count(name) > 1}
    assert not duplicates, f"duplicate operationIds: {sorted(duplicates)}"


def test_every_schema_ref_resolves(document):
    """A $ref pointing at a component that was never hoisted makes the document
    unusable in any client generator, and nothing else would catch it."""
    components = document["components"]["schemas"]
    missing = []

    def walk(node, where):
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
                name = ref.rsplit("/", 1)[-1]
                if name not in components:
                    missing.append((where, ref))
            for key, value in node.items():
                walk(value, f"{where}.{key}")
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{where}[{i}]")

    walk(document["paths"], "paths")
    walk(components, "components")
    assert not missing, f"unresolved refs: {missing}"


# --------------------------------------------------------------------------
# the schema must describe what dispatch actually enforces
# --------------------------------------------------------------------------


def test_declared_permissions_match_the_policy_table(endpoints):
    """The generator resolves permissions the same way register_api_route does:
    a handler's own declaration wins, otherwise the central policy table."""
    for spec in endpoints:
        handler_declared = spec["permission_source"] == "handler"
        from_policy = api_policy.required_permission_for(spec["endpoint"])
        if handler_declared:
            continue
        assert spec["required_permission"] == from_policy, spec["endpoint"]


def test_every_policy_entry_maps_to_a_real_endpoint(endpoints):
    """An entry for an endpoint that no longer exists is a gate protecting
    nothing, and reads as coverage it does not provide."""
    known = {spec["endpoint"] for spec in endpoints}
    orphans = sorted(set(api_policy.API_PERMISSIONS) - known)
    assert not orphans, f"policy entries with no handler: {orphans}"


def test_referenced_permissions_all_exist(endpoints):
    for spec in endpoints:
        if spec["required_permission"]:
            assert spec["required_permission"] in rbac.ALL_PERMISSIONS, spec["endpoint"]


def test_a_required_permission_implies_a_session_in_the_document(document, endpoints):
    """requires_permission() checks is_authenticated() itself and denies 401
    before testing the permission, so an endpoint gated only by the policy table
    is still authenticated."""
    by_endpoint = {spec["endpoint"]: spec for spec in endpoints}
    for path, operations in document["paths"].items():
        endpoint = path[len("/api/"):]
        spec = by_endpoint.get(endpoint)
        if not spec or not spec["required_permission"]:
            continue
        for op in operations.values():
            names = {name for entry in op["security"] for name in entry}
            assert "sessionCookie" in names, f"{endpoint} is gated but documented as anonymous"
            assert "401" in op["responses"], endpoint


def test_unauthenticated_endpoints_are_gated_some_other_way(endpoints):
    """Every endpoint that skips requires_auth must be reachable only via an API
    key, the loopback interface, a permission check, or be deliberately public."""
    deliberately_public = {
        "health",
        "logout",
        # A cold frontend must be able to ask whether it has a session before it
        # can know to render the login page; the anonymous payload is minimal.
        "bootstrap",
        # Password reset. The caller is someone who cannot sign in, so there is
        # no session to check them against -- that is the entire situation, and
        # any gate here would gate out the only people who need it.
        #
        # What stands in for authentication: the request endpoint answers
        # identically whether or not the account exists, so it cannot be used to
        # discover who holds one, and it will only ever mail an address already
        # on an account. The confirm endpoint requires a token that was sent to
        # that address, is stored only as a hash, expires, and is spent on use.
        "password_reset_request",
        "password_reset_confirm",
        # Readiness, for a monitor to scrape. Authenticating reads the session
        # from PostgreSQL, so an authenticated readiness check fails in the auth
        # layer during exactly the outage it exists to report -- verified by
        # stopping the database and watching the first version answer 500
        # instead of 503. What stands in for a gate is the body: `ok` and `ms`
        # per check and nothing else, no exception text and no host names, so it
        # says no more than the status line already does.
        "ready",
    }
    for spec in endpoints:
        if spec["requires_auth"] or spec["endpoint"] in deliberately_public:
            continue
        gated = (
            spec["requires_api_key"]
            or spec["requires_loopback"]
            or bool(spec["required_permission"])
        )
        assert gated, f"{spec['endpoint']} is reachable with no authentication at all"


# --------------------------------------------------------------------------
# /api/bootstrap
# --------------------------------------------------------------------------


# What /api/bootstrap must carry.
#
# This began as a parity check against the values serve_index substituted into
# the inherited index.html. That interface is gone, so the list is now the
# contract in its own right: a cold frontend cannot start without these.
REQUIRED_BOOTSTRAP_KEYS = {
    "version",
    "authenticated",
    "user",
    "permissions",
    "runtime",
    "csrf_token",
    "preferences",
}


@pytest.mark.asyncio
async def test_bootstrap_authenticated_payload_matches_its_declared_schema(monkeypatch):
    """The declared output_model is documentation, not validation, so nothing at
    runtime would notice it drifting from the real payload. This does."""
    from api.bootstrap import Bootstrap, BootstrapResponse
    from helpers import login

    monkeypatch.setattr(
        login,
        "current_user",
        lambda: {
            "id": "u1",
            "username": "someone",
            "display_name": "Someone",
            "email": "someone@example.com",
            "role": rbac.ROLE_POWER,
            "must_change_password": False,
        },
    )

    app = Flask("bootstrap-schema-test")
    app.secret_key = "test-secret"
    handler = Bootstrap(app, threading.RLock())
    with app.test_request_context("/api/bootstrap"):
        payload = await handler.process({}, None)  # type: ignore[arg-type]

    assert payload["authenticated"] is True
    parsed = BootstrapResponse.model_validate(payload)
    assert parsed.user is not None
    assert parsed.user.role == rbac.ROLE_POWER
    assert parsed.permissions == sorted(rbac.get_permissions(rbac.ROLE_POWER))
    assert parsed.runtime is not None
    assert parsed.preferences is not None


@pytest.mark.asyncio
async def test_bootstrap_tells_an_anonymous_caller_nothing_about_the_instance(monkeypatch):
    """It answers while signed out by design, so the payload must stay minimal:
    only whether there is a session, and a version already public on /api/health."""
    from api.bootstrap import Bootstrap, BootstrapResponse
    from helpers import login

    monkeypatch.setattr(login, "current_user", lambda: None)

    app = Flask("bootstrap-anon-test")
    app.secret_key = "test-secret"
    handler = Bootstrap(app, threading.RLock())
    with app.test_request_context("/api/bootstrap"):
        payload = await handler.process({}, None)  # type: ignore[arg-type]

    assert payload["authenticated"] is False
    assert set(payload) == {"ok", "authenticated", "version"}
    for leaked in ("user", "permissions", "runtime", "preferences", "webui_extensions"):
        assert leaked not in payload, f"anonymous bootstrap leaked {leaked}"
    BootstrapResponse.model_validate(payload)


def test_bootstrap_is_reachable_during_a_forced_password_change():
    """Otherwise the frontend cannot load far enough to render the screen that
    lets the user out of the forced change."""
    assert "bootstrap" in PASSWORD_CHANGE_EXEMPT_ENDPOINTS


def test_bootstrap_does_not_require_csrf_but_openapi_does():
    """A cold frontend has no CSRF token yet -- /api/csrf_token itself requires a
    session. Bootstrap is a read-only GET, so it is safe without one."""
    from api.bootstrap import Bootstrap
    from api.openapi import OpenApi

    assert Bootstrap.requires_csrf() is False
    assert Bootstrap.requires_auth() is False
    assert Bootstrap.get_methods() == ["GET"]

    assert OpenApi.requires_auth() is True
    assert OpenApi.requires_csrf() is True
    assert OpenApi.required_permission() == rbac.PERM_SETTINGS_MANAGE


def test_openapi_endpoint_is_administrator_only(document):
    op = document["paths"]["/api/openapi"]["get"]
    assert op["x-required-permission"] == rbac.PERM_SETTINGS_MANAGE
    assert "403" in op["responses"]


# --------------------------------------------------------------------------
# gaps the generated schema exposed
# --------------------------------------------------------------------------


def test_endpoints_that_manage_plugins_or_skills_require_a_permission(endpoints):
    """Installing or modifying a plugin installs executable code into a product
    that runs code.

    This began as a fixed list, after the generated schema showed that
    api_policy held only core endpoint names and left plugin routes ungated --
    plugin_install was reachable by the `user` role, whose sole permission is
    chat.use. Every plugin that exposed such a route has since been removed, so
    the rule is stated as a rule: anything whose name says it manages plugins or
    skills must carry a permission, wherever it lives.
    """
    managing = ("plugin_install", "plugin_validator", "plugin_scan", "skills_import",
                "skills_scan", "plugin_update")
    ungated = [
        spec["endpoint"]
        for spec in endpoints
        if any(word in spec["endpoint"] for word in managing)
        and not spec["required_permission"]
    ]
    assert not ungated, f"plugin/skill management endpoints with no permission: {ungated}"


def test_the_schema_walks_nested_plugin_api_directories(tmp_path):
    """_dispatch splits the path with maxsplit=2, so a handler segment may contain
    slashes: plugins/<name>/api/sub/x.py is served at /api/plugins/<name>/sub/x.
    A single-level glob omits those silently -- it hid 28 live endpoints,
    including unauthenticated ones, until this was caught.

    Builds its own nested plugin rather than asserting against a bundled one, so
    removing a plugin cannot turn this into a test of nothing.
    """
    from helpers import files

    root = Path(files.get_abs_path(files.USER_DIR, files.PLUGINS_DIR))
    plugin = root / "_schema_nesting_probe"
    nested_dir = plugin / "api" / "deep"
    try:
        nested_dir.mkdir(parents=True, exist_ok=True)
        (plugin / "plugin.yaml").write_text("name: _schema_nesting_probe\n", encoding="utf-8")
        (nested_dir / "probe.py").write_text(
            "from helpers.api import ApiHandler, Input, Output, Request\n"
            "\n"
            "\n"
            "class Probe(ApiHandler):\n"
            '    """Nested probe handler."""\n'
            "\n"
            "    async def process(self, input: Input, request: Request) -> Output:\n"
            "        return {}\n",
            encoding="utf-8",
        )
        collected = {spec["endpoint"] for spec in api_schema.collect_endpoints()}
        assert "plugins/_schema_nesting_probe/deep/probe" in collected
    finally:
        shutil.rmtree(plugin, ignore_errors=True)


def test_abstract_base_classes_are_not_published_as_endpoints(endpoints):
    """ApiHandler is not an ABC, so @abstractmethod does not prevent a base class
    from being instantiated and answering with null. plugins/_ea_connector/api/
    v1/base.py did exactly that, unauthenticated, because the base it defines
    declares requires_auth() False."""
    from helpers.api import ApiHandler

    assert getattr(ApiHandler.process, "__isabstractmethod__", False), (
        "process is no longer abstract; the dispatch guard that relies on it is now inert"
    )
    for spec in endpoints:
        module = PROJECT_ROOT / spec["module"]
        source = module.read_text(encoding="utf-8") if module.is_file() else ""
        assert "class ApiHandler" not in source or spec["module"] == "helpers/api.py", spec["module"]


def test_bootstrap_carries_everything_a_cold_start_needs():
    """No longer a comparison against the old interface -- that is gone. These
    are the fields the application cannot start without."""
    from api.bootstrap import BootstrapResponse

    declared = set(BootstrapResponse.model_fields)
    missing = sorted(REQUIRED_BOOTSTRAP_KEYS - declared)
    assert not missing, f"bootstrap no longer declares: {missing}"
