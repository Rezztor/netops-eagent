"""The application's side of the appliance's update service: `helpers/update_spool.py` and the two endpoints.

The host tool is not here; its half is `tests/test_update_tool.py`. What these prove is the boundary: the
application reads a state file, stores one file under a fixed name and writes one request, and does nothing
else, whatever the browser sends.
"""

import asyncio
import io
import json
import types

import pytest

from api.support_bundle_download import SupportBundleDownload
from api.update_apply import UpdateApply
from api.update_status import UpdateStatus
from helpers import api_policy, login, rbac, update_spool

STATE = {
    "v": 1, "phase": "idle", "installed": {"version": "2026.10.08", "base": "2026.10.08", "updated_at": None},
    "update_key": "age1example", "keys": ["release-2026a"], "package": None, "steps": [], "error": None,
    "result": None, "history": [], "request": None, "at": "2026-10-12T14:06:02Z",
    "manifest": {"internal": "the host tool's own working field"}, "kept": [],
}  # fmt: skip


@pytest.fixture
def spool(tmp_path, monkeypatch):
    from helpers import license_client

    monkeypatch.setenv("REZZTOR_UPDATE_SPOOL", str(tmp_path))
    monkeypatch.setattr(login, "current_user", lambda: {"username": "alice"})
    # A release image unless a test says otherwise; the daily-check switch on, as it is by default.
    monkeypatch.setattr(license_client, "uat_trust", lambda: None)
    monkeypatch.setattr(license_client, "community_build", lambda: False)
    monkeypatch.setattr(update_spool, "daily_check_enabled", lambda: True)
    monkeypatch.setattr(update_spool, "_last_asked", None)
    (tmp_path / "state").mkdir()
    (tmp_path / "inbox").mkdir()

    write_state(tmp_path)
    return tmp_path


def write_state(spool, **changes):
    (spool / "state" / "state.json").write_text(json.dumps({**STATE, **changes}))


class Upload:
    def __init__(self, content, filename="rezztor-eagent-update-2026.10.12.rezupd"):
        self.stream, self.filename = io.BytesIO(content), filename


def call(handler, payload=None, form=None, files=None, args=None):
    request = types.SimpleNamespace(form=form or {}, files=files or {}, args=args or {})
    return asyncio.new_event_loop().run_until_complete(handler(app=None, thread_lock=None).process(payload or {}, request))


def test_an_installation_without_the_host_tool_says_so_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("REZZTOR_UPDATE_SPOOL", str(tmp_path / "absent"))
    monkeypatch.setattr(login, "current_user", lambda: {"username": "alice"})
    assert call(UpdateStatus) == {"available": False} and update_spool.summary() is None
    assert call(UpdateApply, {"action": "install"}) == {"ok": False, "error": "This installation has no update service."}
    answer = call(UpdateApply, form={"action": "upload"}, files={"package": Upload(b"x")})
    assert answer["ok"] is False and not (tmp_path / "absent").exists()


def test_the_status_is_the_hosts_state_without_its_working_fields(spool):
    answer = call(UpdateStatus)
    assert answer["available"] is True and answer["installed"]["version"] == "2026.10.08" and answer["update_key"] == "age1example"
    assert "manifest" not in answer and "kept" not in answer and "v" not in answer
    (spool / "state" / "state.json").write_text("{ not json")
    assert call(UpdateStatus) == {"available": False}


def test_the_page_shows_no_error_once_the_host_has_cleared_it(spool):
    """The error on the page is the host's `state.error`, read as it is. When the host clears it after a
    successful check, the page has nothing to show: the "too_old" error of an abandoned attempt stayed on the
    page in UAT on 2026-10-21, long after the list came back fine."""
    write_state(spool, error={"code": "too_old", "text": "This update needs at least 2026.10.12."})
    assert call(UpdateStatus)["error"]["code"] == "too_old"
    write_state(spool, error=None)
    assert call(UpdateStatus)["error"] is None


def test_an_upload_is_stored_under_one_fixed_name_and_the_host_is_asked_to_check_it(spool):
    answer = call(UpdateApply, form={"action": "upload"}, files={"package": Upload(b"the file", filename="../../etc/cron.d/x")})
    assert answer["ok"] is True
    assert sorted(path.name for path in (spool / "inbox").iterdir()) == ["package.rezupd", "request.json"]
    assert (spool / "inbox" / "package.rezupd").read_bytes() == b"the file"
    request = json.loads((spool / "inbox" / "request.json").read_text())
    assert request == {"id": answer["request"], "action": "check", "by": "alice"} and len(answer["request"]) == 16


@pytest.mark.parametrize("action", ["install", "restart", "discard", "bundle"])
def test_the_three_buttons_each_write_one_request_and_nothing_else(spool, action):
    answer = call(UpdateApply, {"action": action})
    assert answer["ok"] is True and [path.name for path in (spool / "inbox").iterdir()] == ["request.json"]
    assert json.loads((spool / "inbox" / "request.json").read_text())["action"] == action


def test_what_is_not_one_of_the_actions_is_refused(spool):
    for payload in ({"action": "check"}, {"action": "shell"}, {}, {"action": "upload"}):
        assert call(UpdateApply, payload)["ok"] is False
    assert not list((spool / "inbox").iterdir())


def test_nothing_is_asked_while_the_host_works_or_has_not_taken_the_last_request(spool):
    write_state(spool, phase="installing")
    assert call(UpdateApply, {"action": "discard"}) == {"ok": False, "error": "An update is being installed. Wait until it has finished."}
    write_state(spool, phase="checked")
    assert call(UpdateApply, {"action": "install"})["ok"] is True
    assert "has not taken the last request yet" in call(UpdateApply, {"action": "install"})["error"]
    assert "has not taken the last request yet" in call(UpdateApply, form={"action": "upload"}, files={"package": Upload(b"x")})["error"]


def test_the_banner_gets_only_an_update_that_is_not_finished(spool):
    assert update_spool.summary() is None
    write_state(spool, phase="staged", package={"version": "2026.10.12", "notes": ["private until asked for"]})
    assert update_spool.summary() == {"phase": "staged", "version": "2026.10.12"}


def test_both_endpoints_are_the_administrators_alone():
    for handler, name in ((UpdateStatus, "update_status"), (UpdateApply, "update_apply")):
        assert handler.required_permission() == rbac.PERM_SETTINGS_MANAGE
        assert api_policy.required_permission_for(name) == rbac.PERM_SETTINGS_MANAGE
        assert handler.requires_auth() and handler.requires_csrf()
    # A fix may be what makes the licence work: an installation with no licence can still be updated.
    assert not {"update_status", "update_apply"} & set(api_policy.REFUSED_WHEN_RESTRICTED)
    assert UpdateApply.get_methods() == ["POST"]


def test_an_upload_that_fails_leaves_nothing_in_the_inbox(spool):
    """The file is written under a second name and renamed when it is whole. When
    the write failed -- a full disk -- that half-written file stayed, with the
    disk still full of it, and nothing on the host removes it."""
    from helpers import update_spool

    class Breaks:
        sent = 0

        def read(self, size):
            self.sent += 1
            if self.sent > 2:
                raise OSError(28, "No space left on device")
            return b"x" * 1024

    with pytest.raises(update_spool.UpdateRefused):
        update_spool.store(Breaks())

    assert list((spool / "inbox").iterdir()) == []


BUNDLE = "rezztor-support-rezztor-eagent-20261015-120000.tar.gz"


def test_a_bundle_the_host_lists_is_downloaded_and_the_download_is_audited(spool, monkeypatch):
    from plugins._audit.helpers import audit

    folder = spool / "state" / "bundles"
    folder.mkdir()
    (folder / BUNDLE).write_bytes(b"the bundle")
    (folder / "rezztor-support-x-20261015-130000.tar.gz").write_bytes(b"not listed by the host")
    write_state(spool, bundles=[{"file": BUNDLE, "size": 10, "at": "2026-10-15T12:00:00Z"}], bundle_error=None)
    assert call(UpdateStatus)["bundles"][0]["file"] == BUNDLE

    events = []
    monkeypatch.setattr(audit, "emit", lambda event, **fields: events.append((event, fields)))
    sent = []
    monkeypatch.setattr("api.support_bundle_download.send_file", lambda path, **kw: sent.append((path, kw)) or "file")
    assert call(SupportBundleDownload, args={"file": BUNDLE}) == "file"
    assert sent[0][0] == str(folder / BUNDLE) and sent[0][1]["download_name"] == BUNDLE
    assert events == [("support_bundle.download", {"file": BUNDLE, "by": "alice"})]

    for name in ("rezztor-support-x-20261015-130000.tar.gz", "../state.json", f"../bundles/{BUNDLE}", "", "state.json"):
        answer = call(SupportBundleDownload, args={"file": name})
        assert answer.status_code == 404, name
    assert len(sent) == 1 and len(events) == 1


def test_the_download_is_the_administrators_alone():
    assert SupportBundleDownload.required_permission() == rbac.PERM_SETTINGS_MANAGE
    assert api_policy.required_permission_for("support_bundle_download") == rbac.PERM_SETTINGS_MANAGE
    assert SupportBundleDownload.requires_auth() and SupportBundleDownload.get_methods() == ["GET"]
    assert update_spool.BUNDLE_NAME.pattern == __import__("re").search(
        r'BUNDLE_NAME = re.compile\(r"([^"]+)"\)',
        (__import__("pathlib").Path(__file__).parents[1] / "deploy/iso/host/rezztor-update").read_text()).group(1)


# --- Rezztor's update service, through the host (ADR 0091) -----------------------------------------------------


def entry(version, downloadable=False, **more):
    return {"version": version, "base": "2026.10.08", "min_from": "2026.10.08", "bridge": False,
            "released_at": "2026-10-21T09:00:00Z", "security": False, "size": 383436800, "sha256": "0" * 64,
            "notes": ["A fix."], "available_at": "2026-10-21T09:00:00Z", "downloadable": downloadable, **more}  # fmt: skip


def updates(*available, checked_at="2026-10-20T08:00:00Z", error=None):
    return {"checked_at": checked_at, "server": "https://update.rezztor.com", "plan": "paid", "licence": "active",
            "core": "2026.10.08", "available": list(available), "error": error}  # fmt: skip


def request_file(spool):
    return json.loads((spool / "inbox" / "request.json").read_text())


def test_the_list_progress_and_download_pass_through_without_what_is_installed(spool):
    write_state(
        spool,
        phase="downloading",
        installed={"version": "2026.10.12", "base": "2026.10.08", "updated_at": None},
        updates=updates(entry("2026.10.12"), entry("2026.10.21", True), entry("2026.11.02")),
        progress={"done": 1, "total": 3, "current": "2026.10.21"},
        download={"version": "2026.10.21", "received": 1000, "size": 383436800},
    )
    answer = call(UpdateStatus)
    assert [item["version"] for item in answer["updates"]["available"]] == ["2026.10.21", "2026.11.02"]
    assert answer["progress"] == {"done": 1, "total": 3, "current": "2026.10.21"}
    assert answer["download"]["received"] == 1000 and answer["daily_check"] is True
    assert update_spool.waiting_count() == 2
    from api import bootstrap

    assert bootstrap._updates_available() == 2 and "updates_available" in bootstrap.BootstrapResponse.model_fields
    # An installation whose host has never asked has no list, and no banner.
    write_state(spool)
    assert call(UpdateStatus)["updates"] is None and update_spool.waiting_count() is None


def test_a_blocked_listing_passes_through_and_a_listing_without_it_still_does(spool):
    """The host keeps a next package it cannot read. The page hears that, and a list that does not name one is unchanged."""
    blocked = {"version": "2026.11.02", "reason": "format", "needs": 5}
    write_state(spool, updates={**updates(), "blocked": blocked})
    answer = call(UpdateStatus)["updates"]
    assert answer["blocked"] == blocked and answer["available"] == []
    assert answer["plan"] == "paid" and answer["error"] is None and answer["core"] == "2026.10.08"
    write_state(spool, updates=updates(entry("2026.10.21", True)))
    again = call(UpdateStatus)["updates"]
    assert again["available"][0]["version"] == "2026.10.21" and again.get("blocked") is None
    assert again["plan"] == "paid" and again["error"] is None


def test_a_release_image_names_no_server_and_a_uat_image_names_its_baked_one_on_every_call(spool, monkeypatch):
    from helpers import license_client

    answer = call(UpdateApply, {"action": "refresh", "server": "https://evil.example", "ca_pem": "x"})
    assert answer["ok"] is True
    assert request_file(spool) == {"id": answer["request"], "action": "refresh", "by": "alice"}

    (spool / "inbox" / "request.json").unlink()
    pem = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"
    monkeypatch.setattr(license_client, "uat_trust", lambda: {"root_keys": {"k": "v"}, "server": "https://license.uat.rezztor.test", "ca_pem": pem})
    service = {"server": "https://update.uat.rezztor.test", "ca_pem": pem}
    answer = call(UpdateApply, {"action": "refresh"})
    assert request_file(spool) == {"id": answer["request"], "action": "refresh", "by": "alice", **service}
    (spool / "inbox" / "request.json").unlink()
    answer = call(UpdateApply, {"action": "update_all"})
    assert request_file(spool) == {"id": answer["request"], "action": "update_all", "by": "alice", **service}
    (spool / "inbox" / "request.json").unlink()
    write_state(spool, updates=updates(entry("2026.10.21-uat", True)))
    answer = call(UpdateApply, {"action": "download", "version": "2026.10.21-uat"})
    assert request_file(spool) == {"id": answer["request"], "action": "download", "by": "alice",
                                   "version": "2026.10.21-uat", **service}  # fmt: skip


def test_only_the_first_downloadable_package_is_asked_for(spool):
    write_state(spool, updates=updates(entry("2026.10.21", True), entry("2026.11.02", True)))
    for version, said in (("2026.11.02", "only the first one"), ("../../x", "Name the version"), (None, "Name the version"),
                          (["2026.10.21"], "Name the version")):  # fmt: skip
        answer = call(UpdateApply, {"action": "download", "version": version})
        assert answer["ok"] is False and said in answer["error"], version
    assert not list((spool / "inbox").iterdir())

    answer = call(UpdateApply, {"action": "download", "version": "2026.10.21"})
    assert request_file(spool) == {"id": answer["request"], "action": "download", "by": "alice", "version": "2026.10.21"}

    (spool / "inbox" / "request.json").unlink()
    write_state(spool, updates=updates(entry("2026.10.21", False)))
    assert "plan" in call(UpdateApply, {"action": "download", "version": "2026.10.21"})["error"]


def test_update_all_is_one_request_and_nothing_is_handed_over_while_the_host_downloads(spool):
    answer = call(UpdateApply, {"action": "update_all"})
    assert request_file(spool) == {"id": answer["request"], "action": "update_all", "by": "alice"}
    (spool / "inbox" / "request.json").unlink()
    write_state(spool, phase="downloading")
    assert call(UpdateApply, {"action": "refresh"}) == {"ok": False, "error": "An update is being downloaded. Wait until it has finished."}


def test_a_community_build_never_asks_the_update_service(spool, monkeypatch):
    from helpers import license_client

    monkeypatch.setattr(license_client, "community_build", lambda: True)
    for action in ("refresh", "update_all"):
        assert call(UpdateApply, {"action": action})["error"] == "A Community build takes no paid update packages."
    assert update_spool.daily_refresh() is False
    assert not list((spool / "inbox").iterdir())


def test_the_download_and_check_are_audited_with_the_version(spool, monkeypatch):
    from plugins._audit.helpers import audit

    events = []
    monkeypatch.setattr(audit, "emit", lambda event, **fields: events.append((event, fields)))
    write_state(spool, updates=updates(entry("2026.10.21", True)))
    call(UpdateApply, {"action": "download", "version": "2026.10.21"})
    call(UpdateApply, {"action": "refresh"})
    assert events[0] == ("update.download", {"outcome": "ok", "detail": "", "version": "2026.10.21"})
    assert events[1][0] == "update.refresh" and events[1][1]["outcome"] == "failed"


DAY = 86400
NOW = 1_760_947_200.0  # 2025-10-20T08:00:00Z


def test_the_daily_check_asks_once_a_day_and_only_when_it_may(spool, monkeypatch):
    from datetime import datetime, timezone

    def stamp(seconds):
        return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace("+00:00", "Z")

    write_state(spool, updates=updates(checked_at=stamp(NOW - DAY + 60)))
    assert update_spool.daily_refresh(NOW) is False  # checked less than a day ago

    write_state(spool, updates=updates(checked_at=stamp(NOW - DAY - 60)))
    assert update_spool.daily_refresh(NOW) is True
    assert request_file(spool)["action"] == "refresh" and request_file(spool)["by"] == "daily check"
    assert update_spool.daily_refresh(NOW + 60) is False  # the request is still waiting

    # The host took it but never wrote a new checked_at (an older tool, or it failed): not asked again for a day.
    (spool / "inbox" / "request.json").unlink()
    assert update_spool.daily_refresh(NOW + 3600) is False
    assert update_spool.daily_refresh(NOW + DAY + 1) is True
    (spool / "inbox" / "request.json").unlink()

    monkeypatch.setattr(update_spool, "_last_asked", None)
    for phase in ("downloading", "installing", "restarting"):
        write_state(spool, phase=phase)
        assert update_spool.daily_refresh(NOW) is False
    write_state(spool)  # never checked
    monkeypatch.setattr(update_spool, "daily_check_enabled", lambda: False)
    assert update_spool.daily_refresh(NOW) is False


def test_an_install_makes_the_check_due_at_once(spool, monkeypatch):
    # A list heard before the last install offers nothing to download, so the next one is asked for right away.
    from datetime import datetime, timezone

    def stamp(seconds):
        return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace("+00:00", "Z")

    monkeypatch.setattr(update_spool, "_last_asked", None)
    installed = {"version": "2026.10.21", "base": "2026.10.08", "updated_at": stamp(NOW - 60)}
    write_state(spool, installed=installed, updates=updates(checked_at=stamp(NOW - 3600)))
    assert update_spool.daily_refresh(NOW) is True
    (spool / "inbox" / "request.json").unlink()
    assert update_spool.daily_refresh(NOW + 60) is False  # asked after the install: a day again
    write_state(spool, installed=installed, updates=updates(checked_at=stamp(NOW - 30)))
    assert update_spool.daily_refresh(NOW + 120) is False
    monkeypatch.setattr(update_spool, "_last_asked", None)
    monkeypatch.setattr(update_spool, "daily_check_enabled", lambda: False)
    write_state(spool, installed=installed, updates=updates(checked_at=stamp(NOW - 3600)))
    assert update_spool.daily_refresh(NOW) is False  # the switch still rules
    monkeypatch.setattr(update_spool, "daily_check_enabled", lambda: True)
    assert update_spool.daily_refresh(NOW) is True


def test_the_daily_check_does_nothing_without_a_spool(tmp_path, monkeypatch):
    import importlib

    monkeypatch.setenv("REZZTOR_UPDATE_SPOOL", str(tmp_path / "absent"))
    monkeypatch.setattr(update_spool, "daily_check_enabled", lambda: True)
    assert update_spool.daily_refresh() is False and not (tmp_path / "absent").exists()
    job = importlib.import_module("extensions.python.job_loop._60_update_check").UpdateCheck
    monkeypatch.setattr(update_spool, "daily_refresh", lambda: 1 / 0)
    job(agent=None).execute()  # a failure is printed, never raised into the loop


def test_the_switch_is_set_through_update_apply_and_shown_by_update_status(spool, monkeypatch):
    from helpers import settings

    saved = []
    monkeypatch.setattr(settings, "set_settings_delta", lambda delta, apply=True: saved.append((delta, apply)))
    assert call(UpdateApply, {"action": "daily_check", "enabled": False}) == {"ok": True, "daily_check": False}
    assert saved == [({"update_daily_check": False}, False)]
    for wrong in ("false", 0, None):
        assert call(UpdateApply, {"action": "daily_check", "enabled": wrong})["ok"] is False
    assert len(saved) == 1 and not list((spool / "inbox").iterdir())
    monkeypatch.setattr(update_spool, "daily_check_enabled", lambda: False)
    assert call(UpdateStatus)["daily_check"] is False


def test_the_switch_is_an_instance_setting_that_is_on_by_default(database, tmp_path, monkeypatch):
    from helpers import dotenv, files, secrets, settings
    from testsupport import build_instance_root

    monkeypatch.setattr(files, "_base_dir", build_instance_root(tmp_path))
    monkeypatch.setattr(dotenv, "get_dotenv_file_path", lambda: str(tmp_path / "dotenv"))
    monkeypatch.setattr(secrets.SecretsManager, "_instances", {})
    monkeypatch.setattr(settings, "_settings", None)
    monkeypatch.setattr(settings, "_apply_settings", lambda *_args, **_kwargs: None)
    assert update_spool.daily_check_enabled() is True
    settings.set_settings_delta({"update_daily_check": False}, apply=False)
    monkeypatch.setattr(settings, "_settings", None)
    assert update_spool.daily_check_enabled() is False


OS_STATE = {"version": "2026.10.21", "snapshot": "20261020T000000Z", "applied": 13, "reboot_needed": True, "boot": "b",
            "packages": [{"name": "linux-image-amd64", "from": "6.12.107-1", "to": "6.12.113-1"}], "error": None,
            "at": "2026-10-21T09:00:00Z"}  # fmt: skip


def test_debian_updates_pass_through_and_a_restart_of_the_appliance_is_asked_only_when_it_is_needed(spool, monkeypatch):
    """ADR 0092: the host says what Debian updates it applied and whether the appliance needs a restart; the page
    may ask for that restart, and only then. The host refuses it too."""
    from plugins._audit.helpers import audit

    events = []
    monkeypatch.setattr(audit, "emit", lambda event, **fields: events.append((event, fields)))
    assert call(UpdateStatus)["os"] is None
    assert call(UpdateApply, {"action": "reboot"}) == {"ok": False, "error": "The appliance does not need a restart."}
    assert not list((spool / "inbox").iterdir())
    write_state(spool, os=OS_STATE)
    assert call(UpdateStatus)["os"] == OS_STATE
    answer = call(UpdateApply, {"action": "reboot"})
    assert request_file(spool) == {"id": answer["request"], "action": "reboot", "by": "alice"}
    assert [event for event, _ in events] == ["update.reboot", "update.reboot"] and events[1][1]["outcome"] == "ok"
    assert "reboot" in update_spool.ACTIONS
