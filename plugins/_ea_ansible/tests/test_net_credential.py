"""net_credential shows a form and never handles a secret."""

from __future__ import annotations

import asyncio

from plugins._ea_ansible.tools import net_credential
from plugins._ea_interaction.helpers import cards


def call(monkeypatch, **args):
    logged = []

    def show(agent, name, **kw):
        card = cards.secret_form_card(name, **kw)  # validates, as the real one does
        logged.append((name, kw))
        return card

    monkeypatch.setattr(cards, "show_secret_form", show)
    tool = net_credential.NetCredential(agent=object(), name="net_credential", method=None, args=args, message="", loop_data=None)
    return asyncio.new_event_loop().run_until_complete(tool.execute()), logged


def test_request_shows_the_form_and_ends_the_turn(monkeypatch):
    response, logged = call(monkeypatch, name="lab-sonic", username="admin")
    assert logged == [("lab-sonic", {"kind": "password", "username": "admin"})]
    assert response.break_loop is True and "stored" in response.message


def test_a_bad_name_or_an_unknown_action_shows_nothing(monkeypatch):
    for args in ({"name": "pw=Zq9-fake-Pass-1234"}, {"name": ""}, {"action": "read", "name": "lab"}):
        response, logged = call(monkeypatch, **args)
        assert logged == [] and response.break_loop is False


def test_a_secret_passed_as_an_argument_is_ignored(monkeypatch):
    response, logged = call(monkeypatch, name="lab", secret="Zq9-fake-Pass-1234", password="Zq9-fake-Pass-1234")
    assert "Zq9-fake-Pass-1234" not in response.message and "Zq9" not in str(logged)
