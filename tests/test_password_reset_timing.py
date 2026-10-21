"""A reset request answers before the lookup and the send, so how long it
takes says nothing about whether the identifier holds an account."""

import threading

import pytest

from api.password_reset_request import _ANSWER, PasswordResetRequest
from helpers import login_throttle, mailer, password_reset


@pytest.mark.asyncio
async def test_the_answer_does_not_wait_for_the_lookup_or_the_mail(monkeypatch):
    release, finished = threading.Event(), threading.Event()
    seen = []

    def slow_send_link(identifier, base_url):
        release.wait(5)  # an SMTP round trip, for as long as the test likes
        seen.append(identifier)
        finished.set()
        return "sent"

    monkeypatch.setattr(password_reset, "send_link", slow_send_link)
    monkeypatch.setattr(mailer, "config", lambda: {"base_url": "https://x.example"})
    handler = PasswordResetRequest(app=None, thread_lock=threading.RLock())  # type: ignore[arg-type]

    reply = await handler.process({"identifier": "priya"}, None)

    assert reply == _ANSWER
    assert seen == []
    release.set()
    assert finished.wait(5)
    assert seen == ["priya"]


# --- per source address, and the page's form token -----------------------------


@pytest.fixture
def no_mail(monkeypatch):
    sent = []
    monkeypatch.setattr(password_reset, "send_link_later", lambda identifier, base_url, via: sent.append(identifier))
    monkeypatch.setattr(mailer, "config", lambda: {"base_url": "https://x.example"})
    # A named proxy, so the caller's own address is the one counted.
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "127.0.0.1")
    password_reset.forget_addresses()
    yield sent
    password_reset.forget_addresses()


@pytest.mark.asyncio
async def test_one_address_cannot_walk_a_list_of_names(no_mail):
    """The per-account interval stops one mailbox being flooded; it did nothing
    about one caller asking for a different name every time."""
    from types import SimpleNamespace

    handler = PasswordResetRequest(app=None, thread_lock=threading.RLock())  # type: ignore[arg-type]
    caller = SimpleNamespace(remote_addr="203.0.113.7")

    for n in range(password_reset.ADDRESS_REQUESTS):
        assert await handler.process({"identifier": f"user{n}"}, caller) == _ANSWER
    refused = await handler.process({"identifier": "one-more"}, caller)

    assert refused.status_code == 429
    assert "one-more" not in no_mail
    other = SimpleNamespace(remote_addr="203.0.113.8")
    assert await handler.process({"identifier": "one-more"}, other) == _ANSWER


def _forgot_client():
    from flask import Flask

    from helpers.ui_server import UiRouteHandlers

    app = Flask("test_forgot_password")
    app.secret_key = "test-secret"
    handlers = UiRouteHandlers(None)  # type: ignore[arg-type]
    app.add_url_rule("/forgot-password", "forgot", handlers.forgot_password_handler, methods=["GET", "POST"])
    return app.test_client()


def test_the_forgot_password_form_needs_the_token_it_was_given(no_mail):
    import re

    client = _forgot_client()
    env = {"REMOTE_ADDR": "203.0.113.9"}

    forged = client.post("/forgot-password", data={"identifier": "priya"}, environ_base=env)
    assert "had expired" in forged.get_data(as_text=True)
    assert no_mail == []

    page = client.get("/forgot-password", environ_base=env).get_data(as_text=True)
    token = re.search(r'name="login_form" value="([^"]+)"', page).group(1)
    for _ in range(password_reset.ADDRESS_REQUESTS):
        client.post("/forgot-password", data={"identifier": "priya", "login_form": token}, environ_base=env)
    limited = client.post("/forgot-password", data={"identifier": "priya", "login_form": token}, environ_base=env)

    assert no_mail == ["priya"] * password_reset.ADDRESS_REQUESTS
    assert "Too many reset requests" in limited.get_data(as_text=True)


def test_a_lan_peer_is_limited_with_no_proxy_named(monkeypatch):
    """With FORWARDED_ALLOW_IPS empty every private peer used to be exempt, so a
    caller on the office LAN could walk a list of names at will."""
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "")
    monkeypatch.setattr(login_throttle, "_default_gateway", lambda: "172.31.249.1")
    password_reset.forget_addresses()
    for _ in range(password_reset.ADDRESS_REQUESTS):
        assert password_reset.address_may_request("10.1.2.3")
    assert not password_reset.address_may_request("10.1.2.3")
    assert password_reset.address_may_request("10.1.2.4")
    password_reset.forget_addresses()


def test_the_proxy_on_this_host_is_counted_by_its_forwarded_hop(monkeypatch):
    """Behind the Docker gateway every browser shares one address: the last
    X-Forwarded-For hop, the one that proxy appended, is who is counted."""
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "")
    monkeypatch.setattr(login_throttle, "_default_gateway", lambda: "172.31.249.1")
    password_reset.forget_addresses()
    gateway = "172.31.249.1"
    for _ in range(password_reset.ADDRESS_REQUESTS):
        assert password_reset.address_may_request(gateway, "1.2.3.4, 10.9.9.9")
    assert not password_reset.address_may_request(gateway, "5.6.7.8, 10.9.9.9")
    # A forged leftmost hop changes nothing; another browser is another key.
    assert password_reset.address_may_request(gateway, "10.9.9.10")
    # No hop at all: the proxy is everybody, so nobody is limited.
    for _ in range(password_reset.ADDRESS_REQUESTS * 2):
        assert password_reset.address_may_request(gateway, None)
    password_reset.forget_addresses()


def test_an_ipv6_caller_is_counted_by_its_64(monkeypatch):
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "127.0.0.1")
    password_reset.forget_addresses()
    for n in range(password_reset.ADDRESS_REQUESTS):
        assert password_reset.address_may_request(f"2001:db8:1:2::{n + 1}")
    assert not password_reset.address_may_request("2001:db8:1:2:ffff::9")
    assert password_reset.address_may_request("2001:db8:1:3::1")
    password_reset.forget_addresses()
