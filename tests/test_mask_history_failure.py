"""A history-masking failure stores a refusal, not the raw content (audit 6, L8)."""

from __future__ import annotations

import types

import extensions.python.hist_add_before._10_mask_content as hook


def test_masking_failure_withholds_the_content(monkeypatch):
    def broken(_context):
        raise OSError("secrets.env unreadable")

    monkeypatch.setattr(hook, "get_secrets_manager", broken)
    data = {"content": {"tool": "net_read", "output": "password hunter2"}}

    hook.MaskHistoryContent(agent=types.SimpleNamespace(context=None)).execute(content_data=data)

    assert data["content"] == hook.WITHHELD
