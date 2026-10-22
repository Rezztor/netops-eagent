"""Audit logging.

The audit trail is the record an auditor or an incident responder works from, so
these tests are about three properties rather than about formatting: it never
breaks the operation it observes, it never carries a secret, and every event
identifies who caused it.
"""

import json

import pytest

from plugins._audit.helpers import audit


def _events(capsys) -> list[dict]:
    out = capsys.readouterr().out
    return [
        json.loads(line[len(audit.AUDIT_PREFIX) + 1 :])
        for line in out.splitlines()
        if line.startswith(audit.AUDIT_PREFIX)
    ]


# --- shape ------------------------------------------------------------------

def test_events_are_one_json_object_per_prefixed_line(capsys):
    audit.emit("tool.start", tool="code_execution_tool", context_id="ctx-1")
    events = _events(capsys)
    assert len(events) == 1
    record = events[0]
    assert record["event"] == "tool.start"
    assert record["tool"] == "code_execution_tool"
    assert record["v"] == audit.SCHEMA_VERSION
    assert record["ts"].endswith("+00:00"), "timestamps must be UTC and unambiguous"
    assert "actor" in record


def test_every_event_carries_an_actor_even_when_unresolved(capsys):
    """An event with no actor is still emitted -- silence is worse -- but the
    gap must be visible rather than implied."""
    audit.emit("tool.start", tool="x")
    actor = _events(capsys)[0]["actor"]
    assert set(actor) == {"user_id", "username", "role"}


def test_actor_is_resolved_from_the_active_user_scope(capsys):
    """Agent runs happen on a background thread with no request session, so the
    actor has to come from the scope the run carries."""
    from helpers import user_scope

    with user_scope.user_scope("alice-id"):
        audit.emit("tool.start", tool="x")
    assert _events(capsys)[0]["actor"]["user_id"] == "alice-id"


# --- must not break the operation it observes -------------------------------

def test_emit_never_raises_on_unserialisable_payloads(capsys):
    class Hostile:
        def __repr__(self):
            raise RuntimeError("nope")

    audit.emit("tool.start", tool="x", weird=Hostile())  # must not raise


def test_emit_never_raises_when_the_actor_cannot_be_resolved(monkeypatch, capsys):
    def explode():
        raise RuntimeError("session gone")

    monkeypatch.setattr(audit, "current_actor", explode)
    audit.emit("tool.start", tool="x")  # must not raise


def test_hooks_swallow_their_own_failures():
    """A logging bug must not abort a tool call."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "plugins" / "_audit"
    for name in (
        "extensions/python/tool_execute_before/_90_audit_tool_start.py",
        "extensions/python/tool_execute_after/_90_audit_tool_end.py",
    ):
        source = (root / name).read_text(encoding="utf-8")
        assert "except Exception:" in source, name


# --- must not carry secrets -------------------------------------------------

def test_previews_are_masked_through_the_secrets_manager(monkeypatch):
    """An audit log is exactly where a leaked credential does the most damage
    and is least expected to be."""
    monkeypatch.setattr(audit, "_mask", lambda value, context=None: value.replace("hunter2", "§§masked"))
    rendered = audit.preview({"password": "hunter2"})
    assert "hunter2" not in rendered
    assert "§§masked" in rendered


def test_preview_is_capped_so_one_event_cannot_be_a_whole_file():
    rendered = audit.preview("x" * 5000)
    assert len(rendered) < 5000
    assert "+4600 chars" in rendered


def test_full_value_is_still_identifiable_by_digest():
    """The preview is truncated, so the digest is what proves two events refer
    to the same input."""
    a = audit.digest({"cmd": "rm -rf /"})
    b = audit.digest({"cmd": "rm -rf /"})
    c = audit.digest({"cmd": "ls"})
    assert a == b and a != c
    assert a.startswith("sha256:")


def test_a_failed_signin_never_records_the_password():
    """A near-miss password is as sensitive as the real one."""
    source = (
        __import__("pathlib").Path(__file__).resolve().parents[1] / "helpers" / "ui_server.py"
    ).read_text(encoding="utf-8")
    signin = source[source.index('_audit_emit(\n                    "auth.signin",\n                    outcome="failure"'):]
    signin = signin[: signin.index(")")]
    assert "password" not in signin


def test_settings_audit_records_keys_not_values():
    source = (
        __import__("pathlib").Path(__file__).resolve().parents[1] / "api" / "settings_set.py"
    ).read_text(encoding="utf-8")
    assert "changed_keys" in source
    assert "frontend.values()" not in source


# --- coverage ---------------------------------------------------------------

def test_the_security_relevant_operations_are_all_audited():
    """Guard against an operation being added later without a trail."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    expected = {
        "api/users_create.py": "account.create",
        "api/users_update.py": "account.update",
        "api/users_delete.py": "account.delete",
        "api/change_password.py": "account.password_changed",
        "api/settings_set.py": "settings.update",
        "api/backup_restore.py": "backup.restore",
        "api/backup_create.py": "backup.create",
        "api/projects.py": "project.",
        "api/chat_export.py": "chat.export",
        "plugins/_ea_ansible/api/ansible_config.py": "ansible.config.",
        "helpers/ui_server.py": "auth.signin",
    }
    for path, event in expected.items():
        source = (root / path).read_text(encoding="utf-8")
        assert event in source, f"{path} does not emit {event}"


# --- sinks ------------------------------------------------------------------
# An extra consumer of the record stream, for a forwarder that ships events to a
# SIEM. The property being defended is the one ADR 0005 is built on: a sink may
# observe the trail, never alter it, and never break the operation that produced
# it (ADR 0028: a sink observes the trail and must not become a hook that can change it).


@pytest.fixture(autouse=True)
def _clean_sinks():
    """_sinks is module state; a test that registers one must not leak it."""
    saved = list(audit._sinks)
    audit._sinks.clear()
    yield
    audit._sinks[:] = saved


def test_a_sink_receives_the_finalised_record(capsys):
    seen = []
    audit.register_sink(seen.append)

    audit.emit("auth.signin", outcome="failure", username="admin")

    assert len(seen) == 1
    assert seen[0]["event"] == "auth.signin"
    assert seen[0]["outcome"] == "failure"
    # The enrichment emit() does is already applied -- a sink formats what was
    # logged, it does not rebuild it and risk diverging from the real record.
    assert seen[0]["v"] == audit.SCHEMA_VERSION
    assert "ts" in seen[0] and "actor" in seen[0]
    assert seen[0] == _events(capsys)[0]


def test_a_sink_cannot_change_what_reached_stdout(capsys):
    """The whole reason this is a sink and not an @extensible start-hook."""

    def vandal(record):
        record["event"] = "something.else"
        record["actor"] = {"username": "not-who-did-it"}
        record["injected"] = True

    audit.register_sink(vandal)
    audit.emit("account.delete", target="alice")

    logged = _events(capsys)[0]
    assert logged["event"] == "account.delete"
    assert logged["actor"] != {"username": "not-who-did-it"}
    assert "injected" not in logged


def test_one_sink_cannot_change_what_the_next_sink_sees():
    first_saw, second_saw = [], []

    def mutate(record):
        record["event"] = "rewritten"
        first_saw.append(record["event"])

    audit.register_sink(mutate)
    audit.register_sink(lambda record: second_saw.append(record["event"]))

    audit.emit("tool.start", tool="x")

    assert first_saw == ["rewritten"]
    assert second_saw == ["tool.start"]


def test_a_sink_cannot_change_the_in_memory_window():
    audit.register_sink(lambda record: record.update(event="rewritten"))
    audit.emit("tool.start", tool="x")
    assert audit.recent_events(1)[0]["event"] == "tool.start"


def test_a_raising_sink_neither_breaks_emit_nor_the_sinks_after_it(capsys):
    reached = []

    def boom(record):
        raise RuntimeError("the SIEM is on fire")

    audit.register_sink(boom)
    audit.register_sink(reached.append)

    audit.emit("tool.start", tool="x")  # must not raise

    assert len(reached) == 1, "a failing sink must not stop the next one"
    assert _events(capsys)[0]["event"] == "tool.start"


def test_the_record_is_written_before_any_sink_runs(capsys):
    """Ordering matters: stdout is the trail of record (ADR 0005). A sink that
    hangs or crashes must not be able to prevent the event being logged."""
    written_when_sink_ran = []

    def observe(record):
        written_when_sink_ran.append(capsys.readouterr().out)

    audit.register_sink(observe)
    audit.emit("auth.signin", outcome="success")

    assert audit.AUDIT_PREFIX in written_when_sink_ran[0]


def test_emit_is_not_wrapped_by_an_extension_point():
    """@extension.extensible would hand a start-hook the arguments before they
    are masked and written -- a mutation point on the audit trail itself.

    Checked on the function object rather than by searching the source, because
    the source legitimately mentions the decorator in the comment explaining why
    it is not used.
    """
    import inspect

    assert not hasattr(audit.emit, "__wrapped__"), "emit() has been decorated"

    decorators = [
        line.strip()
        for line in inspect.getsource(audit.emit).splitlines()
        if line.strip().startswith("@")
    ]
    assert decorators == [], f"emit() carries decorators: {decorators}"


def test_a_refused_tool_is_not_recorded_as_having_run(monkeypatch):
    # Seen live: a standard user's net_read was refused, and the record read "tool.end net_read ok".
    import asyncio

    from helpers.tool import Response
    from plugins._audit.extensions.python.tool_execute_after._90_audit_tool_end import AuditToolEnd
    from plugins._audit.helpers import audit
    from tools.refused import REFUSED_KEY

    class _Agent:
        def __init__(self):
            self.data = {REFUSED_KEY: True}
            self.context = None

        def get_data(self, key):
            return self.data.get(key)

        def set_data(self, key, value):
            self.data[key] = value

    seen = []
    monkeypatch.setattr(audit, "emit", lambda event, **fields: seen.append((event, fields)))
    agent = _Agent()
    hook = AuditToolEnd.__new__(AuditToolEnd)
    hook.agent = agent
    asyncio.run(hook.execute(response=Response(message="not available", break_loop=False), tool_name="net_read"))
    asyncio.run(hook.execute(response=Response(message="done", break_loop=False), tool_name="wait"))
    assert [fields["outcome"] for _, fields in seen] == ["refused", "ok"]


def test_a_forged_audit_line_fails_verification_and_a_gap_is_noticed(capsys):
    for n in range(3):
        audit.emit("tool.start", tool=f"t{n}")
    lines = capsys.readouterr().out.splitlines()
    assert audit.verify(lines) == []
    forged = audit.AUDIT_PREFIX + ' {"v":1,"event":"login.ok","actor":{"username":"admin"}}'
    tampered = lines[1].replace("t1", "t9")
    assert len(audit.verify([lines[0], forged, tampered, lines[2]])) == 3  # two bad macs + the gap they leave
    assert any("gap" in p for p in audit.verify([lines[0], lines[2]]))
    assert audit.verify(lines, key=audit.audit_key("another secret")) != []


@pytest.mark.parametrize(
    "secret",
    [
        "-----BEGIN KEY-----\nMIIEvQIBADANBg\n-----END KEY-----",  # a newline becomes \n
        'pa"ss\\word',  # a quote and a backslash are escaped
        "motdepassé-zürich",  # a letter outside ASCII becomes \uXXXX
    ],
)
def test_a_secret_is_masked_whatever_json_would_do_to_it(monkeypatch, secret):
    """The arguments were turned into JSON first and searched for secrets second.

    JSON writes a newline as `\\n`, escapes a quote and spells a non-ASCII letter
    `\\uXXXX`, so the secret was no longer in the text as it is stored and the
    search found nothing: a private key used by a tool went into the audit line
    and on to the SIEM as it was.
    """
    monkeypatch.setattr(audit, "_mask", lambda value, context=None: value.replace(secret, "§§secret(KEY)"))

    rendered = audit.preview({"code": f"connect --key '{secret}'", "nested": [{"again": secret}]})

    assert rendered.count("§§secret(KEY)") == 2
    for fragment in ("MIIEvQIBADANBg", "ss\\\\word", "motdepass", "rich"):
        assert fragment not in rendered


def test_a_field_passed_to_emit_as_it_is_gets_masked_too(monkeypatch, capsys):
    """The module's second rule says nothing reaches the log unmasked, and only
    `preview` masked: a caller that passed an error text or a path as a field
    wrote it as it was."""
    monkeypatch.setattr(audit, "_mask", lambda value, context=None: value.replace("hunter2", "§§secret(P)"))

    audit.emit("test.event", detail="login failed for password hunter2", nested={"why": ["hunter2"]})

    line = capsys.readouterr().out
    record = json.loads(line[len(audit.AUDIT_PREFIX) :])
    assert "hunter2" not in line
    assert record["detail"] == "login failed for password §§secret(P)"
    assert record["nested"] == {"why": ["§§secret(P)"]}


def test_a_field_cannot_break_one_event_into_several_lines(capsys):
    """One event is one line, and a reader splits on line ends. JSON escapes
    `\\n`, but written with ensure_ascii off it leaves U+2028, U+2029 and U+0085
    as they are, and those end a line for `str.splitlines` and for collectors
    that read text. The username typed into the sign-in form goes into the
    failed-sign-in event, so somebody with no account could cut a genuine
    record in two and start a line of their own behind it."""
    audit.emit("auth.signin", outcome="failure", username="x REZZTOR_AUDIT {\"forged\": true}\u0085y z")

    written = capsys.readouterr().out
    assert len(written.splitlines()) == 1
    assert written.isascii()
    record = json.loads(written[len(audit.AUDIT_PREFIX) :])
    assert record["username"] == "x REZZTOR_AUDIT {\"forged\": true}\u0085y z"


def test_a_field_cannot_replace_who_did_it_or_when(capsys):
    """`emit` set the actor and then let the caller's fields overwrite it. Two
    callers pass `actor=<a username>`, so their events carried a bare name in
    place of the resolved {user_id, username, role} -- under a valid mac."""
    from helpers import user_scope

    with user_scope.user_scope("u-123"):
        audit.emit("inventory.place.saved", actor="somebody-else", ts="1999-01-01T00:00:00Z", v=99, place="hq")

    record = json.loads(capsys.readouterr().out[len(audit.AUDIT_PREFIX) :])
    assert isinstance(record["actor"], dict) and record["actor"].get("user_id") == "u-123"
    assert record["v"] == audit.SCHEMA_VERSION and not record["ts"].startswith("1999")
    assert record["place"] == "hq"
    # What the caller said is kept, under a name that is not the record's own.
    assert record["field_actor"] == "somebody-else"
