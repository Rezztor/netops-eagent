"""Deciding whether a finished turn is worth learning from, and what to send.

Nothing here calls a model or writes a file. It answers one question -- did the
user just complete a substantial task, and if so what is the evidence -- so the
extensions stay thin and the decision stays testable.

Two properties matter and neither is free:

Completion is recorded, not inferred. process_chain_end sits inside the try in
AgentContext._process_chain, so it does not fire at all when the monologue
raises; "the hook ran" therefore says nothing about success. The tool_execute_after
extension records the closing break_loop explicitly and that flag is what gates.

Capture is idempotent per turn. _process_chain is recursive and calls the hook
inside the recursive body, so it fires once per level of the superior chain. The
sequence number of the turn is stamped on the context once captured.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent import Agent

# Per-turn counters, on the agent.
TURN_DATA_KEY = "auto_skills_turn"
# Sequence of the last turn captured, on the context.
CAPTURED_SEQUENCE_KEY = "auto_skills_captured_sequence"
# Sequence of the last turn whose skill loads were recorded. Separate from the
# capture stamp on purpose: a turn too short to review still loaded what it
# loaded, and that is the fact being counted.
USAGE_SEQUENCE_KEY = "auto_skills_usage_sequence"

# Evidence ceiling. Generous -- the whole trajectory is what makes a skill
# generalisable -- but bounded, because this goes into a model call.
MAX_EVIDENCE_CHARS = 24000
_TRUNCATION_MARKER = "\n\n[... middle of the transcript omitted ...]\n\n"


@dataclass(slots=True)
class Candidate:
    """A completed turn that passed the capture criteria."""

    sequence: int
    tool_calls: int
    evidence: str
    loaded_skills: list[str]
    context_id: str = ""


# What a real user message looks like in history. hist_add_user_message renders
# fw.user_message.md, whose "user_message" key is what distinguishes it.
_USER_CONTENT_KEYS = ("user_message", "user_intervention")


def _is_user_message(message: Any) -> bool:
    """Whether this history entry is something the user said.

    Not the same as "not from the model". hist_add_tool_result calls
    hist_add_message(False, ...), so a tool result is stored with ai=False
    exactly like a user message. Treating every non-AI entry as the turn
    boundary makes the boundary move on every tool call, which resets the
    counters and means no turn is ever seen as substantial -- the whole feature
    silently does nothing. Only the content shape tells them apart.
    """
    if getattr(message, "ai", True):
        return False
    content = getattr(message, "content", None)
    if not isinstance(content, dict):
        return False
    return any(content.get(key) for key in _USER_CONTENT_KEYS)


def latest_user_sequence(agent: "Agent") -> int:
    """Sequence of the message that started the current turn."""
    try:
        for message in reversed(agent.history.all_messages()):
            if _is_user_message(message):
                return int(message.sequence or 0)
    except Exception:
        pass
    return 0


def _blank_turn(sequence: int) -> dict[str, Any]:
    return {"sequence": sequence, "tool_calls": 0, "completed": False}


def record_tool_outcome(agent: "Agent", response: Any) -> None:
    """Count a tool call, or mark the turn as ended by one.

    The tool that closes the loop is the response tool; it is the completion
    signal rather than a unit of work, so it is not counted.
    """
    sequence = latest_user_sequence(agent)
    turn = agent.get_data(TURN_DATA_KEY)
    if not isinstance(turn, dict) or turn.get("sequence") != sequence:
        turn = _blank_turn(sequence)

    if getattr(response, "break_loop", False):
        turn["completed"] = True
    else:
        turn["tool_calls"] = int(turn.get("tool_calls") or 0) + 1

    agent.set_data(TURN_DATA_KEY, turn)


def already_captured(agent: "Agent", sequence: int) -> bool:
    try:
        return int(agent.context.get_data(CAPTURED_SEQUENCE_KEY) or 0) == sequence
    except Exception:
        return False


def mark_captured(agent: "Agent", sequence: int) -> None:
    try:
        agent.context.set_data(CAPTURED_SEQUENCE_KEY, sequence)
    except Exception:
        pass


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = limit * 2 // 5
    tail = limit - head - len(_TRUNCATION_MARKER)
    return text[:head] + _TRUNCATION_MARKER + text[-tail:]


def build_evidence(agent: "Agent", since_sequence: int) -> str:
    """The just-completed turn as text: what was asked, what was run, what came back."""
    parts: list[str] = []
    try:
        for message in agent.history.all_messages():
            if int(message.sequence or 0) < since_sequence:
                continue
            text = message.output_text()
            if text and text.strip():
                parts.append(text.strip())
    except Exception:
        return ""
    return _truncate("\n\n".join(parts), MAX_EVIDENCE_CHARS)


def loaded_skill_names(agent: "Agent") -> list[str]:
    """Skills the agent actually loaded while solving this task.

    A receipt, not a guess: if the task was solved by loading an existing skill,
    the reviewer should be updating that one rather than minting a rival.
    """
    try:
        from helpers import skills as skills_helper

        entries = skills_helper.get_loaded_skill_entries(agent)
    except Exception:
        return []

    names: list[str] = []
    for entry in entries or []:
        name = str((entry or {}).get("name") or "").strip()
        if name and name not in names:
            names.append(name)
    return names


@dataclass(slots=True)
class TurnUsage:
    """Which skills the finished turn loaded, and whether it finished well."""

    sequence: int
    loaded: list[str]
    completed: bool


def find_turn_usage(agent: "Agent") -> TurnUsage | None:
    """The just-ended turn's skill loads, once per turn, or None.

    Independent of the capture criteria. A turn with one tool call is not worth
    reviewing and still tells you a skill was read, which is the signal the
    scoring in later phases has to rest on.
    """
    sequence = latest_user_sequence(agent)
    if not sequence:
        return None
    try:
        if int(agent.context.get_data(USAGE_SEQUENCE_KEY) or 0) == sequence:
            return None
    except Exception:
        return None

    turn = agent.get_data(TURN_DATA_KEY)
    completed = bool(isinstance(turn, dict) and turn.get("completed"))

    loaded = loaded_skill_names(agent)
    if not loaded:
        # Nothing to record, but stamp anyway so the repeated hook fires do not
        # re-walk the history for a turn already looked at.
        _stamp_usage(agent, sequence)
        return None

    return TurnUsage(sequence=sequence, loaded=loaded, completed=completed)


def _stamp_usage(agent: "Agent", sequence: int) -> None:
    try:
        agent.context.set_data(USAGE_SEQUENCE_KEY, sequence)
    except Exception:
        pass


def mark_usage_recorded(agent: "Agent", sequence: int) -> None:
    _stamp_usage(agent, sequence)


def find_candidate(agent: "Agent", min_tool_calls: int) -> Candidate | None:
    """The completed turn worth reviewing, or None with no side effects."""
    sequence = latest_user_sequence(agent)
    if not sequence or already_captured(agent, sequence):
        return None

    turn = agent.get_data(TURN_DATA_KEY)
    if not isinstance(turn, dict) or turn.get("sequence") != sequence:
        return None
    if not turn.get("completed"):
        return None

    tool_calls = int(turn.get("tool_calls") or 0)
    if tool_calls < min_tool_calls:
        return None

    evidence = build_evidence(agent, sequence)
    if not evidence:
        return None

    return Candidate(
        sequence=sequence,
        tool_calls=tool_calls,
        evidence=evidence,
        loaded_skills=loaded_skill_names(agent),
        context_id=str(getattr(agent.context, "id", "") or ""),
    )
