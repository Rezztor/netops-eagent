"""The dashboard's area filter is a name, not part of an expression.

It was formatted into `area == '<area>'` and evaluated, so a quote in the area
rewrote the filter the evaluator ran.
"""

from __future__ import annotations

import asyncio
import threading

from flask import Flask


def test_an_area_with_a_quote_is_refused_before_any_search(monkeypatch):
    from plugins._memory.api import memory_dashboard

    filters = []

    class FakeMemory:
        async def search_similarity_threshold(self, query, limit, threshold, filter=""):
            filters.append(filter)
            return []

    async def get_by_subdir(*_args, **_kwargs):
        return FakeMemory()

    monkeypatch.setattr(memory_dashboard.Memory, "get_by_subdir", get_by_subdir)
    handler = memory_dashboard.MemoryDashboard(Flask("memory"), threading.RLock())

    refused = asyncio.run(handler._search_memories({"search": "x", "area": "' or 1=='1"}))
    assert refused["success"] is False
    assert filters == []

    asyncio.run(handler._search_memories({"search": "x", "area": "main"}))
    assert filters == ["area == 'main'"]
