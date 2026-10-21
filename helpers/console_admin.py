"""The product's accounts, as the appliance's console menu reaches them (rezztor-setup, entry `admin`).

The person at the console may give a product administrator a new password: with one administrator and no mail
there was no other way back in, and that person could reinstall the machine anyway. The host runs

    docker exec rezztor-eagent sh -c 'cd /eagent && exec /opt/venv-eagent/bin/python -c \
        "from helpers.console_admin import main; main()" "$@"' sh list|reset USERNAME
"""

from __future__ import annotations

import contextlib
import re
import sys
from typing import Any

from helpers import rbac, users

# The container's own output, which is the product's log: an audit line belongs there and not on the console
# of whoever ran this (plugins/_audit, ADR 0005).
PRODUCT_LOG = "/proc/1/fd/1"


def administrators() -> list[dict[str, Any]]:
    """The accounts a console reset may touch: enabled administrators who sign in with a password here. An
    identity provider's account has no password of ours to set."""
    return [
        user for user in users.list_users()
        if rbac.is_admin(user["role"]) and not user["disabled"] and user["provider"] == users.PROVIDER_LOCAL
    ]  # fmt: skip


def reset(username: str) -> str:
    """A new generated password for the administrator `username`, to be changed at the next sign-in. Every
    session of the account ends with the old password (helpers/login.py). Returns the password, which is shown
    once and kept nowhere."""
    target = users.normalize_username(username)
    if target not in {user["username"] for user in administrators()}:
        raise users.UserError(f"'{target}' is not an administrator whose password this console can reset.")
    password = users.generate_password()
    updated = users.update_user(target, password=password, must_change_password=True, actor="console")
    _audit(updated)
    return password


def _audit(user: dict[str, Any]) -> None:
    """The same event as a password set in the browser, in the product's log. Never fails the reset."""
    try:
        from plugins._audit.helpers import audit

        try:
            out = open(PRODUCT_LOG, "w", encoding="utf-8")  # noqa: SIM115
        except OSError:
            out = None
        with out or contextlib.nullcontext(), contextlib.redirect_stdout(out or sys.stderr):
            audit.emit("account.update", target=user["username"], target_id=user["id"], role=user["role"],
                       disabled=user["disabled"], password_changed=True, password_generated=True, via="console")  # fmt: skip
    except Exception:  # noqa: BLE001
        pass


def _line(text: Any) -> str:
    """One field of a line the console splits on tabs: no tab, no newline, nothing that moves the cursor."""
    return " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", str(text or "")).split())[:60]


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    try:
        if args == ["list"]:
            for user in administrators():
                last = (user["last_login"] or "never")[:16].replace("T", " ")  # an ISO string, in UTC
                print("\t".join(_line(field) for field in (user["username"], user["display_name"], last)))
        elif len(args) == 2 and args[0] == "reset":
            print(reset(args[1]))
        else:
            raise SystemExit("usage: list | reset USERNAME")
    except users.UserError as exc:
        raise SystemExit(str(exc)) from None
