"""Contract tests for the plugins_list API payload shapes.

The React frontend sends {builtin, custom} flat in the JSON body; the inherited
inherited contract expects them nested under "filter". The handler accepts
both so the admin plugin list shows bundled plugins either way, and missing
flags default to True (the admin page lists everything by default).
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.mark.asyncio
async def test_plugins_list_accepts_flat_frontend_payload(monkeypatch):
    """The React frontend sends the filter flags flat in the request body."""
    from api.plugins_list import PluginsList
    from helpers import plugins

    calls: list[tuple[bool, bool]] = []

    def fake_list(custom: bool, builtin: bool):
        calls.append((custom, builtin))
        return []

    monkeypatch.setattr(plugins, "get_enhanced_plugins_list", fake_list)

    handler = PluginsList(app=None, thread_lock=threading.RLock())  # type: ignore[arg-type]
    output = await handler.process(
        {"builtin": True, "custom": True},
        request=None,  # type: ignore[arg-type]
    )

    assert output == {"ok": True, "plugins": []}
    assert calls == [(True, True)]


@pytest.mark.asyncio
async def test_plugins_list_accepts_nested_filter_payload(monkeypatch):
    """The inherited shape nests the flags under a "filter" key."""
    from api.plugins_list import PluginsList
    from helpers import plugins

    calls: list[tuple[bool, bool]] = []

    def fake_list(custom: bool, builtin: bool):
        calls.append((custom, builtin))
        return []

    monkeypatch.setattr(plugins, "get_enhanced_plugins_list", fake_list)

    handler = PluginsList(app=None, thread_lock=threading.RLock())  # type: ignore[arg-type]
    output = await handler.process(
        {"filter": {"builtin": False, "custom": True}},
        request=None,  # type: ignore[arg-type]
    )

    assert output == {"ok": True, "plugins": []}
    assert calls == [(True, False)]


@pytest.mark.asyncio
async def test_plugins_list_defaults_to_listing_everything(monkeypatch):
    """No flags in the payload means both bundled and custom plugins."""
    from api.plugins_list import PluginsList
    from helpers import plugins

    calls: list[tuple[bool, bool]] = []

    def fake_list(custom: bool, builtin: bool):
        calls.append((custom, builtin))
        return []

    monkeypatch.setattr(plugins, "get_enhanced_plugins_list", fake_list)

    handler = PluginsList(app=None, thread_lock=threading.RLock())  # type: ignore[arg-type]
    await handler.process(
        {},
        request=None,  # type: ignore[arg-type]
    )

    assert calls == [(True, True)]
