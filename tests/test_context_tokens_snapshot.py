"""The prompt size the interface shows.

The number already existed -- the agent stores it when it builds a prompt, and
ctx_window_get returns it. It travels on the snapshot because that is when it
changes, and because the endpoint returns the entire rendered prompt beside it:
polling that once a turn to read four digits would ship hundreds of kilobytes.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers.state_snapshot import _context_tokens


class _Agent:
    def __init__(self, window):
        self._window = window

    def get_data(self, _key):
        return self._window


class _Context:
    def __init__(self, window, streaming=None):
        self.root_agent = _Agent(window)
        self.streaming_agent = _Agent(streaming) if streaming is not None else None


def test_it_reports_what_the_agent_stored():
    assert _context_tokens(_Context({"text": "...", "tokens": 42300})) == 42300


def test_it_prefers_the_streaming_agent():
    # Mid-run, the streaming agent is the one whose prompt was last built --
    # a subordinate's window, not the root's stale one.
    assert _context_tokens(_Context({"tokens": 100}, streaming={"tokens": 900})) == 900


def test_no_context_is_zero():
    assert _context_tokens(None) == 0


def test_a_conversation_with_no_prompt_yet_is_zero():
    # Before the first turn, and after compaction pops the stored window.
    assert _context_tokens(_Context(None)) == 0


def test_a_malformed_window_is_zero_rather_than_an_error():
    for window in ({}, {"text": "x"}, {"tokens": None}, {"tokens": "many"}, "not a dict", []):
        assert _context_tokens(_Context(window)) == 0


def test_a_negative_count_is_refused():
    assert _context_tokens(_Context({"tokens": -5})) == 0


def test_an_agent_that_raises_does_not_break_the_snapshot():
    """A snapshot that raises is a chat that stops updating. A missing number is
    a blank indicator, which is the far better failure."""

    class Exploding:
        @property
        def streaming_agent(self):
            raise RuntimeError("context torn down mid-poll")

    assert _context_tokens(Exploding()) == 0
