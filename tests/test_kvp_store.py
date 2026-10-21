"""The generic persistent key/value store.

It had no tests when it was one JSON file per key and a directory glob. It has
some now, because the move to SQL turned the glob into a LIKE, and LIKE has
wildcards of its own that the file version did not have to think about.
"""

from __future__ import annotations

import pytest

from helpers import kvp


@pytest.fixture
def store(database):
    return kvp


def test_a_value_round_trips(store):
    store.set_persistent("alpha", {"a": 1, "nested": [1, 2]})
    assert store.get_persistent("alpha") == {"a": 1, "nested": [1, 2]}


def test_a_missing_key_gives_the_default(store):
    assert store.get_persistent("nothing-here", "fallback") == "fallback"


def test_setting_twice_replaces_rather_than_duplicates(store):
    store.set_persistent("alpha", "first")
    store.set_persistent("alpha", "second")
    assert store.get_persistent("alpha") == "second"
    assert store.find_persistent("alpha") == ["alpha"]


def test_removing_a_key_that_is_not_there_is_not_an_error(store):
    store.remove_persistent("never-existed")


def test_a_star_matches_a_prefix(store):
    store.set_persistent("alpha", 1)
    store.set_persistent("alpha_two", 2)
    store.set_persistent("beta", 3)
    assert store.find_persistent("alpha*") == ["alpha", "alpha_two"]


def test_a_question_mark_matches_one_character(store):
    store.set_persistent("beta", 1)
    store.set_persistent("betaa", 2)
    assert store.find_persistent("bet?") == ["beta"]


def test_a_percent_in_a_key_is_a_percent_and_not_a_wildcard(store):
    """LIKE's wildcards are not the caller's. A key containing a percent sign
    would otherwise match everything, which is the kind of thing that works in
    every test until one key has a percent in it."""
    store.set_persistent("has%percent", 1)
    store.set_persistent("unrelated", 2)
    assert store.find_persistent("has%percent") == ["has%percent"]


def test_an_underscore_in_a_key_is_an_underscore(store):
    """LIKE's single-character wildcard. `alpha_two` must not match `alphaxtwo`."""
    store.set_persistent("alpha_two", 1)
    store.set_persistent("alphaxtwo", 2)
    assert store.find_persistent("alpha_two") == ["alpha_two"]


def test_an_empty_pattern_finds_nothing_rather_than_everything(store):
    store.set_persistent("alpha", 1)
    assert store.find_persistent("") == []
