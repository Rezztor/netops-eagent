"""The application's side of the appliance's update service (ADR 0080).

An appliance installed from the ISO runs a root tool on the host, `rezztor-update`, which installs a
Rezztor-signed update file. This module is everything the application has to do with it, and it is not much:

  read    `<spool>/state/state.json`, which the host writes and this container mounts read-only;
  store   one uploaded file as `<spool>/inbox/package.rezupd`;
  ask     one request as `<spool>/inbox/request.json`: an id, one of the words in ACTIONS, who asked, and for
          `download` the version, for `refresh` in a UAT build the update server and its CA (ADR 0091).
          `reboot` restarts the appliance, and the host does it only when Debian's updates need it (ADR 0092).
  give    a support bundle the host made into `<spool>/state/bundles/`, for the administrator to download.

The application verifies nothing, installs nothing and calls no update server. The host asks Rezztor's update
service which packages follow the running version (`refresh`), downloads the first one (`download`) or walks the
whole chain (`update_all`), checks every file's signature itself, with keys this container cannot write, and
treats the request as untrusted: at worst a broken application asks the host to fetch and install a genuine,
newer, Rezztor-signed update, or to restart.

With no state file there is no update service (a hand-installed Compose deployment, a developer's machine).
`status()` then says `available: false` and nothing is written anywhere.
"""

import json
import os
import re
import secrets
import time
from datetime import datetime
from typing import Any, BinaryIO

ACTIONS = ("check", "install", "restart", "discard", "bundle", "refresh", "download", "update_all", "reboot")
# What the host's state file holds that the page may see. The file also carries the tool's own working fields.
PUBLIC = ("phase", "installed", "update_key", "keys", "package", "steps", "error", "result", "history", "request", "at",
          "bundles", "bundle_error", "updates", "progress", "download", "os")  # fmt: skip
# The host's names for support bundles (rezztor-update, BUNDLE_NAME).
BUNDLE_NAME = re.compile(r"rezztor-support-[a-z0-9-]{1,40}-\d{8}-\d{6}\.tar\.gz")
# A release or package version as the host reads one (rezztor-update, VERSION), with a UAT build's suffix.
VERSION = re.compile(r"\d{4}\.\d{2}\.\d{2}(\.\d{1,2})?(-uat)?")
# While the host is in one of these, nothing new is handed to it.
BUSY = {"downloading": "An update is being downloaded. Wait until it has finished.",
        "installing": "An update is being installed. Wait until it has finished.",
        "restarting": "An update is being installed. Wait until it has finished."}  # fmt: skip
# The address a UAT build's host asks when its trust file names none (ADR 0085, 0091); the file's `update_server` wins.
# A release build sends none: its host asks update.rezztor.com.
UAT_SERVER = "https://update.uat.invalid"
# The instance setting behind the daily check (helpers/settings.py), on by default.
DAILY_SETTING = "update_daily_check"
DAY = 86400


class UpdateRefused(Exception):
    """A request the application does not pass on. The text is for the administrator."""


def _spool() -> str:
    return os.environ.get("REZZTOR_UPDATE_SPOOL") or "/run/rezztor-update"


def _state() -> dict[str, Any] | None:
    try:
        with open(os.path.join(_spool(), "state", "state.json"), encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) else None


def _version_key(version: Any) -> tuple[int, ...] | None:
    text = str(version or "").removesuffix("-uat")
    return tuple(int(part) for part in text.split(".")) if VERSION.fullmatch(text) else None


def _waiting(state: dict[str, Any]) -> dict[str, Any] | None:
    """The host's list of packages, without those the machine has installed since it was fetched."""
    updates = state.get("updates")
    if not isinstance(updates, dict):
        return None
    have = _version_key((state.get("installed") or {}).get("version"))
    listed = [entry for entry in updates.get("available") or [] if isinstance(entry, dict)]
    if have is not None:
        listed = [entry for entry in listed if (_version_key(entry.get("version")) or ()) > have]
    return {**updates, "available": listed}


def status() -> dict[str, Any]:
    """The Updates page's whole answer."""
    state = _state()
    if state is None:
        return {"available": False}
    return {"available": True, **{key: state.get(key) for key in PUBLIC}, "updates": _waiting(state)}


def summary() -> dict[str, str] | None:
    """For the bootstrap payload: the update that waits, or None. Never raises."""
    state = _state() or {}
    package = state.get("package")
    if state.get("phase") in (None, "idle") or not isinstance(package, dict):
        return None
    return {"phase": str(state["phase"]), "version": str(package.get("version") or "")}


def waiting_count() -> int | None:
    """For the bootstrap payload: how many packages the host last heard of, or None without a list. Never raises."""
    try:
        state = _state()
        waiting = _waiting(state) if state else None
    except Exception:  # noqa: BLE001 - the banner must not fail a cold start
        return None
    return len(waiting["available"]) if waiting else None


def _ready() -> str:
    state = _state()
    if state is None:
        raise UpdateRefused("This installation has no update service.")
    if state.get("phase") in BUSY:
        raise UpdateRefused(BUSY[state["phase"]])
    inbox = os.path.join(_spool(), "inbox")
    if os.path.lexists(os.path.join(inbox, "request.json")):
        raise UpdateRefused("The update service has not taken the last request yet. Try again in a moment.")
    return inbox


def store(upload: BinaryIO) -> None:
    """Keep an uploaded file for the host to look at. One file at a time, under one fixed name."""
    inbox = _ready()
    part = os.path.join(inbox, "package.rezupd.part")
    try:
        with open(part, "wb") as out:
            while chunk := upload.read(1 << 20):
                out.write(chunk)
        os.replace(part, os.path.join(inbox, "package.rezupd"))
    except OSError as exc:
        # What was written so far goes too: after a full disk it is what keeps the disk full,
        # and nothing on the host looks for a file of this name.
        try:
            os.unlink(part)
        except OSError:
            pass
        raise UpdateRefused("The file could not be stored on this installation.") from exc


def bundle_path(name: str) -> str:
    """The file of a support bundle the host lists, for a download; UpdateRefused for any other name."""
    listed = {bundle.get("file") for bundle in (_state() or {}).get("bundles") or [] if isinstance(bundle, dict)}
    path = os.path.join(_spool(), "state", "bundles", str(name))
    if not BUNDLE_NAME.fullmatch(str(name)) or name not in listed or not os.path.isfile(path):
        raise UpdateRefused("There is no such support bundle. It may be older than 7 days.")
    return path


def _fields(action: str, version: str | None) -> dict[str, str]:
    """What a request carries besides its id, action and author; UpdateRefused for what the host would refuse."""
    from helpers import license_client

    if action == "reboot" and not ((_state() or {}).get("os") or {}).get("reboot_needed"):
        raise UpdateRefused("The appliance does not need a restart.")
    if action not in ("refresh", "download", "update_all"):
        return {}
    if license_client.community_build():
        raise UpdateRefused("A Community build takes no paid update packages.")
    # Every call to the service: a UAT image names its server and CA each time (the host keeps the last ones).
    trust = license_client.uat_trust()
    service = {"server": trust.get("update_server") or UAT_SERVER, **({"ca_pem": trust["ca_pem"]} if trust["ca_pem"] else {})} if trust else {}
    if action != "download":
        return service
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise UpdateRefused("Name the version to download.")
    first = ((_waiting(_state() or {}) or {}).get("available") or [None])[0]
    if not first or first.get("version") != version:
        raise UpdateRefused("Updates are installed in order: only the first one in the list can be downloaded.")
    if not first.get("downloadable"):
        raise UpdateRefused("This update cannot be downloaded on this installation's plan yet.")
    return {"version": version, **service}


def request(action: str, by: str, version: str | None = None) -> str:
    """Ask the host for one of the things in ACTIONS. Returns the request's id; the host echoes it in the state
    when it has taken the request."""
    if action not in ACTIONS:
        raise UpdateRefused("Unknown action.")
    inbox = _ready()
    fields = _fields(action, version)
    ident = secrets.token_hex(8)
    part = os.path.join(inbox, "request.json.part")
    try:
        with open(part, "w", encoding="utf-8") as out:
            json.dump({"id": ident, "action": action, "by": by, **fields}, out)
        os.replace(part, os.path.join(inbox, "request.json"))
    except OSError as exc:
        raise UpdateRefused("The request could not be handed to the update service.") from exc
    return ident


def daily_check_enabled() -> bool:
    """The administrator's switch for the daily check (instance setting `update_daily_check`, on by default)."""
    from helpers import settings

    return bool(settings.get_settings().get(DAILY_SETTING, True))


def _seconds(stamp: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


_last_asked: float | None = None


def daily_refresh(now: float | None = None) -> bool:
    """Ask the host for one `refresh` when the last check is a day old. Returns whether it asked.

    Nothing without a spool (a Compose install, a developer's machine), with the switch off, while the host is
    busy or has not taken the last request. The day is counted from the host's `checked_at` and from this
    process's own last ask, so a host that never answers is not asked every minute; an install since then makes
    the check due at once, so the next update is offered without a Check now.
    """
    global _last_asked
    now = time.time() if now is None else now
    state = _state()
    if state is None or state.get("phase") in BUSY or not daily_check_enabled():
        return False
    if os.path.lexists(os.path.join(_spool(), "inbox", "request.json")):
        return False
    updates = state.get("updates")
    checked = _seconds(updates.get("checked_at")) if isinstance(updates, dict) else None
    last = max((mark for mark in (checked, _last_asked) if mark is not None), default=None)
    # A list heard before the last install offers nothing to download (its next entry was not first then), so an
    # install makes the check due at once, not a day later.
    installed = state.get("installed")
    installed_at = _seconds(installed.get("updated_at")) if isinstance(installed, dict) and installed.get("updated_at") else None
    stale = installed_at is not None and (last is None or last < installed_at)
    if last is not None and now - last < DAY and not stale:
        return False
    try:
        request("refresh", "daily check")
    except UpdateRefused:
        return False
    _last_asked = now
    return True
