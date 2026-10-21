"""Counting which skills a turn actually read.

The receipt was already computed on every capture and handed to the reviewer,
then discarded. Keeping it is what any later scoring has to rest on, so what
matters here is that it counts the right turns, counts them once, and never
breaks the turn it is counting.
"""

import pytest

from helpers import files
from plugins._ea_auto_skills.helpers import capture, store


@pytest.fixture
def instance(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    (tmp_path / "usr" / "users" / "alice").mkdir(parents=True)
    return tmp_path


def _seed(user="alice", *names):
    ledger = {n: {"created": "2026-09-01T00:00:00Z"} for n in names}
    store.write_ledger(user, ledger)


# --- the ledger side --------------------------------------------------------

def test_a_load_is_counted(instance):
    _seed("alice", "rotate-keys")
    store.note_usage("alice", ["rotate-keys"], completed=True)
    assert store.usage("alice", "rotate-keys")["loads"] == 1
    assert store.usage("alice", "rotate-keys")["loads_completed"] == 1


def test_a_turn_that_did_not_finish_counts_as_a_load_but_not_a_success(instance):
    # Both numbers matter: loaded-and-finished is the signal; loaded-and-not is
    # the counter-signal that later phases need to tell a good skill from a used one.
    _seed("alice", "rotate-keys")
    store.note_usage("alice", ["rotate-keys"], completed=False)
    usage = store.usage("alice", "rotate-keys")
    assert usage["loads"] == 1
    assert usage["loads_completed"] == 0


def test_loads_accumulate_and_record_when(instance):
    _seed("alice", "rotate-keys")
    for _ in range(3):
        store.note_usage("alice", ["rotate-keys"], completed=True)
    usage = store.usage("alice", "rotate-keys")
    assert usage["loads"] == 3
    assert usage["last_loaded"].endswith("Z")


def test_only_this_plugin_s_own_skills_are_counted(instance):
    # A bundled or promoted skill is not in the ledger. Counting it there would
    # invent an entry for something this plugin does not own.
    _seed("alice", "rotate-keys")
    recorded = store.note_usage("alice", ["rotate-keys", "build-skill"], completed=True)
    assert recorded == ["rotate-keys"]
    assert store.usage("alice", "build-skill") == {}


def test_usage_of_an_unknown_skill_is_empty_not_zero(instance):
    _seed("alice")
    assert store.usage("alice", "never-written") == {}


def test_recording_nothing_leaves_the_ledger_alone(instance):
    _seed("alice", "rotate-keys")
    before = store.read_ledger("alice")
    assert store.note_usage("alice", [], completed=True) == []
    assert store.read_ledger("alice") == before


# --- the per-turn side ------------------------------------------------------

class _Message:
    def __init__(self, content, sequence, ai=False):
        self.ai = ai
        self.content = content
        self.sequence = sequence

    def output_text(self):
        return str(self.content)


class _History:
    def __init__(self, messages):
        self._messages = messages

    def all_messages(self):
        return self._messages


class _Context:
    def __init__(self):
        self.id = "ctx-1"
        self._data = {}

    def get_data(self, key):
        return self._data.get(key)

    def set_data(self, key, value):
        self._data[key] = value


class _Agent:
    def __init__(self, loaded, completed=True):
        self.history = _History([_Message({"user_message": "do it"}, 4)])
        self.context = _Context()
        self.data = {capture.TURN_DATA_KEY: {"sequence": 4, "tool_calls": 3, "completed": completed}}
        self._loaded = loaded

    def get_data(self, key):
        return self.data.get(key)

    def set_data(self, key, value):
        self.data[key] = value


@pytest.fixture
def loaded(monkeypatch):
    def use(names):
        monkeypatch.setattr(capture, "loaded_skill_names", lambda agent: list(names))
    return use


def test_a_turn_reports_what_it_loaded(instance, loaded):
    loaded(["rotate-keys"])
    usage = capture.find_turn_usage(_Agent(["rotate-keys"]))
    assert usage is not None
    assert usage.loaded == ["rotate-keys"]
    assert usage.completed is True


def test_the_same_turn_is_counted_once_however_often_the_hook_fires(instance, loaded):
    # process_chain_end fires once per level of the superior chain.
    loaded(["rotate-keys"])
    agent = _Agent(["rotate-keys"])
    seen = []
    for _ in range(3):
        usage = capture.find_turn_usage(agent)
        if usage:
            seen.append(usage)
            capture.mark_usage_recorded(agent, usage.sequence)
    assert len(seen) == 1


def test_a_turn_that_loaded_nothing_is_stamped_anyway(instance, loaded):
    # Otherwise every repeated hook fire re-walks the history for the same turn.
    loaded([])
    agent = _Agent([])
    assert capture.find_turn_usage(agent) is None
    assert agent.context.get_data(capture.USAGE_SEQUENCE_KEY) == 4


def test_an_unfinished_turn_is_still_a_load(instance, loaded):
    loaded(["rotate-keys"])
    usage = capture.find_turn_usage(_Agent(["rotate-keys"], completed=False))
    assert usage is not None
    assert usage.completed is False


def test_usage_is_tracked_separately_from_capture(instance, loaded):
    """A turn already captured for review must still have its loads counted, and
    the two stamps must not interfere."""
    loaded(["rotate-keys"])
    agent = _Agent(["rotate-keys"])
    capture.mark_captured(agent, 4)
    assert capture.find_candidate(agent, 2) is None      # already reviewed
    assert capture.find_turn_usage(agent) is not None    # not yet counted
