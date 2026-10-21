import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api.plugins import Plugins
from helpers import files, plugins


def test_advanced_plugin_toggle_modal_was_removed():
    assert not (
        PROJECT_ROOT / "webui/components/plugins/toggle/plugin-toggle-advanced.html"
    ).exists()


def test_list_toggle_state_is_global_even_when_scoped_rules_exist(monkeypatch):
    monkeypatch.setattr(
        plugins,
        "get_plugin_meta",
        lambda _plugin_name: SimpleNamespace(always_enabled=False),
    )
    monkeypatch.setattr(
        plugins,
        "get_plugin_roots",
        lambda _plugin_name: ["plugins/example", "usr/plugins/example"],
    )
    monkeypatch.setattr(
        plugins,
        "determined_toggle_from_paths",
        lambda _default, _paths: False,
    )

    def fail_on_scoped_lookup(*_args, **_kwargs):
        raise AssertionError("Plugin list toggle state should not inspect scoped rules")

    monkeypatch.setattr(plugins, "find_plugin_assets", fail_on_scoped_lookup)

    assert plugins.get_toggle_state("example") == "disabled"


def test_always_enabled_plugin_ignores_disable_files_at_runtime(monkeypatch):
    monkeypatch.setattr(plugins.cache, "get", lambda *args, **kwargs: None)
    monkeypatch.setattr(plugins.cache, "add", lambda *args, **kwargs: None)
    monkeypatch.setattr(plugins, "get_plugins_list", lambda: ["_required"])
    monkeypatch.setattr(
        plugins,
        "get_plugin_meta",
        lambda _plugin_name: SimpleNamespace(always_enabled=True),
    )

    def fail_on_toggle_lookup(*_args, **_kwargs):
        raise AssertionError("always-enabled plugins must ignore toggle files")

    monkeypatch.setattr(plugins, "determined_toggle_from_paths", fail_on_toggle_lookup)

    assert plugins.get_enabled_plugins(None) == ["_required"]


def test_always_enabled_plugin_rejects_disable_attempt(monkeypatch):
    monkeypatch.setattr(
        plugins,
        "get_plugin_meta",
        lambda _plugin_name: SimpleNamespace(always_enabled=True),
    )

    with pytest.raises(ValueError, match="always enabled"):
        plugins.toggle_plugin.__wrapped__("_required", False)


def test_plugin_api_returns_bad_request_for_rejected_disable(monkeypatch):
    def reject(*_args, **_kwargs):
        raise ValueError('Plugin "_required" is always enabled.')

    monkeypatch.setattr(plugins, "toggle_plugin", reject)

    response = Plugins._toggle_plugin.__wrapped__(
        object.__new__(Plugins),
        {"plugin_name": "_required", "enabled": False},
    )

    assert response.status_code == 400
    assert "always enabled" in response.get_data(as_text=True)


def test_scoped_plugin_watchdogs_skip_frontend_reload(monkeypatch):
    handlers = {}
    changes = []
    monkeypatch.setattr(
        plugins.watchdog,
        "add_watchdog",
        lambda **kwargs: handlers.setdefault(kwargs["id"], kwargs["handler"]),
    )
    monkeypatch.setattr(
        plugins,
        "after_plugin_change",
        lambda names=None, python_change=False, frontend_reload=True: changes.append(
            (names, python_change, frontend_reload)
        ),
    )

    plugins.register_watchdogs()
    handlers["plugins_agents"](
        [["/tmp/usr/agents/custom/plugins/_code_execution/.toggle-0", "delete"]]
    )
    handlers["plugins_roots"](
        [["/tmp/plugins/_code_execution/.toggle-0", "delete"]]
    )

    assert changes == [
        (["_code_execution"], False, False),
        (["_code_execution"], False, True),
    ]


def test_toggle_plugin_writes_project_scope_file_immediately(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    changes = []
    monkeypatch.setattr(
        plugins,
        "after_plugin_change",
        lambda names, **kwargs: changes.append((names, kwargs)),
    )
    monkeypatch.setitem(
        sys.modules,
        "helpers.projects",
        SimpleNamespace(
            get_project_meta=lambda name, *sub_dirs: files.get_abs_path(
                "usr/projects",
                name,
                ".eaproj",
                *sub_dirs,
            )
        ),
    )

    plugins.toggle_plugin.__wrapped__("example", False, project_name="alpha")

    scoped_plugin_dir = (
        tmp_path / "usr/projects/alpha/.eaproj/plugins/example"
    )
    assert (scoped_plugin_dir / ".toggle-0").exists()
    assert not (scoped_plugin_dir / ".toggle-1").exists()

    plugins.toggle_plugin.__wrapped__("example", True, project_name="alpha")

    assert (scoped_plugin_dir / ".toggle-1").exists()
    assert not (scoped_plugin_dir / ".toggle-0").exists()
    assert changes == [
        (["example"], {"frontend_reload": False}),
        (["example"], {"frontend_reload": False}),
    ]
