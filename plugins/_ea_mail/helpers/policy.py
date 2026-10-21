"""Who the agent may email, and how often.

The recipient rule is one sentence: an agent may email the account whose
conversation it is, and any other account only when the person operating it
holds `users.manage`.

That is not an arbitrary split. `users.manage` is already the permission that can
read every account's address, create accounts and reset passwords, so an
administrator's agent reaching another member adds no capability its operator
lacks. A standard user's agent reaching a colleague would: it would turn a
document someone was asked to summarise into a way to put words in the platform's
mouth, addressed to somebody who never chose to be in the conversation.

Sending is rate limited per operator, not per recipient. The thing worth bounding
is one conversation that has gone wrong -- a loop, or an instruction the agent
read somewhere and took seriously -- and that is a property of the sender.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

from helpers import files, plugins as plugin_helpers, rbac, users

PLUGIN_NAME = "_ea_mail"

_FALLBACK = {"max_per_hour": 20, "allow_admin_to_email_others": True}
_WINDOW_SECONDS = 3600
_LEDGER = "usr/mail_sent.json"
_LOCK = threading.RLock()


class Refused(Exception):
    """The send is not allowed. The message is shown to the agent."""


def config() -> dict[str, Any]:
    stored = plugin_helpers.get_plugin_config(PLUGIN_NAME) or {}
    merged = {**_FALLBACK, **stored}
    try:
        limit = int(merged.get("max_per_hour"))
        merged["max_per_hour"] = limit if limit > 0 else _FALLBACK["max_per_hour"]
    except (TypeError, ValueError):
        merged["max_per_hour"] = _FALLBACK["max_per_hour"]
    merged["allow_admin_to_email_others"] = bool(merged.get("allow_admin_to_email_others"))
    return merged


def resolve_target(who: str, sender_id: str) -> dict[str, Any]:
    """The account a name refers to, checked against who is asking.

    Accepts a username or a user id and nothing else. There is deliberately no
    branch that treats the argument as an address: the moment one exists, a
    sentence in a document can name somewhere outside the organisation, and the
    platform sends it under its own name.
    """
    wanted = str(who or "").strip()
    if not wanted:
        raise Refused("Name the account to email, by username.")

    if "@" in wanted:
        # Said plainly, because the agent will try it and should learn why not.
        raise Refused(
            "This tool takes the username of an account on this instance, not an "
            "email address. It can only reach people who hold accounts here."
        )

    target = users.get_user(wanted) or users.get_user_by_id(wanted)
    if target is None:
        raise Refused(f"There is no account called '{wanted}'.")
    if target.get("disabled"):
        raise Refused(f"The account '{target['username']}' is disabled.")

    if target["id"] == sender_id:
        return target

    sender = users.get_user_by_id(sender_id)
    if sender is None:
        raise Refused("This conversation has no account to send on behalf of.")

    settings = config()
    may_reach_others = rbac.has_permission(
        sender.get("role"), rbac.PERM_USERS_MANAGE
    ) and settings["allow_admin_to_email_others"]

    if not may_reach_others:
        raise Refused(
            f"You may email {sender['username']} — the account this conversation "
            "belongs to — and no one else."
        )
    return target


def _ledger_path() -> str:
    return files.get_abs_path(_LEDGER)


def _read_ledger() -> dict[str, list[float]]:
    try:
        with open(_ledger_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_ledger(data: dict[str, list[float]]) -> None:
    path = _ledger_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    os.replace(tmp, path)


def check_and_record(sender_id: str) -> None:
    """Count this send against the sender's hour, or refuse it.

    Recorded here rather than after a successful send, on purpose. A mail server
    that accepts slowly, or fails after delivering, would otherwise let a loop
    run uncounted for as long as it kept failing.
    """
    limit = config()["max_per_hour"]
    now = time.time()
    cutoff = now - _WINDOW_SECONDS

    with _LOCK:
        ledger = _read_ledger()
        recent = [
            stamp
            for stamp in ledger.get(sender_id, [])
            if isinstance(stamp, (int, float)) and stamp > cutoff
        ]
        if len(recent) >= limit:
            raise Refused(
                f"This account has already sent {limit} messages in the last hour, "
                "which is the limit. Try again later."
            )
        recent.append(now)
        ledger[sender_id] = recent
        # Drop accounts whose window has emptied, so the file does not grow one
        # entry per account that ever sent anything.
        ledger = {key: value for key, value in ledger.items() if value}
        _write_ledger(ledger)
