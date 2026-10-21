"""The console's way back in for a locked-out administrator (helpers/console_admin.py, rezztor-setup entry `admin`)."""

import json

import pytest

from helpers import console_admin, users


@pytest.fixture
def accounts(database, tmp_path, monkeypatch):
    log = tmp_path / "product.log"
    monkeypatch.setattr(console_admin, "PRODUCT_LOG", str(log))
    users.create_user("chief", "the-old-password-of-chief", "admin", display_name="Chief\tAdmin\n\x1b[2J",
                      must_change_password=False)  # fmt: skip
    users.create_user("second", "the-old-password-second", "admin")
    users.create_user("gone", "the-old-password-of-gone", "admin")
    users.update_user("gone", disabled=True)
    users.create_user("worker", "the-old-password-worker", "user")
    users.upsert_federated_user(subject="s-1", username="federated", email="f@example.com", display_name="F",
                                role="admin", issuer="https://idp.example.com")  # fmt: skip
    return log


def test_only_enabled_local_administrators_are_offered(accounts, capsys):
    assert [user["username"] for user in console_admin.administrators()] == ["chief", "second"]
    console_admin.main(["list"])
    lines = capsys.readouterr().out.splitlines()
    assert [line.split("\t")[:2] for line in lines] == [["chief", "Chief Admin [2J"], ["second", "second"]]
    assert all(line.count("\t") == 2 and "\x1b" not in line for line in lines) and lines[0].endswith("\tnever")


def test_a_reset_gives_a_new_password_once_and_the_old_one_stops_working(accounts, capsys):
    console_admin.main(["reset", "Chief"])
    password = capsys.readouterr().out.strip()
    assert len(password) == 20
    assert users.authenticate("chief", "the-old-password-of-chief") is None
    signed_in = users.authenticate("chief", password)
    assert signed_in and signed_in["must_change_password"] is True
    assert users.get_user("chief")["updated_by"] == "console"

    # The audit event is in the product's log, says how, and does not hold the password.
    event = json.loads(accounts.read_text().split(" ", 1)[1])
    assert event["event"] == "account.update" and event["target"] == "chief" and event["via"] == "console"
    assert event["password_changed"] is True and password not in accounts.read_text()


@pytest.mark.parametrize("name", ["worker", "gone", "federated", "nobody"])
def test_nothing_else_can_be_reset_from_the_console(accounts, capsys, name):
    before = users.get_user(name)
    with pytest.raises(SystemExit, match="not an administrator whose password this console can reset"):
        console_admin.main(["reset", name])
    assert capsys.readouterr().out == "" and users.get_user(name) == before


def test_the_audit_goes_to_stderr_when_the_product_log_cannot_be_opened(accounts, capsys, monkeypatch):
    monkeypatch.setattr(console_admin, "PRODUCT_LOG", "/nonexistent/fd/1")
    console_admin.main(["reset", "second"])
    out, err = capsys.readouterr()
    assert len(out.strip()) == 20 and "account.update" in err and out.strip() not in err


def test_anything_else_is_a_usage_error(accounts):
    for argv in ([], ["reset"], ["list", "x"], ["delete", "chief"]):
        with pytest.raises(SystemExit, match="usage"):
            console_admin.main(argv)
