"""The whole induction path, with only the model replaced.

Every other test here covers one stage. This one runs review.run end to end --
prompt rendering, the model call, parsing, the validation gate, the write and the
queue -- because the seams between those stages were the only part with no
coverage, and they are where a signature mismatch would hide.

The prompts are the real files, rendered through the real reader, so a template
that stopped substituting or a renamed placeholder fails here.

review.run is dispatched detached from the turn that triggered it. Nothing it
does may raise, so every failure mode below asserts that as well as asserting
that nothing was written.
"""

import asyncio
import json
from pathlib import Path

import pytest

from helpers import files, skills as skills_helper, user_scope
from plugins._ea_auto_skills.helpers import promote, review, store
from plugins._ea_auto_skills.helpers.capture import Candidate

# The real prompt directory, resolved from this file rather than through
# files.get_abs_path, which the fixture below repoints at a temporary tree.
PROMPTS = Path(__file__).resolve().parents[1] / "prompts"

USER = "alice"

SETTINGS = {
    "mode": "personal",
    "min_tool_calls": 2,
    "max_personal_skills": 25,
    "max_pending_promotions": 50,
    "max_skill_md_chars": 10000,
    "max_skill_bytes": 40000,
    "review_timeout_seconds": 30,
}

GOOD_REPLY = json.dumps({
    "action": "create",
    "name": "trace-a-slow-endpoint",
    "description": "Find why one endpoint is slow while the rest are fine.",
    "body": "1. Compare against a neighbouring route.\n2. Check query counts first.",
    "rationale": "Profiling before counting queries wastes the most time.",
})


class _FakeLog:
    def __init__(self):
        self.items = []

    def log(self, **kwargs):
        self.items.append(kwargs)
        return object()


class _FakeContext:
    def __init__(self):
        self.id = "ctx-e2e"
        self.log = _FakeLog()
        self._data = {}

    def get_data(self, key):
        return self._data.get(key)

    def set_data(self, key, value):
        self._data[key] = value


class _FakeAgent:
    """Everything review.run touches, with the model call under our control."""

    def __init__(self, reply="", *, raises=None, delay=0.0):
        self.context = _FakeContext()
        self.calls = []
        self._reply = reply
        self._raises = raises
        self._delay = delay

    def read_prompt(self, file, **kwargs):
        return files.read_prompt_file(file, _directories=[str(PROMPTS)], **kwargs)

    async def call_utility_model(self, system, message, background=False):
        self.calls.append({"system": system, "message": message, "background": background})
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._raises:
            raise self._raises
        return self._reply


@pytest.fixture
def instance(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    # An empty library: nothing to collide with, and nothing to list.
    monkeypatch.setattr(skills_helper, "list_skills", lambda *a, **k: [])
    # Both plugin roots have to exist. helpers.plugins.get_plugins_list walks
    # usr/plugins and plugins with an unguarded iterdir, so a missing one raises
    # -- which reached the secrets check, which refused the skill. It only
    # showed up once another test had cleared the plugin cache first, so this
    # file passed alone and failed in the suite.
    for part in ("usr/skills", "usr/plugins", "plugins", f"usr/users/{USER}/skills"):
        (tmp_path / part).mkdir(parents=True, exist_ok=True)
    return tmp_path


def _candidate():
    return Candidate(
        sequence=12,
        tool_calls=4,
        evidence="user: the /reports endpoint got slow\nai: traced it to an N+1",
        loaded_skills=["some-loaded-skill"],
        context_id="ctx-e2e",
    )


def _run(agent, settings=None):
    with user_scope.user_scope(USER):
        asyncio.run(review.run(agent, _candidate(), settings or SETTINGS))


def _written(instance, name="trace-a-slow-endpoint"):
    return instance / "usr" / "users" / USER / "skills" / name / "SKILL.md"


# --- the path that works ----------------------------------------------------

def test_a_usable_reply_becomes_a_skill_and_a_proposal(instance):
    agent = _FakeAgent(GOOD_REPLY)

    _run(agent)

    written = _written(instance)
    assert written.is_file()
    assert "trace-a-slow-endpoint" in written.read_text()
    assert "Check query counts first." in written.read_text()

    queued = promote.list_all()
    assert len(queued) == 1
    assert queued[0]["name"] == "trace-a-slow-endpoint"
    assert queued[0]["owner_user_id"] == USER
    assert queued[0]["status"] == promote.STATUS_PENDING

    assert "trace-a-slow-endpoint" in store.read_ledger(USER)


def test_the_user_is_told_a_skill_was_learned(instance):
    agent = _FakeAgent(GOOD_REPLY)
    _run(agent)
    assert any("trace-a-slow-endpoint" in item.get("heading", "") for item in agent.context.log.items)


def test_the_written_file_is_discoverable_as_a_skill(instance, monkeypatch):
    """The end that matters: the agent has to be able to find it next time."""
    agent = _FakeAgent(GOOD_REPLY)
    _run(agent)

    monkeypatch.undo()  # restore the real list_skills, keep the temporary tree
    monkeypatch.setattr(files, "_base_dir", str(instance))
    with user_scope.user_scope(USER):
        found = {skill.name for skill in skills_helper.list_skills()}

    assert "trace-a-slow-endpoint" in found


# --- what the model is actually asked ---------------------------------------

def test_the_reviewer_is_sent_the_evidence_and_the_receipts(instance):
    agent = _FakeAgent(GOOD_REPLY)
    _run(agent)

    assert len(agent.calls) == 1
    sent = agent.calls[0]

    assert "traced it to an N+1" in sent["message"]
    assert "some-loaded-skill" in sent["message"]
    assert "(none)" in sent["message"]  # no existing skills to list
    assert "{{" not in sent["message"], "a placeholder was left unsubstituted"

    # Whitespace-normalised: the prompt is hard-wrapped, so a sentence that
    # matters should not stop matching because it was reflowed.
    system = " ".join(sent["system"].split())
    assert "Skipping is the normal answer" in system
    assert "Respond with one JSON object and nothing else" in system


def test_the_review_does_not_consume_the_users_rate_limit(instance):
    """It runs after the turn, on the user's behalf but not in their queue."""
    agent = _FakeAgent(GOOD_REPLY)
    _run(agent)
    assert agent.calls[0]["background"] is True


# --- every way it can go wrong ----------------------------------------------

def test_a_skip_and_a_refusal_are_told_apart_in_the_audit_stream(instance, capsys):
    """From outside, a reviewer that declined and a draft the gate rejected look
    identical: nothing written, nothing queued. An operator needs to know which."""
    _run(_FakeAgent(json.dumps({"action": "skip", "rationale": "Nothing reusable."})))
    assert "auto_skills.skipped" in capsys.readouterr().out

    _run(_FakeAgent(json.dumps({
        "action": "create", "name": "Not A Valid Name",
        "description": "d", "body": "b", "rationale": "r",
    })))
    refusal = capsys.readouterr().out
    assert "auto_skills.refused" in refusal
    assert "lowercase" in refusal, "the reason has to travel with the event"

    _run(_FakeAgent("not json at all"))
    assert "auto_skills.unusable_reply" in capsys.readouterr().out


def test_a_skip_writes_nothing(instance):
    agent = _FakeAgent(json.dumps({"action": "skip", "rationale": "Nothing reusable."}))

    _run(agent)

    assert not _written(instance).exists()
    assert promote.list_all() == []
    assert store.read_ledger(USER) == {}


def test_an_unparseable_reply_writes_nothing(instance):
    _run(_FakeAgent("I had a think and decided not to bother."))
    assert not _written(instance).exists()
    assert promote.list_all() == []


def test_a_reply_that_fails_the_gate_writes_nothing_and_queues_nothing(instance):
    """A draft rejected by validation must not reach the queue either, or an
    administrator would be offered something the gate already refused."""
    agent = _FakeAgent(json.dumps({
        "action": "create",
        "name": "Not A Valid Name",
        "description": "d",
        "body": "b",
        "rationale": "r",
    }))

    _run(agent)

    assert promote.list_all() == []
    assert store.read_ledger(USER) == {}


def test_a_slow_model_is_abandoned(instance):
    agent = _FakeAgent(GOOD_REPLY, delay=0.2)

    _run(agent, {**SETTINGS, "review_timeout_seconds": 0.01})

    assert not _written(instance).exists()
    assert promote.list_all() == []


def test_a_failing_model_is_survived(instance):
    agent = _FakeAgent(raises=RuntimeError("provider returned 401"))

    _run(agent)  # must not raise

    assert not _written(instance).exists()
    assert promote.list_all() == []


def test_nothing_happens_without_an_owner(instance):
    """Resolved once at the top: a write path must never guess whose it is."""
    agent = _FakeAgent(GOOD_REPLY)

    asyncio.run(review.run(agent, _candidate(), SETTINGS))

    assert agent.calls == [], "the model should not even be asked"
    assert promote.list_all() == []


def test_review_mode_queues_without_writing(instance):
    agent = _FakeAgent(GOOD_REPLY)

    _run(agent, {**SETTINGS, "mode": "review"})

    assert not _written(instance).exists()
    assert len(promote.list_all()) == 1
    assert agent.context.log.items == [], "nothing was applied, so say nothing"
