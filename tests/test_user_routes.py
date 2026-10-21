import threading

from flask import Flask

from helpers import cache, files, login, plugins, subagents
from helpers.api import CACHE_AREA, register_api_route
from helpers.ui_server import UiServerRuntime


WEBUI_MANIFEST_CACHE_AREA = "webui_extension_manifest(extensions)(plugins)"


def _new_app(name: str) -> Flask:
    app = Flask(name, static_folder=None)
    app.secret_key = "test-secret"
    return app


def _api_handler_source(source: str) -> str:
    return f"""from helpers.api import ApiHandler


class Handler(ApiHandler):
    @classmethod
    def get_methods(cls):
        return ["GET"]

    async def process(self, input, request):
        return {{"source": {source!r}}}
"""


def test_http_dispatches_contained_user_api_handler(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    user_api_dir = tmp_path / "usr" / "api"
    user_api_dir.mkdir(parents=True)
    handler_source = _api_handler_source("user")
    (user_api_dir / "ping.py").write_text(handler_source, encoding="utf-8")
    (tmp_path / "usr" / "escaped.py").write_text(
        handler_source, encoding="utf-8"
    )
    authenticated = {"value": False}
    monkeypatch.setattr(login, "is_authenticated", lambda: authenticated["value"])

    cache.clear(CACHE_AREA)
    try:
        app = _new_app("test_user_api_route")
        app.add_url_rule("/", "serve_index", lambda: "")
        app.add_url_rule("/login", "login_handler", lambda: "")
        register_api_route(app, threading.RLock())
        client = app.test_client()

        # /api/ paths answer with 401 JSON rather than a redirect, so XHR
        # callers get a status they can act on.
        assert client.get("/api/ping").status_code == 401
        authenticated["value"] = True
        with client.session_transaction() as session:
            session["csrf_token"] = "csrf-token"
        response = client.get("/api/ping", headers={"X-CSRF-Token": "csrf-token"})
        assert response.status_code == 200
        assert response.get_json() == {"source": "user"}

        with app.test_request_context("/api/../escaped", method="GET"):
            denied = app.ensure_sync(app.view_functions["api_dispatch"])("../escaped")
        assert denied.status_code == 404
    finally:
        cache.clear(CACHE_AREA)


def test_existing_api_sources_keep_precedence(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    monkeypatch.setattr(login, "is_authenticated", lambda: True)

    builtin_file = tmp_path / "api" / "shared.py"
    builtin_file.parent.mkdir(parents=True)
    builtin_file.write_text(_api_handler_source("builtin"), encoding="utf-8")

    user_api_dir = tmp_path / "usr" / "api"
    (user_api_dir / "plugins" / "demo").mkdir(parents=True)
    (user_api_dir / "shared.py").write_text(
        _api_handler_source("user"), encoding="utf-8"
    )
    (user_api_dir / "plugins" / "demo" / "ping.py").write_text(
        _api_handler_source("user"), encoding="utf-8"
    )

    plugin_dir = tmp_path / "plugins" / "demo"
    (plugin_dir / "api").mkdir(parents=True)
    (plugin_dir / "api" / "ping.py").write_text(
        _api_handler_source("plugin"), encoding="utf-8"
    )
    monkeypatch.setattr(
        plugins,
        "find_plugin_dir",
        lambda name: str(plugin_dir) if name == "demo" else None,
    )

    cache.clear(CACHE_AREA)
    try:
        app = _new_app("test_existing_api_precedence")
        app.add_url_rule("/", "serve_index", lambda: "")
        app.add_url_rule("/login", "login_handler", lambda: "")
        register_api_route(app, threading.RLock())
        client = app.test_client()
        with client.session_transaction() as session:
            session["csrf_token"] = "csrf-token"
        headers = {"X-CSRF-Token": "csrf-token"}

        assert client.get("/api/shared", headers=headers).get_json() == {
            "source": "builtin"
        }
        assert client.get("/api/plugins/demo/ping", headers=headers).get_json() == {
            "source": "plugin"
        }
    finally:
        cache.clear(CACHE_AREA)




def test_an_endpoint_has_one_spelling_and_keeps_its_method_gate(tmp_path, monkeypatch) -> None:
    """The permission table is looked up by string and the handler by file.

    `/api/./settings_get` reached the same file as `/api/settings_get` and
    matched no entry, so an account holding only `chat.use` ran it. And the
    method check was made on the way into the handler cache, so it stopped
    applying after the first request.
    """
    from helpers import api_policy

    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    monkeypatch.setattr(login, "is_authenticated", lambda: True)
    monkeypatch.setattr(login, "must_change_password", lambda: False)
    monkeypatch.setattr(login, "has_permission", lambda _permission: False)
    monkeypatch.setitem(api_policy.API_PERMISSIONS, "guarded", "settings.manage")
    monkeypatch.setitem(api_policy.API_PERMISSIONS, "plugins/demo/guarded", "settings.manage")

    (tmp_path / "api").mkdir()
    (tmp_path / "api" / "guarded.py").write_text(_api_handler_source("builtin"), encoding="utf-8")
    (tmp_path / "api" / "open.py").write_text(_api_handler_source("builtin"), encoding="utf-8")
    plugin_dir = tmp_path / "plugins" / "demo"
    (plugin_dir / "api").mkdir(parents=True)
    (plugin_dir / "api" / "guarded.py").write_text(_api_handler_source("plugin"), encoding="utf-8")
    monkeypatch.setattr(
        plugins, "find_plugin_dir", lambda name: str(plugin_dir) if name == "demo" else None
    )

    cache.clear(CACHE_AREA)
    try:
        app = _new_app("test_api_one_spelling")
        app.add_url_rule("/", "serve_index", lambda: "")
        app.add_url_rule("/login", "login_handler", lambda: "")
        register_api_route(app, threading.RLock())
        client = app.test_client()
        with client.session_transaction() as session:
            session["csrf_token"] = "csrf-token"
        headers = {"X-CSRF-Token": "csrf-token"}

        assert client.get("/api/guarded", headers=headers).status_code == 403
        assert client.get("/api/plugins/demo/guarded", headers=headers).status_code == 403
        for alias in (
            "/api/./guarded",
            "/api/%2e/guarded",
            "/api/plugins/demo/./guarded",
            "/api/plugins/demo/%2e/guarded",
            "/api/plugins/./demo/guarded",
            "/api/plugins/demo/../api/guarded",
        ):
            assert client.get(alias, headers=headers).status_code == 404, alias

        # GET-only, and still GET-only once the handler is cached.
        assert client.get("/api/open", headers=headers).status_code == 200
        assert client.post("/api/open", headers=headers).status_code == 405
    finally:
        cache.clear(CACHE_AREA)
