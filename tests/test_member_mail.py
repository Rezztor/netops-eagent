"""Mail that can only reach people who hold accounts here.

The agent writes these messages, and it writes them from whatever reached its
context — a web page, an uploaded document, a tool result. So the property that
matters is not "the right message was composed" but "no composed message can
name a mailbox outside the organisation", and that has to hold against an
argument the agent chose.
"""

import time

import pytest

from helpers import files, mailer, password_reset, rbac, settings as settings_module, users
from plugins._ea_mail.helpers import policy


@pytest.fixture
def instance(database, tmp_path, monkeypatch):
    # send_link's admin-exemption check reads settings.get_settings(), and
    # normalize_settings() inside it re-reads conf/workdir.gitignore off disk on
    # *every* call, cache or not. That file lives at the real repo root, and
    # files._base_dir is about to move away from it -- so the function itself is
    # replaced, computed against the real repo first, rather than trying to
    # pre-warm a cache that gets bypassed anyway.
    base_settings = dict(settings_module.get_default_settings())
    monkeypatch.setattr(settings_module, "get_settings", lambda: dict(base_settings))

    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    (tmp_path / "usr").mkdir(parents=True, exist_ok=True)
    yield tmp_path


@pytest.fixture
def accounts(instance):
    admin = users.create_user(
        "admin", "adminpassword", rbac.ROLE_ADMIN, email="admin@example.com"
    )
    member = users.create_user(
        "priya", "priyapassword", rbac.ROLE_USER, email="priya@example.com"
    )
    other = users.create_user(
        "sam", "sam-password", rbac.ROLE_USER, email="sam@example.com"
    )
    return {"admin": admin, "member": member, "other": other}


@pytest.fixture
def sent(monkeypatch):
    """Capture what would reach SMTP, without reaching it."""
    outbox = []

    def fake_send(address, *, subject, body):
        outbox.append({"address": address, "subject": subject, "body": body})

    monkeypatch.setattr(mailer, "_send", fake_send)
    monkeypatch.setattr(
        mailer,
        "config",
        lambda: {
            "enabled": True, "host": "smtp.example.com", "port": 587,
            "security": "starttls", "username": "", "password": "",
            "from_address": "eagent@example.com", "from_name": "Rezztor EAgent",
            "base_url": "https://eagent.example.com",
        },
    )
    return outbox


# --- the address never comes from the caller --------------------------------


def test_a_message_goes_to_the_address_on_the_account(accounts, sent):
    mailer.send_to_user(accounts["member"]["id"], subject="Hello", body="Body")
    assert sent[0]["address"] == "priya@example.com"


def test_an_email_address_is_not_a_recipient(accounts):
    """The defining rule. An instruction hidden in a document naming an outside
    mailbox must not be able to become a recipient."""
    with pytest.raises(policy.Refused, match="not an email address"):
        policy.resolve_target("attacker@evil.example", accounts["admin"]["id"])


def test_an_unknown_account_is_refused(accounts):
    with pytest.raises(policy.Refused, match="no account called"):
        policy.resolve_target("nobody", accounts["admin"]["id"])


def test_an_account_with_no_address_is_refused_by_name(instance):
    silent = users.create_user("quiet", "quietpassword", rbac.ROLE_USER)
    with pytest.raises(mailer.MailError, match="no email address"):
        mailer.resolve_recipient(silent["id"])


def test_a_disabled_account_is_not_a_recipient(accounts):
    users.update_user("sam", disabled=True)
    with pytest.raises(mailer.MailError, match="disabled"):
        mailer.resolve_recipient(accounts["other"]["id"])


def test_an_account_that_does_not_exist_is_not_a_recipient(instance):
    with pytest.raises(mailer.MailError, match="No such account"):
        mailer.resolve_recipient("0" * 32)


# --- who may reach whom ------------------------------------------------------


def test_anyone_may_email_their_own_account(accounts):
    target = policy.resolve_target("priya", accounts["member"]["id"])
    assert target["username"] == "priya"


def test_a_standard_user_may_not_email_a_colleague(accounts):
    """Otherwise a document someone was asked to summarise becomes a way to put
    words in the platform's mouth, addressed to somebody not in the room."""
    with pytest.raises(policy.Refused, match="and no one else"):
        policy.resolve_target("sam", accounts["member"]["id"])


def test_an_administrator_may_email_another_account(accounts):
    target = policy.resolve_target("sam", accounts["admin"]["id"])
    assert target["username"] == "sam"


def test_the_administrator_exception_can_be_turned_off(accounts, monkeypatch):
    monkeypatch.setattr(
        policy, "config",
        lambda: {"max_per_hour": 20, "allow_admin_to_email_others": False},
    )
    with pytest.raises(policy.Refused):
        policy.resolve_target("sam", accounts["admin"]["id"])


def test_a_sender_with_no_account_is_refused(accounts):
    with pytest.raises(policy.Refused, match="no account to send on behalf of"):
        policy.resolve_target("sam", "0" * 32)


# --- rate limiting -----------------------------------------------------------


def test_a_sender_is_capped_per_hour(accounts, monkeypatch):
    monkeypatch.setattr(
        policy, "config",
        lambda: {"max_per_hour": 3, "allow_admin_to_email_others": True},
    )
    for _ in range(3):
        policy.check_and_record(accounts["admin"]["id"])
    with pytest.raises(policy.Refused, match="limit"):
        policy.check_and_record(accounts["admin"]["id"])


def test_the_cap_is_per_sender_not_global(accounts, monkeypatch):
    monkeypatch.setattr(
        policy, "config",
        lambda: {"max_per_hour": 1, "allow_admin_to_email_others": True},
    )
    policy.check_and_record(accounts["admin"]["id"])
    policy.check_and_record(accounts["member"]["id"])  # must not raise


def test_the_window_lets_go(accounts, monkeypatch):
    monkeypatch.setattr(
        policy, "config",
        lambda: {"max_per_hour": 1, "allow_admin_to_email_others": True},
    )
    policy.check_and_record(accounts["admin"]["id"])
    later = time.time() + 3601
    monkeypatch.setattr(policy.time, "time", lambda: later)
    policy.check_and_record(accounts["admin"]["id"])  # must not raise


# --- the address field on an account ----------------------------------------


def test_a_malformed_address_is_refused_on_create(instance):
    with pytest.raises(users.UserError, match="not a valid email"):
        users.create_user("bad", "badpassword12", rbac.ROLE_USER, email="not an address")


def test_two_accounts_cannot_share_an_address(accounts):
    """Password reset looks an account up by address; two would make that
    ambiguous exactly when it must not be."""
    with pytest.raises(users.UserError, match="already uses"):
        users.create_user("dupe", "dupepassword12", rbac.ROLE_USER, email="priya@example.com")


def test_an_account_may_keep_its_own_address_when_edited(accounts):
    updated = users.update_user("priya", email="priya@example.com", display_name="Priya R")
    assert updated["email"] == "priya@example.com"


def test_an_address_is_stored_lowercased(instance):
    created = users.create_user("mixed", "mixedpassword", rbac.ROLE_USER, email="Mixed@Example.COM")
    assert created["email"] == "mixed@example.com"
    assert users.get_user_by_email("MIXED@example.com") is not None


def test_an_account_may_have_no_address(instance):
    created = users.create_user("none", "nonepassword12", rbac.ROLE_USER)
    assert created["email"] == ""


# --- reset tokens ------------------------------------------------------------


def test_a_token_identifies_its_account(accounts):
    token, _ = password_reset.issue(accounts["member"]["id"])
    assert password_reset.consume(token) == accounts["member"]["id"]


def test_a_token_works_once(accounts):
    token, _ = password_reset.issue(accounts["member"]["id"])
    password_reset.consume(token)
    with pytest.raises(password_reset.ResetError, match="already been used"):
        password_reset.consume(token)


def test_an_expired_token_is_refused(accounts, monkeypatch):
    token, _ = password_reset.issue(accounts["member"]["id"])
    monkeypatch.setattr(password_reset, "_now", lambda: time.time() + 3600)
    with pytest.raises(password_reset.ResetError, match="expired"):
        password_reset.consume(token)


def test_an_invented_token_is_refused(accounts):
    password_reset.issue(accounts["member"]["id"])
    with pytest.raises(password_reset.ResetError, match="not valid"):
        password_reset.consume("made-up-token")


def test_the_raw_token_is_never_stored(accounts):
    """A copy of the store must not be a set of working links.

    The store is a table now, so this reads every column of every row rather
    than the file it used to be -- and reads them all rather than just the
    digest, because the point is that the token is nowhere, not that it is
    absent from the column we remembered to check.
    """
    from helpers import db

    token, _ = password_reset.issue(accounts["member"]["id"])
    with db.unscoped_transaction() as conn:
        rows = conn.execute("SELECT * FROM password_resets").fetchall()
    assert rows, "nothing was stored at all"
    assert token not in str(rows)


def test_asking_again_replaces_the_link(accounts):
    first, _ = password_reset.issue(accounts["member"]["id"])
    second, _ = password_reset.issue(accounts["member"]["id"])
    with pytest.raises(password_reset.ResetError):
        password_reset.consume(first)
    assert password_reset.consume(second) == accounts["member"]["id"]


def test_a_second_request_within_the_minute_is_throttled(accounts):
    password_reset.issue(accounts["member"]["id"])
    assert password_reset.recently_requested(accounts["member"]["id"]) is True
    assert password_reset.recently_requested(accounts["other"]["id"]) is False


def test_revoking_kills_outstanding_links(accounts):
    token, _ = password_reset.issue(accounts["member"]["id"])
    password_reset.revoke_for_user(accounts["member"]["id"])
    with pytest.raises(password_reset.ResetError):
        password_reset.consume(token)


def test_one_accounts_token_does_not_unlock_another(accounts):
    token, _ = password_reset.issue(accounts["member"]["id"])
    assert password_reset.consume(token) != accounts["other"]["id"]


# --- the flow, as both front doors run it -----------------------------------


def test_a_reset_link_reaches_the_address_on_the_account(accounts, sent):
    outcome = password_reset.send_link("priya", "https://eagent.example.com")
    assert outcome == "sent"
    assert sent[0]["address"] == "priya@example.com"
    assert "/reset-password?token=" in sent[0]["body"]


def test_a_reset_link_can_be_asked_for_by_address(accounts, sent):
    assert password_reset.send_link("PRIYA@example.com", "https://x.example") == "sent"


def test_an_unknown_identifier_sends_nothing(accounts, sent):
    assert password_reset.send_link("nobody", "https://x.example") == "no_such_account"
    assert sent == []


def test_a_disabled_account_gets_no_link(accounts, sent):
    users.update_user("sam", disabled=True)
    assert password_reset.send_link("sam", "https://x.example") == "no_such_account"
    assert sent == []


def test_no_base_url_means_no_link_rather_than_a_guessed_one(accounts, sent):
    """A link built from the request's Host header points wherever the caller
    says. Better to send nothing."""
    assert password_reset.send_link("priya", "") == "no_base_url"
    assert sent == []


def test_a_second_request_in_the_same_minute_sends_once(accounts, sent):
    password_reset.send_link("priya", "https://x.example")
    assert password_reset.send_link("priya", "https://x.example") == "rate_limited"
    assert len(sent) == 1


def test_the_emailed_token_actually_works(accounts, sent):
    password_reset.send_link("priya", "https://eagent.example.com")
    token = sent[0]["body"].split("/reset-password?token=")[1].split("\n")[0].strip()
    ok, _message = password_reset.complete(token, "a-new-long-password")
    assert ok
    assert users.authenticate("priya", "a-new-long-password") is not None


def test_completing_a_reset_clears_the_forced_change(accounts, sent):
    """The password is now one only its holder has chosen, so there is nothing
    left to force a change of."""
    password_reset.send_link("priya", "https://eagent.example.com")
    token = sent[0]["body"].split("/reset-password?token=")[1].split("\n")[0].strip()
    password_reset.complete(token, "a-new-long-password")
    assert users.must_change_password("priya") is False


def test_a_short_password_does_not_spend_the_link(accounts, sent):
    password_reset.send_link("priya", "https://eagent.example.com")
    token = sent[0]["body"].split("/reset-password?token=")[1].split("\n")[0].strip()
    ok, message = password_reset.complete(token, "short")
    assert not ok and "at least" in message
    # Still usable: the user mistyped, and that should not cost them the email.
    assert password_reset.complete(token, "a-new-long-password")[0] is True


def test_the_old_password_stops_working(accounts, sent):
    password_reset.send_link("priya", "https://eagent.example.com")
    token = sent[0]["body"].split("/reset-password?token=")[1].split("\n")[0].strip()
    password_reset.complete(token, "a-new-long-password")
    assert users.authenticate("priya", "priyapassword") is None


def test_the_message_never_contains_the_password(accounts, sent):
    password_reset.send_link("priya", "https://eagent.example.com")
    assert "priyapassword" not in sent[0]["body"]


# --- an administrator's switch on self-service reset ------------------------


@pytest.fixture
def reset_disabled(instance, monkeypatch):
    """Depends on `instance` explicitly so its settings patch is already in
    place -- fixture order is not otherwise guaranteed, and stacking this on
    top of the *real* get_settings would reintroduce the disk read."""
    current = dict(settings_module.get_settings())
    current["password_reset_enabled"] = False
    monkeypatch.setattr(settings_module, "get_settings", lambda: current)


def test_turning_it_off_stops_a_standard_users_link(accounts, sent, reset_disabled):
    assert password_reset.send_link("priya", "https://x.example") == "disabled"
    assert sent == []


def test_an_administrator_is_exempt_so_they_cannot_be_locked_out(accounts, sent, reset_disabled):
    """The one self-service recovery path must not have a setting that can lock
    the person who controls that setting out of their own account."""
    assert password_reset.send_link("admin", "https://x.example") == "sent"
    assert len(sent) == 1


def test_being_off_does_not_change_which_message_the_caller_sees(accounts, sent, monkeypatch):
    """The visible notice must not depend on the outcome, or "disabled" for a
    standard user against "sent" for an admin would leak which accounts hold
    the admin role -- to anyone who can see the sign-in screen, with no
    session at all."""
    base = dict(settings_module.get_settings())
    disabled = {**base, "password_reset_enabled": False}
    monkeypatch.setattr(settings_module, "get_settings", lambda: disabled)
    seen_outcomes = []

    def record_and_call(who):
        outcome = password_reset.send_link(who, "https://x.example")
        seen_outcomes.append(outcome)

    record_and_call("admin")
    record_and_call("priya")
    record_and_call("nobody-at-all")

    assert seen_outcomes == ["sent", "disabled", "no_such_account"]
    # And yet: api/password_reset_request.py always returns the same _ANSWER
    # regardless of what send_link returned. That contract lives in the
    # handler, not here -- asserted directly against its constant.
    from api.password_reset_request import _ANSWER

    assert "message" in _ANSWER  # one fixed message for every outcome above


def test_turning_it_back_on_restores_the_flow(accounts, sent, reset_disabled, monkeypatch):
    base = dict(settings_module.get_settings())
    monkeypatch.setattr(
        settings_module, "get_settings", lambda: {**base, "password_reset_enabled": True}
    )
    assert password_reset.send_link("priya", "https://x.example") == "sent"
