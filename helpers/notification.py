from dataclasses import dataclass
import uuid
import threading
from datetime import datetime, timedelta
from enum import Enum

from helpers.localization import Localization


class NotificationType(Enum):
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    PROGRESS = "progress"


class NotificationPriority(Enum):
    NORMAL = 10
    HIGH = 20


@dataclass
class NotificationItem:
    manager: "NotificationManager"
    no: int
    type: NotificationType
    priority: NotificationPriority
    title: str
    message: str
    detail: str  # HTML content for expandable details
    timestamp: datetime
    display_time: int = 3  # Display duration in seconds, default 3 seconds
    read: bool = False
    id: str = ""
    group: str = ""  # Group identifier for grouping related notifications
    # The account it was raised for, taken from the user scope at creation. None
    # is a system item (startup, maintenance): administrators see those.
    owner_id: str | None = None
    # Where in the application the item points: a path under the app (`/approvals?change=...`), or "".
    link: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())
        # Ensure type is always NotificationType
        if isinstance(self.type, str):
            self.type = NotificationType(self.type)

    def mark_read(self):
        self.read = True
        self.manager.update_item(self.no, read=True)

    def output(self):
        return {
            "no": self.no,
            "id": self.id,
            "type": self.type.value if isinstance(self.type, NotificationType) else self.type,
            "priority": self.priority.value if isinstance(self.priority, NotificationPriority) else self.priority,
            "title": self.title,
            "message": self.message,
            "detail": self.detail,
            "timestamp": Localization.get().serialize_datetime(self.timestamp),
            "display_time": self.display_time,
            "read": self.read,
            "group": self.group,
            "link": self.link,
        }


def _viewer() -> tuple[str | None, bool]:
    """Who is asking, and whether they also see system items.

    One process holds one manager, so the list is every account's. It was also
    shown to every account: a new `user` saw another's change plan and sign-in
    codes in its bell, and could mark them read or clear them.

    Outside a request and a user scope there is no account to hide anything
    from, and only system items match a missing owner anyway.
    """
    from helpers import rbac, user_scope, users

    user_id = user_scope.get_current_user_id()
    if not user_id:
        return None, True
    try:
        user = users.get_user_by_id(user_id)
    except Exception:
        user = None
    return user_id, bool(user) and rbac.has_permission(
        user.get("role"), rbac.PERM_SETTINGS_MANAGE
    )


class NotificationManager:
    def __init__(self, max_notifications: int = 100):
        self._lock = threading.RLock()
        self.guid: str = str(uuid.uuid4())
        self.updates: list[int] = []
        self.notifications: list[NotificationItem] = []
        self.max_notifications = max_notifications

    @staticmethod
    def send_notification(
        type: NotificationType,
        priority: NotificationPriority,
        message: str,
        title: str = "",
        detail: str = "",
        display_time: int = 3,
        group: str = "",
        id: str = "",
        link: str = "",
    ) -> NotificationItem:
        from agent import AgentContext
        return AgentContext.get_notification_manager().add_notification(
            type, priority, message, title, detail, display_time, group, id, link
        )

    def add_notification(
        self,
        type: NotificationType,
        priority: NotificationPriority,
        message: str,
        title: str = "",
        detail: str = "",
        display_time: int = 3,
        group: str = "",
        id: str = "",
        link: str = "",
    ) -> NotificationItem:
        from helpers import user_scope

        owner_id = user_scope.get_current_user_id()
        with self._lock:
            existing = None
            if id:
                existing = next(
                    (n for n in self.notifications if n.id == id and n.owner_id == owner_id),
                    None,
                )
                if existing is None and any(n.id == id for n in self.notifications):
                    # Somebody else's item holds this id. Ids are how an item is
                    # marked read, so two accounts must not share one.
                    id = ""

            if existing:
                existing.type = NotificationType(type)
                existing.priority = NotificationPriority(priority)
                existing.title = title
                existing.message = message
                existing.detail = detail
                existing.timestamp = Localization.get().now()
                existing.display_time = display_time
                existing.group = group
                existing.link = link
                existing.read = False
                self.updates.append(existing.no)
                item = existing
            else:
            # Create notification item
                item = NotificationItem(
                    manager=self,
                    no=len(self.notifications),
                    type=NotificationType(type),
                    priority=NotificationPriority(priority),
                    title=title,
                    message=message,
                    detail=detail,
                    timestamp=Localization.get().now(),
                    display_time=display_time,
                    id=id,
                    group=group,
                    owner_id=owner_id,
                    link=link,
                )

                self.notifications.append(item)
                self.updates.append(item.no)
                self._enforce_limit()

        from helpers.state_monitor_integration import mark_dirty_all
        mark_dirty_all(reason="notification.NotificationManager.add_notification")
        return item

    def _enforce_limit(self):
        """Keep the newest `max_notifications` of each owner.

        One list holds every account's items, and the cap was on the list: the
        oldest went first whoever they belonged to, so one account's items
        pushed out everybody else's. The cap is per owner now -- system items,
        which have no owner, count as one -- so an account can only crowd out
        its own.
        """
        with self._lock:
            over: dict[str | None, int] = {}
            for notification in self.notifications:
                over[notification.owner_id] = over.get(notification.owner_id, 0) + 1
            over = {owner: count - self.max_notifications for owner, count in over.items()}
            if not any(excess > 0 for excess in over.values()):
                return
            kept: list[NotificationItem] = []
            moved: dict[int, int] = {}
            for notification in self.notifications:  # oldest first
                if over[notification.owner_id] > 0:
                    over[notification.owner_id] -= 1
                    continue
                moved[notification.no] = len(kept)
                notification.no = len(kept)
                kept.append(notification)
            self.notifications = kept
            self.updates = [moved[no] for no in self.updates if no in moved]

    def get_recent_notifications(self, seconds: int = 30) -> list[NotificationItem]:
        cutoff = Localization.get().now() - timedelta(seconds=seconds)
        return [n for n in self._visible() if n.timestamp >= cutoff]

    def output(self, start: int | None = None, end: int | None = None) -> list[dict]:
        return self.output_with_state(start, end)[0]

    def output_with_state(
        self, start: int | None = None, end: int | None = None
    ) -> tuple[list[dict], str, int]:
        with self._lock:
            if start is None:
                start = 0
            if end is None:
                end = len(self.updates)
            updates = self.updates[start:end]
            # The cursor stays one for the whole process; what is filtered is
            # which of the updates this caller is shown.
            visible = {n.no for n in self._visible()}
            out = []
            seen = set()
            for update in updates:
                if update not in seen and update in visible:
                    out.append(self.notifications[update].output())
                    seen.add(update)
            return out, self.guid, len(self.updates)

    def _visible(self) -> list[NotificationItem]:
        """The caller's own items, plus system items for an administrator."""
        user_id, sees_system = _viewer()
        with self._lock:
            return [
                n
                for n in self.notifications
                if n.owner_id == user_id or (n.owner_id is None and sees_system)
            ]

    def output_all(self) -> list[dict]:
        return [n.output() for n in self._visible()]

    def mark_read_by_ids(self, notification_ids: list[str]) -> int:
        ids = {nid for nid in notification_ids if isinstance(nid, str) and nid.strip()}
        if not ids:
            return 0

        changed_nos: list[int] = []
        with self._lock:
            for notification in self._visible():
                if notification.id in ids and not notification.read:
                    notification.read = True
                    changed_nos.append(notification.no)
            if changed_nos:
                self.updates.extend(changed_nos)

        if not changed_nos:
            return 0

        from helpers.state_monitor_integration import mark_dirty_all
        mark_dirty_all(reason="notification.NotificationManager.mark_read_by_ids")
        return len(changed_nos)

    def update_item(self, no: int, **kwargs) -> None:
        self._update_item(no, **kwargs)

    def _update_item(self, no: int, **kwargs):
        changed = False
        with self._lock:
            if no < len(self.notifications):
                item = self.notifications[no]
                for key, value in kwargs.items():
                    if hasattr(item, key):
                        setattr(item, key, value)
                self.updates.append(no)
                changed = True

        if not changed:
            return

        from helpers.state_monitor_integration import mark_dirty_all
        mark_dirty_all(reason="notification.NotificationManager._update_item")

    def mark_all_read(self):
        changed_nos: list[int] = []
        with self._lock:
            for notification in self._visible():
                if not notification.read:
                    notification.read = True
                    changed_nos.append(notification.no)
            if changed_nos:
                self.updates.extend(changed_nos)

        if not changed_nos:
            return

        from helpers.state_monitor_integration import mark_dirty_all
        mark_dirty_all(reason="notification.NotificationManager.mark_all_read")

    def clear_all(self):
        with self._lock:
            # Only what the caller can see goes. The rest is renumbered and
            # every remaining item is queued again under a new guid, so each
            # other account's next read rebuilds its own list unchanged.
            gone = {id(n) for n in self._visible()}
            self.notifications = [n for n in self.notifications if id(n) not in gone]
            for i, notification in enumerate(self.notifications):
                notification.no = i
            self.updates = list(range(len(self.notifications)))
            self.guid = str(uuid.uuid4())
        from helpers.state_monitor_integration import mark_dirty_all
        mark_dirty_all(reason="notification.NotificationManager.clear_all")

    def get_notifications_by_type(self, type: NotificationType) -> list[NotificationItem]:
        return [n for n in self._visible() if n.type == type]
