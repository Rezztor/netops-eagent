"""The memory filter compares, and a memory area is one folder name."""

from __future__ import annotations

import pytest

from helpers import user_scope
from plugins._memory.helpers.memory import Memory, abs_db_dir


def test_a_filter_still_compares_metadata():
    matches = Memory._get_comparator("area == 'main' and score > 0.5")
    assert matches({"area": "main", "score": 0.7}) is True
    assert matches({"area": "other", "score": 0.7}) is False


def test_a_filter_cannot_call_a_method():
    """It runs once per stored row, in the process every account shares, and a
    string method builds a string of any size the caller names."""
    matches = Memory._get_comparator("'x'.rjust(3000000) or area == 'main'")
    assert matches({"area": "main"}) is False


@pytest.mark.parametrize("area", ["../../x", "a/b", "..", "/tmp/x"])
def test_a_memory_area_cannot_name_a_path(area):
    with user_scope.user_scope("alice"):
        with pytest.raises(ValueError, match="Invalid memory area"):
            abs_db_dir(area)
        assert abs_db_dir("default").endswith("usr/users/alice/memory/default")
