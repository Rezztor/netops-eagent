"""Who may sign a waiting change, and telling them that it waits.

A high-risk change expires in thirty minutes inside one chat, and the person who may approve it is often not in that
chat. So when a device change, a runbook plan with a change in it, or an Azure change starts to wait for approval,
every account that may decide it is told: a notification in the bell that opens the Approvals page on that change, and
a mail to the account's own address when mail is configured (`helpers/mailer.send_to_user`: an account, never an
address, the `_ea_mail` rule), at most one per approver in ten minutes with what waited in between batched into it.
Called from `change_store._store`, the one place every status transition passes.

Nothing here decides an approval. Who may decide is read off the endpoints that do: `change_approve` lets any holder
of `network.approve` decide a device record; `azure_approve` lets only the requester decide an Azure one (ADR 0039).
`may_decide` repeats that so nobody is told about a change they cannot sign, and the endpoints still check for
themselves. Reads and rollbacks are not announced: a read card is answered in the chat that asked for it, and a
rollback card follows every apply.
"""

from __future__ import annotations

import datetime as _dt
import threading
import time
from typing import Any

from helpers import rbac, user_scope, users
from helpers.print_style import PrintStyle

#: Decided by `change_approve`, by any holder of `network.approve`.
DEVICE_KINDS = ("read", "change", "rollback", "plan")
#: Decided by `azure_approve`, by the requester only (`plugins/_ea_azure_change/helpers/records.KINDS`; not imported,
#: because that plugin imports this one).
AZURE_KINDS = ("azure_change", "azure_action")

APPROVALS_PATH = "/approvals"


def wants_signature(record: Any) -> bool:
    """A change someone has to sign: announced, and listed on the Approvals page."""
    return (
        record.kind == "change"
        or record.kind in AZURE_KINDS
        or (record.kind == "plan" and str(record.tier or "").startswith("change"))
    )


def may_decide(record: Any, owner: str, user: dict[str, Any] | None) -> bool:
    """Whether the endpoint that decides `record` would let `user` decide it."""
    if not user or user.get("disabled") or not rbac.has_permission(user.get("role"), rbac.PERM_NETWORK_APPROVE):
        return False
    if record.kind in AZURE_KINDS:
        return user.get("id") == owner
    return record.kind in DEVICE_KINDS


def approvers(record: Any, owner: str) -> list[dict[str, Any]]:
    return [user for user in users.list_users() if may_decide(record, owner, user)]


def link(record: Any) -> str:
    return f"{APPROVALS_PATH}?change={record.id}"


def _expiry(record: Any) -> str:
    if not record.expires_at:
        return "it does not expire"
    at = _dt.datetime.fromtimestamp(record.expires_at, _dt.timezone.utc)
    return f"it expires at {at:%Y-%m-%d %H:%M} UTC"


def _background(work: Any) -> None:
    """SMTP can be slow, and the save that announced the change is an agent's tool call: never wait for it."""
    threading.Thread(target=work, name="approval-mail", daemon=True).start()


def _later(delay: float, work: Any) -> None:
    timer = threading.Timer(delay, work)
    timer.daemon = True
    timer.start()


_now = time.time

#: At most one approval mail per approver in this many seconds; what waits in between goes into the next one.
MAIL_EVERY = 600.0
# ponytail: held in this process, so a restart drops a held batch (the bell and the Approvals page still show it) and
# a second worker process would keep its own window; move to a table if the application ever runs more than one.
_lock = threading.Lock()
_mailed_at: dict[str, float] = {}
_held: dict[str, list[tuple[str, str, str, str]]] = {}  # approver -> [(change id, owner, headline, paragraph)]


def notify(record: Any, owner: str) -> None:
    """Tell everyone who may sign `record` that it waits. Never raises: telling is not the change."""
    try:
        if not wants_signature(record):
            return
        found = approvers(record, owner)
        if not found:
            return
        requester = next((u for u in found if u["id"] == owner), None) or users.get_user_by_id(owner) or {}
        who = requester.get("display_name") or requester.get("username") or "an engineer"
        what = record.summary or record.playbook_name or record.kind
        where = f" on {record.limit}" if record.limit else ""
        tier = f" ({record.tier})" if record.tier else ""
        message = f"{who} asks for approval{tier}: {what}{where}. Waiting now; {_expiry(record)}."
        _tell(record, found, message)
        _mail(record, owner, [u for u in found if u["id"] != owner], who, what, where, tier)
    except Exception as error:  # noqa: BLE001
        PrintStyle.error(f"Approvers of {getattr(record, 'id', '?')} were not told: {error}")


def _tell(record: Any, found: list[dict[str, Any]], message: str) -> None:
    from helpers.notification import NotificationManager, NotificationPriority, NotificationType

    for user in found:
        with user_scope.user_scope(user["id"]):
            NotificationManager.send_notification(
                NotificationType.WARNING, NotificationPriority.HIGH, message, title="A change waits for approval",
                display_time=10, group="approvals", id=f"approval-{record.id}", link=link(record),
            )


def _mail(record: Any, owner: str, found: list[dict[str, Any]], who: str, what: str, where: str, tier: str) -> None:
    """The requester is not mailed: they are at the card. Everyone else with an address on file is, at most once in
    `MAIL_EVERY` per approver: a change that waits inside the window is held and goes, with any others, in one mail
    when the window ends, if it is still waiting then. The bell is still told about each change at once."""
    from helpers import mailer, spa

    if not found or not mailer.is_available()[0]:
        return
    base = mailer.config()["base_url"]
    open_at = f"{base}{spa.MOUNT_PATH}{link(record)}" if base else "Rezztor, Approvals"
    headline = f"{what}{where}{tier}"
    paragraph = (
        f"{headline}, asked for by {who}. Reference {record.id}; it waits now, and {_expiry(record)}.\n"
        f"Read the exact commands and decide at: {open_at}"
    )
    for user in found:
        uid = user["id"]
        with _lock:
            held = _held.setdefault(uid, [])
            held.append((record.id, owner, headline, paragraph))
            if len(held) > 1:
                continue  # a mail to this approver is already on its way, and takes this one with it
            wait = _mailed_at.get(uid, float("-inf")) + MAIL_EVERY - _now()
        if wait <= 0:
            _background(lambda uid=uid: _flush(uid))
        else:
            _later(wait, lambda uid=uid: _flush(uid))


def _still_waiting(change_id: str, owner: str) -> bool:
    from plugins._ea_ansible.helpers import change_store
    from plugins._ea_ansible.helpers.change_model import AWAITING_APPROVAL

    try:
        record = change_store.load(change_id, owner)
    except Exception:  # noqa: BLE001 - gone or unreadable: nothing to sign
        return False
    return record.status == AWAITING_APPROVAL and not record.expired()


def _flush(uid: str) -> None:
    """Send one approver the changes held for them that still wait. Never raises."""
    from helpers import mailer

    with _lock:
        items = _held.pop(uid, [])
        _mailed_at[uid] = _now()
    waiting = [(headline, paragraph) for change_id, owner, headline, paragraph in items if _still_waiting(change_id, owner)]
    if not waiting:
        return
    subject = f"Approval needed: {waiting[0][0]}" if len(waiting) == 1 else f"{len(waiting)} changes need your approval"
    body = "These changes wait for an approver:\n\n" + "\n\n".join(f"- {p}" for _, p in waiting) + "\n"
    try:
        mailer.send_to_user(uid, subject=subject[: mailer.MAX_SUBJECT], body=body)
    except Exception as error:  # noqa: BLE001
        PrintStyle.error(f"Approval mail to {uid} failed: {error}")
