"""What counts as a task worth learning from.

process_chain_end is a blunt signal: it fires once per level of the superior
chain, and not at all when the monologue raised. Everything here pins the
guards that turn it into a usable one.
"""

import pytest

from plugins._ea_auto_skills.helpers import capture


class _FakeMessage:
    """A history entry, shaped the way the real ones are.

    The content shape is the point. hist_add_user_message renders
    fw.user_message.md into a dict with a "user_message" key, while
    hist_add_tool_result stores {"tool_name", "tool_result"} -- and both are
    written with ai=False. A fake that used bare strings hid a real bug here:
    the turn boundary read as "the last non-AI message" moved on every tool
    call, so the counters reset and no turn was ever substantial.
    """

    def __init__(self, content, sequence, *, ai=False):
        self.ai = ai
        self.content = content
        self.sequence = sequence

    def output_text(self):
        if isinstance(self.content, dict):
            return " ".join(str(v) for v in self.content.values())
        return str(self.content)


def _user(text, sequence):
    return _FakeMessage({"user_message": text}, sequence)


def _tool_result(tool, result, sequence):
    return _FakeMessage({"tool_name": tool, "tool_result": result}, sequence)


def _assistant(text, sequence):
    return _FakeMessage(text, sequence, ai=True)


class _FakeHistory:
    def __init__(self, messages):
        self._messages = messages

    def all_messages(self):
        return self._messages


class _FakeContext:
    def __init__(self):
        self.id = "ctx-1"
        self.data = {}

    def get_data(self, key):
        return self.data.get(key)

    def set_data(self, key, value):
        self.data[key] = value


class _FakeAgent:
    def __init__(self, messages):
        self.history = _FakeHistory(messages)
        self.context = _FakeContext()
        self.data = {}

    def get_data(self, key):
        return self.data.get(key)

    def set_data(self, key, value):
        self.data[key] = value


class _Response:
    def __init__(self, break_loop=False):
        self.break_loop = break_loop


def _agent():
    return _FakeAgent([
        _user("Rotate the staging key.", 4),
        _assistant("Ran the rotation.", 5),
    ])


def _complete_turn(agent, tool_calls=2):
    """Run tools the way the loop does: each one appends its result to history
    before the next call is recorded."""
    sequence = max(m.sequence for m in agent.history._messages)
    for index in range(tool_calls):
        capture.record_tool_outcome(agent, _Response())
        sequence += 1
        agent.history._messages.append(
            _tool_result("text_editor", f"result {index}", sequence)
        )
    capture.record_tool_outcome(agent, _Response(break_loop=True))


# --- the completion signal --------------------------------------------------

def test_the_closing_response_tool_is_not_counted_as_work():
    agent = _agent()
    _complete_turn(agent, tool_calls=2)
    turn = agent.get_data(capture.TURN_DATA_KEY)
    assert turn["tool_calls"] == 2
    assert turn["completed"] is True


def test_a_turn_that_never_closed_is_not_a_candidate():
    """The hook does not fire when the monologue raised, but it does fire on
    paths that end without a response tool. Neither is a completed task."""
    agent = _agent()
    capture.record_tool_outcome(agent, _Response())
    capture.record_tool_outcome(agent, _Response())
    assert capture.find_candidate(agent, 2) is None


def test_no_recorded_turn_at_all_is_not_a_candidate():
    assert capture.find_candidate(_agent(), 2) is None


def test_counters_reset_when_a_new_turn_starts():
    agent = _agent()
    _complete_turn(agent, tool_calls=3)
    agent.history._messages.append(_user("Now do it in prod.", 20))
    capture.record_tool_outcome(agent, _Response())
    turn = agent.get_data(capture.TURN_DATA_KEY)
    assert turn["sequence"] == 20
    assert turn["tool_calls"] == 1
    assert turn["completed"] is False


def test_tool_results_in_history_do_not_move_the_turn_boundary():
    """Regression, found by running a real turn rather than a fake one.

    hist_add_tool_result stores its result with ai=False, exactly as a user
    message is stored. Reading the turn boundary as "the most recent non-AI
    message" therefore moved it on every tool call, blanking the counters each
    time. A live turn that made two tool calls and finished reported
    tool_calls: 0, so nothing was ever captured and the feature silently did
    nothing at all.
    """
    agent = _FakeAgent([_user("Compare these two files.", 4)])

    capture.record_tool_outcome(agent, _Response())
    agent.history._messages.append(_tool_result("text_editor", "file one", 5))
    capture.record_tool_outcome(agent, _Response())
    agent.history._messages.append(_tool_result("text_editor", "file two", 6))
    capture.record_tool_outcome(agent, _Response(break_loop=True))

    turn = agent.get_data(capture.TURN_DATA_KEY)
    assert turn["sequence"] == 4, "the boundary followed the tool results"
    assert turn["tool_calls"] == 2, "the counter was reset by each tool result"
    assert turn["completed"] is True

    candidate = capture.find_candidate(agent, 2)
    assert candidate is not None
    assert "file two" in candidate.evidence, "tool results are the evidence"


def test_an_assistant_message_does_not_move_the_boundary_either():
    agent = _FakeAgent([_user("Do the thing.", 4)])
    capture.record_tool_outcome(agent, _Response())
    agent.history._messages.append(_assistant("Thinking about it.", 5))
    capture.record_tool_outcome(agent, _Response())
    capture.record_tool_outcome(agent, _Response(break_loop=True))

    assert agent.get_data(capture.TURN_DATA_KEY)["tool_calls"] == 2


# --- the criteria -----------------------------------------------------------

def test_a_short_task_is_not_worth_generalising():
    agent = _agent()
    _complete_turn(agent, tool_calls=1)
    assert capture.find_candidate(agent, 2) is None


def test_a_substantial_completed_task_is_a_candidate():
    agent = _agent()
    _complete_turn(agent, tool_calls=2)
    candidate = capture.find_candidate(agent, 2)
    assert candidate is not None
    assert candidate.tool_calls == 2
    assert candidate.sequence == 4
    assert candidate.context_id == "ctx-1"
    assert "Rotate the staging key." in candidate.evidence


def test_evidence_covers_the_completed_turn_and_not_the_chat_before_it():
    agent = _FakeAgent([
        _user("An older question.", 1),
        _assistant("An older answer.", 2),
        _user("Rotate the staging key.", 4),
        _assistant("Ran the rotation.", 5),
    ])
    _complete_turn(agent, tool_calls=2)
    candidate = capture.find_candidate(agent, 2)
    assert "An older question." not in candidate.evidence
    assert "Rotate the staging key." in candidate.evidence


# --- idempotency ------------------------------------------------------------

def test_the_same_turn_is_captured_once_however_often_the_hook_fires():
    """_process_chain calls the hook inside its own recursion, so a chain three
    levels deep fires it three times for one completed task."""
    agent = _agent()
    _complete_turn(agent, tool_calls=2)

    captured = []
    for _ in range(3):
        candidate = capture.find_candidate(agent, 2)
        if candidate:
            capture.mark_captured(agent, candidate.sequence)
            captured.append(candidate)

    assert len(captured) == 1


def test_a_later_turn_is_captured_again():
    agent = _agent()
    _complete_turn(agent, tool_calls=2)
    capture.mark_captured(agent, capture.find_candidate(agent, 2).sequence)

    agent.history._messages.append(_user("Do the next one.", 20))
    _complete_turn(agent, tool_calls=2)
    assert capture.find_candidate(agent, 2) is not None


# --- evidence bounds --------------------------------------------------------

def test_a_long_transcript_is_truncated_in_the_middle():
    agent = _FakeAgent([
        _user("START" + "x" * capture.MAX_EVIDENCE_CHARS + "END", 1),
    ])
    _complete_turn(agent, tool_calls=2)
    evidence = capture.find_candidate(agent, 2).evidence

    # Both ends are what make a trajectory legible: what was asked, and how it
    # turned out. The middle is what gets dropped.
    assert len(evidence) <= capture.MAX_EVIDENCE_CHARS
    assert evidence.startswith("START")
    assert evidence.endswith("text_editor result 1")
    assert "omitted" in evidence
