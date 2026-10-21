"""The licence client against a stand-in licence server, through a whole life.

The server here is built from `rezlic`'s own signing with keys made for the test, and it speaks HTTP on
127.0.0.1, so the client is exercised through its real transport. The product's embedded root list is replaced
in these tests only; `test_the_embedded_root_list_is_the_whole_trust` checks the real one. No test reaches a
network: `REZZTOR_LICENCE_SERVER_URL` points every call at the stand-in.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from helpers import api_policy, license_client, license_entitlements
from rezlic import formats, keys
from rezlic.formats import DAY, b64d, b64e, hash_b64

REAL_STATE = license_client.state  # taken at import, before conftest's `licensed_installation` replaces it
T0 = int(time.time())
IID = "iid-test-0001"
LIC = "REZ-TEST-0001"


class FakeServer:
    """spec/protocol.md for one licence, in memory. `mode` makes it misbehave; `tamper` rewrites an answer."""

    def __init__(self):
        self.now = T0
        self.root = keys.new_private_key()
        self.lease_key = keys.new_private_key()
        self.roots = {"root-test": keys.export_public(self.root)}
        self.cert = formats.sign("cert", "root-test", {
            "typ": "cert", "kid": "lease-test", "pub": keys.export_public(self.lease_key), "use": ["lease"],
            "nbf": T0 - DAY, "exp": T0 + 95 * DAY, "limits": {}}, self.root)
        self.key = keys.generate()
        self.acts: dict[str, dict] = {}
        self.requests: list[tuple[str, str, dict | None, str]] = []  # method, path, body, user agent
        self.raw: list[bytes] = []  # the bodies as they arrived
        self.mode = "ok"
        self.tamper = None
        self.revoked_at = 0
        self.knows_devices = True  # False: a server from before ADR 0093, whose check-in forbids extra fields
        self.retired: dict[str, dict] = {}  # tokens of activations this one replaced; still valid for deactivate
        self.path_mode: dict[str, str] = {}  # a path's own mode, else `mode`
        self.issue()

    def licence_payload(self, **over) -> dict:
        return {"typ": "licence", "lic": LIC, "ver": 1, "customer": "Example Bank", "kind": "subscription",
                "edition": "standard", "features": ["azure", "solarwinds"], "seats": 50, "instances": 1,
                "iat": T0, "nbf": T0, "exp": T0 + 365 * DAY, "lease_days": 30, "grace_days": 30,
                "offline": False, **over}

    def issue(self, **over) -> None:
        self.payload = self.licence_payload(**over)
        self.licence = formats.sign("licence", "root-test", self.payload, self.root)

    def issue_other(self, lic: str) -> str:
        """Point the next activation at another licence. Returns its key. The token now held stays valid
        for deactivate."""
        key = keys.generate()
        self.key = key
        self.issue(lic=lic)
        act = self.acts.get(IID)
        if act:
            act["nonce"] = None
            act["answer"] = {}
        return key

    def lease(self, act: dict, body: dict, key=None, **over) -> dict:
        act["seq"] += 1
        status = "released" if act["released"] else "revoked" if self.revoked_at else "ok"
        payload = {
            "typ": "lease", "lic": self.payload["lic"], "ver": self.payload["ver"], "iid": body["iid"], "seq": act["seq"],
            "prev": act["prev"], "iat": self.now, "now": self.now, "nonce": body["nonce"], "status": status,
            "exp": min(self.now + self.payload["lease_days"] * DAY, self.payload["exp"]),
            "notice_until": self.revoked_at + 7 * DAY if status == "revoked" else 0,
            "licence_hash": hash_b64(b64d(self.licence["payload"])), **over}
        envelope = formats.sign("lease", "lease-test", payload, key or self.lease_key)
        act["prev"] = hash_b64(b64d(envelope["payload"]))
        return envelope

    def handle(self, path: str, body: dict | None) -> tuple[int, dict]:
        refused = (403, {"error": "refused"})
        if path == "/v1/keys":
            return 200, {"certs": [self.cert]}
        assert body is not None
        if path == "/v1/activate":
            try:
                if keys.normalise(body["key"]) != keys.normalise(self.key):
                    return refused
            except keys.ActivationKeyError:
                return refused
            act = self.acts.setdefault(body["iid"], {"seq": 0, "prev": "", "nonce": None, "answer": {}})
            if act["nonce"] == body["nonce"] and "token" in act["answer"]:
                return 200, act["answer"]
            if act.get("token"):
                self.retired.setdefault(act["token"], {"released": bool(act.get("released")), "lic": act.get("lic", LIC)})
            act.update(token=b64e(secrets.token_bytes(32)), released=False, lic=self.payload["lic"])
            answer = {"licence": self.licence, "lease": self.lease(act, body), "certs": [self.cert],
                      "token": act["token"]}
        else:
            act = self.acts.get(body["iid"])
            if path == "/v1/deactivate":
                token = body.get("token")
                if not isinstance(token, str):
                    return refused
                if act is not None and token == act.get("token"):
                    act["released"] = True
                    return 200, {"ok": True}
                retired = self.retired.get(token)
                if retired is not None:
                    retired["released"] = True
                    return 200, {"ok": True}
                return refused
            if act is None or body.get("token") != act["token"]:
                return refused
            assert path == "/v1/checkin"
            if not self.knows_devices and "devices" in body:
                return 400, {"error": "bad_request"}
            if act["nonce"] == body["nonce"] and "token" not in act["answer"]:
                return 200, act["answer"]
            answer = {"lease": self.lease(act, body), "certs": [self.cert]}
            if self.payload["ver"] > body["ver"]:
                answer["licence"] = self.licence
        act["nonce"], act["answer"] = body["nonce"], answer
        return 200, answer

    def calls(self, path: str) -> list[dict]:
        return [body for _, p, body, _ in self.requests if p == path and body is not None]


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _serve(self):
        fake: FakeServer = self.server.fake  # type: ignore[attr-defined]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        body = json.loads(raw) if raw else None
        fake.requests.append((self.command, self.path, body, self.headers.get("User-Agent", "")))
        fake.raw.append(raw)
        mode = fake.path_mode.get(self.path, fake.mode)
        if mode == "nothing":
            return  # the connection closes with no answer
        if mode == "500":
            status, raw = 500, b'{"error":"unavailable"}'
        elif mode == "403":
            status, raw = 403, b'{"error":"refused"}'
        elif mode == "redirect":
            self.send_response(307)
            self.send_header("Location", "http://127.0.0.1:1/elsewhere")
            self.end_headers()
            return
        else:
            status, answer = fake.handle(self.path, body)
            if mode == "lost":
                return  # the server acted; the answer never arrives
            if fake.tamper and status == 200:
                answer = fake.tamper(dict(answer), body)
            raw = b"<html>not the protocol</html>" if mode == "garbage" else json.dumps(answer).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    do_GET = do_POST = _serve


def _stamp_enterprise(monkeypatch, tmp_path) -> None:
    """Point the product at an Enterprise stamp, so today's licence path is what the test exercises."""
    from helpers import files

    path = tmp_path / "build_info.json"
    path.write_text(json.dumps({"version": "2026.10.21", "licence": "enterprise"}))
    real = files.get_abs_path

    def routed(*parts):
        if parts == ("build_info.json",):
            return str(path)
        return real(*parts)

    monkeypatch.setattr(files, "get_abs_path", routed)
    license_client.enterprise_build.cache_clear()
    license_client.uat_trust.cache_clear()


@pytest.fixture
def server(database, monkeypatch, tmp_path):
    fake = FakeServer()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    httpd.fake = fake  # type: ignore[attr-defined]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setenv(license_client.ENV_SERVER, f"http://127.0.0.1:{httpd.server_address[1]}")
    monkeypatch.setattr(license_client, "state", REAL_STATE)
    monkeypatch.setattr(license_client, "ROOT_KEYS", fake.roots)
    monkeypatch.setattr(license_client, "installation_id", lambda: IID)
    monkeypatch.setattr(license_client, "product_version", lambda: "9.9-test")
    monkeypatch.setattr(license_client, "seats_used", lambda: 7)
    monkeypatch.setattr(license_client, "devices_used", lambda: 3)
    monkeypatch.setattr(license_client, "_devices_refused_at", 0)
    _stamp_enterprise(monkeypatch, tmp_path)
    # `raising=False`: the module without the fix has no such name, and the test then fails on what it asserts
    # rather than on a missing attribute in the fixture.
    monkeypatch.setattr(license_client, "_devices_sent_again_at", 0, raising=False)
    yield fake
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def active(server):
    """An installation activated at T0."""
    license_client.activate(server.key, now=T0)
    return server


def stored() -> dict:
    """What the installation holds: the documents, the token and the mark."""
    row = license_client._load()
    return {name: row[name] for name in ("licence", "certs", "lease", "token", "mark")}


def at(seconds: int) -> dict:
    return license_client.state(seconds)


def day(server: FakeServer, n: float) -> int:
    """Move the server's clock to day n and give the product the same time."""
    server.now = T0 + int(n * DAY)
    return server.now


# --- trust -------------------------------------------------------------------------------------------------------

def test_the_embedded_root_list_is_the_whole_trust(database, monkeypatch):
    assert license_client.ROOT_KEYS == {"root-2026a": "upIWLEFjeb667i6SA-PnN-t2-HKq7xCG4rRTJfQiHuc"}
    assert license_client.DEFAULT_SERVER == "https://license.rezztor.com"
    # Nothing an administrator can set reaches the list: the settings document has no key for it.
    from helpers import settings

    assert not [name for name in settings.get_default_settings() if "licen" in name.lower()]


def test_documents_of_another_root_are_refused_by_the_real_list(server, monkeypatch):
    monkeypatch.setattr(license_client, "ROOT_KEYS", {"root-2026a": "upIWLEFjeb667i6SA-PnN-t2-HKq7xCG4rRTJfQiHuc"})
    with pytest.raises(license_client.LicenceError, match="unknown_kid"):
        license_client.activate(server.key, now=T0)
    assert stored()["licence"] is None and at(T0)["state"] == "restricted"


@pytest.mark.parametrize("url,ok", [
    ("https://license.example.test", True), ("http://localhost:8420", True), ("http://127.0.0.1:1", True),
    ("http://license.example.test", False), ("ftp://localhost", False), ("license.example.test", False),
])
def test_the_server_address_is_https_or_this_machine(monkeypatch, url, ok):
    monkeypatch.setenv(license_client.ENV_SERVER, url)
    if ok:
        assert license_client.server_url() == url
    else:
        with pytest.raises(license_client.LicenceError):
            license_client.server_url()


# --- no licence, and the key check -------------------------------------------------------------------------------

def test_an_installation_with_no_licence_is_restricted(server):
    assert at(T0)["state"] == "restricted" and at(T0)["reason"] == "no_licence"
    with pytest.raises(license_entitlements.LicenceRestrictedError, match="licence page"):
        license_entitlements.require_agent_runs_allowed()
    assert license_entitlements.feature_enabled("azure") is False


@pytest.mark.parametrize("typed", ["", "REZ-AAAAA", "REZ-ZZZZZ-ZZZZZ-ZZZZZ-ZZZZZ-ZZZZZ"])
def test_a_mistyped_activation_key_makes_no_call(server, typed):
    wrong_check = server.key[:-1] + ("0" if server.key[-1] != "0" else "1")
    for key in (typed, wrong_check):
        with pytest.raises(license_client.LicenceError):
            license_client.activate(key, now=T0)
    assert server.requests == [] and license_client._load()["pending"] is None


def test_a_refused_key_is_not_kept(server):
    other = keys.generate()
    with pytest.raises(license_client.LicenceError, match="refused"):
        license_client.activate(other, now=T0)
    row = license_client._load()
    assert row["pending"] is None and row["licence"] is None and row["last_result"].endswith("refused the request.")


# --- a whole life ------------------------------------------------------------------------------------------------

def test_a_whole_life(active):
    server = active
    # Activation: one call, as the protocol words it.
    [(method, path, body, agent)] = server.requests
    assert (method, path, agent) == ("POST", "/v1/activate", "RezztorEAgent/9.9-test")
    assert set(body) == {"key", "iid", "version", "nonce"} and body["iid"] == IID
    assert 16 <= len(b64d(body["nonce"])) <= 32
    state = at(T0)
    assert (state["state"], state["reason"]) == ("licensed", "ok")
    assert state["licence"]["customer"] == "Example Bank" and state["lease"]["seq"] == 1
    assert license_client._load()["token"] == server.acts[IID]["token"]
    assert license_client._load()["mark"] == T0 and license_client._load()["pending"] is None

    # Forty days, one tick a minute would be the job loop; one a day shows the same schedule.
    license_client.tick(T0 + 3600)
    assert server.calls("/v1/checkin") == []  # not due: the next call is a day after the activation
    for n in range(1, 41):
        license_client.tick(day(server, n))
        assert at(server.now)["state"] == "licensed"
    checkins = server.calls("/v1/checkin")
    assert len(checkins) == 40 and len({c["nonce"] for c in checkins}) == 40
    assert set(checkins[0]) == {"lic", "ver", "iid", "token", "seq", "prev", "seats", "devices", "version",
                                "nonce"}
    assert [c["seq"] for c in checkins] == list(range(1, 41)) and checkins[0]["seats"] == 7
    assert checkins[0]["devices"] == 3
    assert at(server.now)["lease"]["seq"] == 41 and license_client._load()["mark"] == server.now
    assert len([r for r in server.requests if r[1] == "/v1/keys"]) == 40

    # A lost answer: the server issued lease 42, the product never saw it. The request stays pending, also
    # across a restart (it lives in the table, not in memory), and is repeated with the same nonce.
    server.mode = "lost"
    license_client.tick(day(server, 41))
    row = license_client._load()
    assert server.acts[IID]["seq"] == 42 and at(server.now)["lease"]["seq"] == 41
    assert row["pending"]["kind"] == "checkin" and row["last_result"].endswith("could not be reached.")
    server.mode = "ok"
    license_client.tick(server.now + 30)   # inside the one-minute wait: nothing is sent
    assert len(server.calls("/v1/checkin")) == 41
    license_client.tick(server.now + 60)
    repeated = server.calls("/v1/checkin")[-2:]
    assert repeated[0] == repeated[1] == row["pending"]["body"]
    assert server.acts[IID]["seq"] == 42 and at(server.now)["lease"]["seq"] == 42  # the chain did not move twice
    assert license_client._load()["pending"] is None

    # A renewal arrives with a check-in.
    server.issue(ver=2, seats=80, exp=T0 + 730 * DAY, features=["azure", "mail"])
    license_client.tick(day(server, 43))
    state = at(server.now)
    assert (state["licence"]["ver"], state["licence"]["seats"], state["state"]) == (2, 80, "licensed")
    assert state["licence"]["features"] == ["azure", "mail"] and state["licence"]["exp"] == T0 + 730 * DAY

    # Revocation: a signed notice of seven days, then Restricted. Check-ins go on and say the same.
    server.revoked_at = day(server, 44)
    license_client.tick(server.now)
    state = at(server.now)
    assert (state["state"], state["reason"], state["days_left"]) == ("licensed", "revoked_notice", 7)
    license_client.tick(day(server, 50))
    assert at(server.now)["state"] == "licensed"
    license_client.tick(day(server, 52))
    state = at(server.now)
    assert (state["state"], state["reason"]) == ("restricted", "revoked")
    assert state["licence"]["lic"] == LIC  # nothing was deleted


def test_grace_one_grace_two_then_restricted_when_the_server_is_gone(active):
    server = active
    before = stored()
    server.mode = "nothing"
    attempts = []
    clock = T0 + DAY
    for _ in range(6):
        license_client.tick(clock)
        row = license_client._load()
        attempts.append(row["next_attempt_at"] - row["last_attempt_at"])
        clock = row["next_attempt_at"]
    assert attempts == [60, 300, 1800, 3600, 3600, 3600]  # 1, 5, 30 minutes, then hourly
    sent = [raw for raw, request in zip(server.raw, server.requests) if request[1] == "/v1/checkin"]
    assert len(sent) == 6 and len(set(sent)) == 1 and len(sent[0]) <= 4096  # one request, the same bytes each time

    lease_end = T0 + 30 * DAY
    assert at(lease_end + 3600)["state"] == "licensed"
    assert at(lease_end + 3601)["state"] == "grace1"
    assert at(lease_end + 14 * DAY)["state"] == "grace1"
    grace2 = at(lease_end + 14 * DAY + 1)
    assert (grace2["state"], grace2["reason"], grace2["days_left"]) == ("grace2", "ended", 16)
    assert at(lease_end + 30 * DAY)["state"] == "grace2"
    over = at(lease_end + 30 * DAY + 1)
    assert (over["state"], over["reason"]) == ("restricted", "grace_over")
    assert {**stored(), "mark": 0} == {**before, "mark": 0}  # the licence and the lease are still held


def test_deactivate_releases_and_discards_the_lease_and_the_token(active):
    server = active
    license_client.deactivate(now=T0 + 60)
    [body] = server.calls("/v1/deactivate")
    assert set(body) == {"lic", "iid", "token", "nonce"} and server.acts[IID]["released"] is True
    row = license_client._load()
    assert row["lease"] is None and row["token"] is None and row["pending"] is None
    state = at(T0 + 60)
    assert (state["state"], state["reason"]) == ("restricted", "no_lease") and state["licence"]["lic"] == LIC
    with pytest.raises(license_client.LicenceError, match="no lease to renew"):
        license_client.checkin(now=T0 + 120)
    license_client.tick(T0 + 2 * DAY)
    assert server.calls("/v1/checkin") == []
    # The same installation id continues its activation with the key.
    license_client.activate(server.key, now=T0 + 3 * DAY)
    assert at(T0 + 3 * DAY)["state"] == "licensed"


def test_a_refused_deactivation_keeps_the_lease_and_the_token(active):
    active.acts[IID]["token"] = "another-token"
    before = stored()
    with pytest.raises(license_client.LicenceError, match="refused"):
        license_client.deactivate(now=T0 + 60)
    assert stored() == before and at(T0 + 60)["state"] == "licensed"


OTHER = "REZ-TEST-0002"


def _audit_events(monkeypatch):
    from plugins._audit.helpers import audit

    events = []
    monkeypatch.setattr(audit, "emit", lambda event, **fields: events.append((event, fields)))
    return events


def test_reactivating_the_same_licence_releases_nothing(active):
    server = active
    before = license_client._load()["token"]
    assert license_client.activate(server.key, now=T0 + 60) is None
    assert server.calls("/v1/deactivate") == []
    row = license_client._load()
    assert row["pending"] is None and row["token"] != before and row["lease"] is not None
    assert at(T0 + 60)["licence"]["lic"] == LIC and at(T0 + 60)["releasing"] is None


def test_activating_another_licence_releases_the_old_one_with_its_token(active, monkeypatch):
    server = active
    events = _audit_events(monkeypatch)
    old_token = license_client._load()["token"]
    replaced = license_client.activate(server.issue_other(OTHER), now=T0 + 60)
    assert replaced == LIC
    [body] = server.calls("/v1/deactivate")
    assert set(body) == {"lic", "iid", "token", "nonce"} and body["token"] == old_token and body["lic"] == LIC
    assert server.retired[old_token]["released"] is True
    paths = [path for _, path, _, _ in server.requests]
    assert paths.index("/v1/activate", 1) < paths.index("/v1/deactivate")
    row = license_client._load()
    assert row["token"] not in (None, old_token) and row["lease"] is not None and row["pending"] is None
    state = at(T0 + 60)
    assert (state["licence"]["lic"], state["state"], state["releasing"]) == (OTHER, "licensed", None)
    assert old_token not in json.dumps(state)
    released = [fields for event, fields in events if event == "licence.deactivate"]
    assert released == [{"outcome": "ok", "detail": "", "lic": LIC, "state": "licensed"}]
    assert old_token not in json.dumps(released)


def test_a_failed_release_of_the_previous_licence_is_retried_by_tick_and_survives_restart(active, monkeypatch):
    """The old token lives in the pending row, which is the table, so a restart repeats the same request."""
    server = active
    events = _audit_events(monkeypatch)
    old_token = license_client._load()["token"]
    server.path_mode["/v1/deactivate"] = "nothing"
    assert license_client.activate(server.issue_other(OTHER), now=T0 + 60) == LIC
    row = license_client._load()
    pending = row["pending"]
    assert pending["kind"] == "deactivate" and pending["body"]["token"] == old_token and pending["body"]["lic"] == LIC
    assert set(pending["body"]) == {"lic", "iid", "token", "nonce"}
    assert row["token"] not in (None, old_token) and row["lease"] is not None
    assert at(T0 + 60)["releasing"] == LIC and at(T0 + 60)["licence"]["lic"] == OTHER
    assert old_token not in json.dumps(at(T0 + 60)) and events == []
    saved = pending
    server.path_mode.clear()
    license_client.tick(T0 + 90)  # inside the one-minute wait: the stored request is not sent again
    assert license_client._load()["pending"] == saved and len(server.calls("/v1/deactivate")) == 1
    license_client.tick(T0 + 120)
    assert server.calls("/v1/deactivate")[-1] == saved["body"] and server.retired[old_token]["released"] is True
    row = license_client._load()
    assert row["pending"] is None and row["token"] not in (None, old_token) and row["lease"] is not None
    assert at(T0 + 120)["state"] == "licensed" and at(T0 + 120)["releasing"] is None
    assert [fields for event, fields in events if event == "licence.deactivate"] == [
        {"outcome": "ok", "detail": "", "lic": LIC, "state": "licensed"}]


def test_a_refused_release_of_the_previous_licence_is_dropped(active, monkeypatch):
    server = active
    events = _audit_events(monkeypatch)
    old_token = license_client._load()["token"]
    server.path_mode["/v1/deactivate"] = "403"
    assert license_client.activate(server.issue_other(OTHER), now=T0 + 60) is None
    body = server.calls("/v1/deactivate")[-1]
    assert body["token"] == old_token and body["lic"] == LIC
    row = license_client._load()
    assert row["pending"] is None and row["token"] not in (None, old_token) and row["lease"] is not None
    assert at(T0 + 60)["licence"]["lic"] == OTHER and at(T0 + 60)["releasing"] is None
    license_client.tick(T0 + 120)
    assert len(server.calls("/v1/deactivate")) == 1
    [refused] = [fields for event, fields in events if event == "licence.deactivate"]
    assert refused["outcome"] == "failed" and refused["lic"] == LIC and "refused" in refused["detail"]
    assert old_token not in json.dumps(refused)


def test_releasing_this_installation_finishes_the_previous_release_first(active):
    server = active
    old_token = license_client._load()["token"]
    server.path_mode["/v1/deactivate"] = "nothing"
    license_client.activate(server.issue_other(OTHER), now=T0 + 60)
    new_token = license_client._load()["token"]
    server.path_mode.clear()
    license_client.deactivate(now=T0 + 120)
    assert [body["token"] for body in server.calls("/v1/deactivate")] == [old_token, old_token, new_token]
    assert server.retired[old_token]["released"] is True and server.acts[IID]["released"] is True
    row = license_client._load()
    assert row["lease"] is None and row["token"] is None and row["pending"] is None


def test_the_activate_answer_names_a_released_previous_licence(active, monkeypatch):
    post = _client(monkeypatch)
    old = license_client._load()["token"]
    response = post("license_activate", {"action": "activate", "key": active.issue_other(OTHER)})
    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is True and body["licence"]["released_lic"] == LIC and body["licence"]["releasing"] is None
    assert body["licence"]["licence"]["lic"] == OTHER
    text = response.get_data(as_text=True)
    assert old not in text and active.acts[IID]["token"] not in text


def test_the_activate_answer_names_a_previous_licence_still_to_be_released(active, monkeypatch):
    active.path_mode["/v1/deactivate"] = "nothing"
    post = _client(monkeypatch)
    old = license_client._load()["token"]
    response = post("license_activate", {"action": "activate", "key": active.issue_other(OTHER)})
    body = response.get_json()
    assert body["ok"] is True and "released_lic" not in body["licence"] and body["licence"]["releasing"] == LIC
    assert old not in response.get_data(as_text=True)


def test_a_clock_set_back_extends_nothing(active):
    server = active
    server.mode = "nothing"
    late = T0 + 70 * DAY
    license_client.tick(late)  # the clock reads day 70: the mark follows, the lease and its grace are over
    assert at(late)["state"] == "restricted"
    back = at(T0 + DAY)        # the clock is set back to day 1
    assert (back["state"], back["clock_behind"], back["now"]) == ("restricted", True, late)
    # A check-in corrects it: the lease carries the server's time, and the mark is set to it.
    server.mode = "ok"
    day(server, 70)
    license_client.checkin(now=T0 + DAY)
    fixed = at(T0 + DAY)
    assert (fixed["state"], fixed["clock_behind"], fixed["now"]) == ("licensed", True, late)
    assert fixed["end"] == late + 30 * DAY


# --- answers that must change nothing ----------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["500", "garbage", "nothing", "redirect"])
def test_a_server_that_answers_wrongly_changes_nothing(active, mode):
    server = active
    before = stored()
    server.mode = mode
    with pytest.raises(license_client.LicenceError):
        license_client.checkin(now=T0 + DAY)
    row = license_client._load()
    assert stored() == before and at(T0 + DAY)["state"] == "licensed"
    assert row["pending"]["kind"] == "checkin" and row["failures"] == 1 and row["last_result"] != "ok"
    assert row["last_success_at"] == T0


@pytest.mark.parametrize("mode", ["500", "garbage", "nothing", "redirect"])
def test_a_failed_activation_stores_nothing_and_is_repeated(server, mode):
    server.mode = mode
    with pytest.raises(license_client.LicenceError):
        license_client.activate(server.key, now=T0)
    row = license_client._load()
    assert row["licence"] is None and row["lease"] is None and row["token"] is None
    nonce = row["pending"]["body"]["nonce"]
    server.mode = "ok"
    license_client.tick(T0 + 61)  # the background repeats it, unchanged
    assert at(T0 + 61)["state"] == "licensed"
    assert [c["nonce"] for c in server.calls("/v1/activate")][-1] == nonce


def _forged_lease(answer, body):
    attacker = keys.new_private_key()
    payload = json.loads(b64d(answer["lease"]["payload"]))
    payload["exp"] += 300 * DAY
    return {**answer, "lease": formats.sign("lease", "lease-test", payload, attacker)}


def _lease_for_another_installation(fake):
    def tamper(answer, body):
        act = {"seq": 5, "prev": hash_b64(b"x"), "released": False}
        return {**answer, "lease": fake.lease(act, {**body, "iid": "another-installation"})}
    return tamper


def _lease_with_an_old_nonce(fake):
    def tamper(answer, body):
        act = {"seq": 5, "prev": hash_b64(b"x"), "released": False}
        return {**answer, "lease": fake.lease(act, {**body, "nonce": b64e(secrets.token_bytes(16))})}
    return tamper


@pytest.mark.parametrize("case,code", [
    ("forged", "bad_signature"), ("other_installation", "wrong_iid"), ("replayed", "wrong_nonce"),
    ("other_licence", "other_lic"), ("older_licence", "lower_ver"),
])
def test_an_answer_that_does_not_verify_changes_nothing(active, case, code):
    server = active
    if case == "older_licence":
        server.issue(ver=3)
        license_client.checkin(now=T0 + 60)
    before = stored()

    def another_licence(answer, body):
        payload = server.licence_payload(lic="REZ-OTHER-0002", ver=9) if case == "other_licence" \
            else server.licence_payload(ver=2)
        return {**answer, "licence": formats.sign("licence", "root-test", payload, server.root)}

    server.tamper = {"forged": _forged_lease, "other_installation": _lease_for_another_installation(server),
                     "replayed": _lease_with_an_old_nonce(server), "other_licence": another_licence,
                     "older_licence": another_licence}[case]
    with pytest.raises(license_client.LicenceError, match=code):
        license_client.checkin(now=T0 + DAY)
    assert stored() == before and at(T0 + DAY)["state"] == "licensed"


def test_a_forged_activation_stores_nothing(server):
    attacker = keys.new_private_key()

    def tamper(answer, body):
        return {**answer, "licence": formats.sign("licence", "root-test", server.licence_payload(seats=9999), attacker)}

    server.tamper = tamper
    with pytest.raises(license_client.LicenceError, match="bad_signature"):
        license_client.activate(server.key, now=T0)
    assert stored()["licence"] is None and stored()["token"] is None and at(T0)["state"] == "restricted"


def test_an_activation_answer_without_a_proper_token_stores_nothing(server):
    server.tamper = lambda answer, body: {**answer, "token": "short"}
    with pytest.raises(license_client.LicenceError, match="bad_token"):
        license_client.activate(server.key, now=T0)
    assert stored()["licence"] is None


def test_a_stored_licence_that_was_swapped_is_invalid(active):
    from psycopg.types.json import Jsonb

    from helpers import db

    forged = formats.sign("licence", "root-test", active.licence_payload(seats=9999), keys.new_private_key())
    with db.unscoped_transaction() as conn:
        conn.execute("UPDATE licence_state SET licence = %s", (Jsonb(forged),))
    state = at(T0)
    assert (state["state"], state["reason"], state["problem"]) == ("restricted", "invalid", "bad_signature")


# --- offline -----------------------------------------------------------------------------------------------------

def offline_file(server, iid=IID, **over) -> str:
    payload = server.licence_payload(lic="REZ-OFFLINE-0001", offline=True, offline_iid=iid, **over)
    return formats.dump_envelope(formats.sign("licence", "root-test", payload, server.root))


def test_an_offline_licence_for_this_installation(server):
    code = license_client.request_code("SOC primary")
    from rezlic.request_code import parse_request_code

    assert parse_request_code(code).instance_id == IID
    license_client.load_licence_file(offline_file(server))
    state = at(T0)
    assert (state["state"], state["lease"], state["licence"]["offline"]) == ("licensed", None, True)
    license_client.tick(T0 + 2 * DAY)
    assert server.requests == []  # an offline installation never calls the server
    assert at(T0 + 365 * DAY + 3601)["state"] == "grace1"
    # A renewal is the same exchange with a higher version; an older file is refused.
    license_client.load_licence_file(offline_file(server, ver=2, nbf=T0 + 300 * DAY, exp=T0 + 600 * DAY))
    assert at(T0 + 400 * DAY)["state"] == "licensed"
    with pytest.raises(license_client.LicenceError, match="older"):
        license_client.load_licence_file(offline_file(server))


def test_an_offline_licence_for_another_installation_is_refused(active):
    before = stored()
    with pytest.raises(license_client.LicenceError, match="another installation"):
        license_client.load_licence_file(offline_file(active, iid="another-installation"))
    for text in ("not a licence", "{}", formats.dump_envelope(active.licence)):
        with pytest.raises(license_client.LicenceError):
            license_client.load_licence_file(text)
    assert stored() == before and at(T0)["state"] == "licensed"


def test_an_offline_licence_replaces_an_online_one_and_its_lease(active):
    license_client.load_licence_file(offline_file(active))
    row = license_client._load()
    assert row["lease"] is None and row["token"] is None and at(T0)["licence"]["lic"] == "REZ-OFFLINE-0001"


# --- what Restricted stops, and what the endpoints say -----------------------------------------------------------

def test_seats_are_the_enabled_accounts(monkeypatch):
    from helpers import users

    monkeypatch.setattr(users, "list_users", lambda: [{"id": "a"}, {"id": "b", "disabled": True}, {"id": "c"}])
    assert license_client.seats_used() == 2


def test_features_come_from_the_licence(active):
    assert license_entitlements.feature_enabled("azure") is True
    assert license_entitlements.feature_enabled("mail") is False
    assert set(license_entitlements.FEATURE_PLUGINS) == {
        "network_automation", "azure", "azure_change", "solarwinds", "siem_forward", "mail", "auto_skills",
        "attachments"}
    license_entitlements.require_agent_runs_allowed()


# --- network devices (ADR 0093): a limit that warns and refuses nothing ------------------------------------------

def _verdict(state="licensed", kind="subscription", edition="free", **licence) -> dict:
    return {"state": state, "licence": {"kind": kind, "edition": edition, **licence}}


@pytest.mark.parametrize("verdict, allowed", [
    (_verdict(edition="free"), 100),
    (_verdict(edition="pro"), 500),
    (_verdict(edition="ultra"), 500),
    (_verdict(state="grace2", edition="pro"), 500),
    (_verdict(edition="standard"), None),                   # editions from before the plans: no limit
    (_verdict(edition="plus"), None),
    (_verdict(kind="subscription", edition="lab"), None),   # an edition that is not in the table
    (_verdict(kind="trial", edition="free"), None),         # a trial: no limit
    (_verdict(kind="trial", edition="trial"), None),
    (_verdict(edition="free", devices=250), 250),           # the signed field wins over the edition
    (_verdict(edition="standard", devices=40), 40),
    (_verdict(kind="trial", edition="pro", devices=20), 20),
    (_verdict(state="restricted", edition="free"), None),
])
def test_the_device_limit_is_the_signed_field_else_the_edition(verdict, allowed):
    assert license_entitlements.devices_allowed(verdict) == allowed
    assert license_entitlements.EDITION_DEVICES == {"free": 100, "pro": 500, "ultra": 500}


@pytest.mark.parametrize("used, allowed, warning", [
    (0, 100, None), (89, 100, None), (90, 100, "near"), (100, 100, "near"), (101, 100, "over"),
    (449, 500, None), (450, 500, "near"), (501, 500, "over"), (10_000, None, None),
])
def test_the_device_warning_comes_at_ninety_percent_and_over_the_limit(used, allowed, warning):
    assert license_entitlements.devices_warning(used, allowed) == warning


def test_devices_are_the_active_inventory_and_the_azure_network_boxes(database):
    from helpers import db

    with db.unscoped_transaction() as conn:
        for n, status in enumerate(("active", "active", "planned", "staged", "offline", "spare", "faulty",
                                    "decommissioned")):
            conn.execute("INSERT INTO inventory_device (name, status) VALUES (%s, %s)", (f"dev-{n}", status))
        account = conn.execute("INSERT INTO cloud_account (provider, native_id) VALUES ('azure', 't') "
                               "RETURNING id").fetchone()[0]
        sub = conn.execute("INSERT INTO cloud_container (account_id, kind, native_id, name) "
                           "VALUES (%s, 'subscription', 's', 's') RETURNING id", (account,)).fetchone()[0]
        kinds = [(t, None) for t in license_client.DEVICE_CLOUD_TYPES] + [
            ("microsoft.network/azurefirewalls", "now()"),            # removed in the last pull
            ("microsoft.network/firewallpolicies", None), ("microsoft.network/connections", None),
            ("microsoft.network/localnetworkgateways", None), ("microsoft.network/virtualnetworks", None),
            ("microsoft.compute/virtualmachines", None)]
        for n, (rtype, removed) in enumerate(kinds):
            conn.execute(
                "INSERT INTO cloud_resource (account_id, subscription_id, container_id, native_id, name, type, "
                f"category, removed_at) VALUES (%s, %s, %s, %s, %s, %s, 'networking', {removed or 'NULL'})",
                (account, sub, sub, f"r{n}", f"r{n}", rtype))
    assert len(license_client.DEVICE_CLOUD_TYPES) == 9
    assert license_client.devices_used() == 2 + 9


def test_the_page_warns_about_devices_and_refuses_nothing(server, monkeypatch):
    server.issue(edition="free")
    license_client.activate(server.key, now=T0)
    monkeypatch.setattr(license_client, "devices_used", lambda: 101)
    status = license_entitlements.licence_status()
    assert (status["devices_used"], status["devices_allowed"], status["devices_warning"]) == (101, 100, "over")
    license_entitlements.require_agent_runs_allowed()  # over the limit, and runs still start

    server.issue(ver=2, edition="free", devices=200)
    license_client.checkin(now=T0 + 60)
    status = license_entitlements.licence_status()
    assert (status["devices_allowed"], status["devices_warning"]) == (200, None)


def test_a_licence_server_that_does_not_know_the_device_count_is_asked_again_without_it(active):
    server = active
    server.knows_devices = False
    license_client.tick(day(server, 1))
    first, second = server.calls("/v1/checkin")
    assert first["devices"] == 3 and "devices" not in second and first["nonce"] != second["nonce"]
    assert {k: v for k, v in first.items() if k not in ("devices", "nonce")} == {
        k: v for k, v in second.items() if k != "nonce"}
    row = license_client._load()
    assert row["pending"] is None and row["last_result"] == "ok" and at(server.now)["lease"]["seq"] == 2

    license_client.tick(day(server, 2))  # remembered: the next check-in leaves the count out
    assert "devices" not in server.calls("/v1/checkin")[-1] and len(server.calls("/v1/checkin")) == 3

    server.knows_devices = True  # the server was upgraded; a week after the refusal the count is sent again
    license_client.tick(day(server, 8))
    assert server.calls("/v1/checkin")[-1]["devices"] == 3 and len(server.calls("/v1/checkin")) == 4
    assert at(server.now)["state"] == "licensed"


def test_only_a_refusal_of_the_body_drops_the_device_count(active):
    server = active
    server.mode = "500"
    license_client.tick(day(server, 1))
    assert len(server.calls("/v1/checkin")) == 1
    assert license_client._load()["pending"]["body"]["devices"] == 3  # repeated unchanged, as any request


def test_the_refusal_of_the_device_count_is_logged_with_the_status_and_the_day_it_comes_back(active, caplog):
    """The fallback is quiet in the product's own words: the check-in goes again without the count and nothing
    else changes. Without a line in the log the appliance looked as if it had never told the server how many
    devices it has, and nobody could tell how long the count was left out."""
    server = active
    server.knows_devices = False
    with caplog.at_level(logging.INFO, logger="helpers.license_client"):
        license_client.tick(day(server, 1))

    refused = [line for line in caplog.messages if "device count" in line]
    assert len(refused) == 1, caplog.messages
    assert "HTTP 400" in refused[0]
    # Until when the count is left out: the week after the refusal, said in UTC.
    until = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(day(server, 1) + license_client.DEVICES_RETRY))
    assert until in refused[0]
    # Nothing of the request itself reaches the line: the installation token is a secret.
    assert server.acts[IID]["token"] not in refused[0]

    # The count is sent again after the week, and that is said too.
    caplog.clear()
    server.knows_devices = True
    with caplog.at_level(logging.INFO, logger="helpers.license_client"):
        license_client.tick(day(server, 8))
    again = [line for line in caplog.messages if "sent with the check-ins again" in line]
    assert len(again) == 1, caplog.messages


def test_a_refused_device_count_is_not_logged_again_on_every_check_in(active, caplog):
    """One line when the fallback starts, not one a day for a week."""
    server = active
    server.knows_devices = False
    with caplog.at_level(logging.INFO, logger="helpers.license_client"):
        license_client.tick(day(server, 1))
        license_client.tick(day(server, 2))
        license_client.tick(day(server, 3))
    assert len([line for line in caplog.messages if "device count" in line]) == 1, caplog.messages


def test_restricted_refuses_change_tools_and_leaves_reads(server):
    from helpers.errors import RepairableException

    from extensions.python.tool_execute_before._05_licence_restricted import LicenceRestricted

    gate = LicenceRestricted(agent=None)
    for tool in sorted(license_entitlements.RESTRICTED_TOOLS):
        with pytest.raises(RepairableException, match="licence page"):
            asyncio.run(gate.execute(tool_name=tool))
    asyncio.run(gate.execute(tool_name="net_read"))
    license_client.activate(server.key, now=T0)
    asyncio.run(gate.execute(tool_name="net_apply"))  # licensed: the gate says nothing


def _client(monkeypatch, permitted=True):
    from flask import Flask

    from helpers import api, login

    monkeypatch.setattr(login, "is_authenticated", lambda: True)
    monkeypatch.setattr(login, "must_change_password", lambda: False)
    monkeypatch.setattr(login, "has_permission", lambda permission: permitted)
    app = Flask("test_licence_client")
    app.secret_key = "test-secret"
    api.register_api_route(app, threading.RLock())
    client = app.test_client()
    with client.session_transaction() as session:
        session["csrf_token"] = "csrf"
    return lambda path, body=None: client.post(f"/api/{path}", json=body or {}, headers={"X-CSRF-Token": "csrf"})


def test_restricted_refuses_approvals_and_manual_task_runs_at_the_dispatcher(server, monkeypatch):
    post = _client(monkeypatch)
    assert api_policy.REFUSED_WHEN_RESTRICTED >= {"plugins/_ea_ansible/change_approve",
                                                 "plugins/_ea_azure_change/azure_approve"}
    for path in sorted(api_policy.REFUSED_WHEN_RESTRICTED):
        response = post(path)
        assert response.status_code == 403 and "licence page" in response.get_json()["error"], path
    # Settings and the licence page itself keep working.
    assert post("license_status").get_json()["licence"]["state"] == "restricted"


def test_the_licence_endpoints_are_settings_administration(server, monkeypatch):
    from helpers import rbac

    for path in ("license_status", "license_activate"):
        assert api_policy.required_permission_for(path) == rbac.PERM_SETTINGS_MANAGE
    post = _client(monkeypatch, permitted=False)
    assert post("license_status").status_code == 403
    assert post("license_activate", {"action": "activate", "key": server.key}).status_code == 403
    assert server.requests == []


def test_the_page_actions_and_that_no_secret_leaves(server, monkeypatch, capsys):
    from plugins._audit.helpers import audit

    audited = []
    monkeypatch.setattr(audit, "emit", lambda event, **fields: audited.append((event, fields)))
    post = _client(monkeypatch)
    said = []

    def act(body):
        response = post("license_activate", body)
        assert response.status_code == 200
        said.append(response.get_data(as_text=True))
        return response.get_json()

    bad = act({"action": "activate", "key": "REZ-AAAAA-AAAAA-AAAAA-AAAAA-AAAAB"})
    assert bad["ok"] is False and "typing mistake" in bad["error"] and server.requests == []

    done = act({"action": "activate", "key": server.key})
    token = server.acts[IID]["token"]
    assert done["ok"] is True and done["licence"]["state"] == "licensed"
    card = done["licence"]
    assert card["licence"] == {
        "lic": LIC, "ver": 1, "customer": "Example Bank", "kind": "subscription", "edition": "standard",
        "features": ["azure", "solarwinds"], "seats": 50, "instances": 1, "offline": False,
        "contract_end": T0 + 365 * DAY, "grace_days": 30}
    assert card["seats_used"] == 7 and card["lease_end"] == T0 + 30 * DAY and card["can_release"] is True
    assert (card["devices_used"], card["devices_allowed"], card["devices_warning"]) == (3, None, None)
    assert card["last_result"] == "ok" and card["last_success_at"] and card["installation_id"] == IID

    assert act({"action": "checkin"})["ok"] is True
    assert act({"action": "request_code", "deployment_name": "SOC"})["request_code"].startswith("REZ-REQ-")
    assert act({"action": "install", "licence": "nonsense"})["ok"] is False
    assert act({"action": "deactivate"})["ok"] is False and server.calls("/v1/deactivate") == []
    released = act({"action": "deactivate", "confirm": True})
    assert released["ok"] is True and released["licence"]["state"] == "restricted"
    assert released["licence"]["can_release"] is False
    assert act({"action": "nonsense"})["ok"] is False

    said.append(post("license_status").get_data(as_text=True))
    said.append(json.dumps(audited))
    said.append("".join(capsys.readouterr()))
    everything = "\n".join(said)
    bare_key = keys.normalise(server.key)
    assert token not in everything and server.key not in everything and bare_key not in everything
    assert [event for event, _ in audited] == [
        "licence.activate", "licence.activate", "licence.checkin", "licence.install", "licence.deactivate"]
    assert audited[1][1]["outcome"] == "ok" and audited[1][1]["lic"] == LIC


def test_the_summary_for_every_user_carries_no_commercial_terms(active):
    summary = license_entitlements.licence_summary()
    assert summary == {"state": "licensed", "agent_runs_allowed": True, "grace_days_remaining": None, "reason": None}


# --- Community is the default; Enterprise is a stamp -------------------------------------------------------------


@pytest.mark.parametrize("stamp, community", [
    (None, True),                                                            # a source checkout: no stamp
    ({"version": "2026.10.11"}, True),                                       # a self-built image
    ({"version": "2026.10.11-lab", "licence": "open"}, True),                # the old lab stamp
    ({"version": "2026.10.11", "licence": "enterprise"}, False),
    ({"version": "2026.10.11-lab", "licence": "enterprise"}, False),         # the field is the whole test
])
def test_only_an_enterprise_stamp_is_not_community(tmp_path, monkeypatch, stamp, community):
    """Nothing but the stamp the image build wrote says Enterprise: no variable, no setting, and the old
    open lab stamp is Community like a checkout with no stamp at all."""
    from helpers import files

    path = tmp_path / "build_info.json"
    if stamp is not None:
        path.write_text(json.dumps(stamp))
    monkeypatch.setattr(files, "get_abs_path", lambda *parts: str(path))
    for name in ("BUILD_LICENCE", "REZZTOR_LICENCE", "REZZTOR_LAB"):
        monkeypatch.setenv(name, "open")
    license_client.enterprise_build.cache_clear()
    license_client.uat_trust.cache_clear()
    try:
        assert license_client.community_build() is community
        assert license_client.enterprise_build() is not community
        if community:
            state = REAL_STATE(1_800_000_000)   # no database in this test: a Community build reads none
            assert (state["state"], state["reason"], state["now"]) == ("community", "community", 1_800_000_000)
            assert state["licence"] is None and state["message"] == "Community build: free, no limits."
            monkeypatch.setattr(license_client, "state", REAL_STATE)
            assert license_entitlements.agent_runs_allowed() and license_entitlements.restricted_refusal() is None
            assert license_entitlements.feature_enabled("network_automation")
            assert license_entitlements.feature_enabled("not-a-named-feature")
            assert license_entitlements.devices_allowed() is None and license_entitlements.seats_allowed() is None
            assert license_entitlements.devices_warning(10_000, license_entitlements.devices_allowed()) is None
            summary = license_entitlements.licence_summary()
            assert summary == {"state": "community", "agent_runs_allowed": True, "grace_days_remaining": None,
                               "reason": None}
            called = []
            monkeypatch.setattr(license_client, "_call", lambda *args, **kwargs: called.append(args))
            license_client.tick(1_800_000_000)
            with pytest.raises(license_client.LicenceError, match="Community build"):
                license_client.activate("REZ-AAAAA-BBBBB-CCCCC-DDDDD-EEEEN", now=1_800_000_000)
            assert called == [], "a Community build never calls the licence server"
    finally:
        license_client.enterprise_build.cache_clear()
        license_client.uat_trust.cache_clear()


def test_an_enterprise_stamp_with_no_licence_is_restricted(server):
    """The same installation, stamped Enterprise, is restricted until a licence is stored. `server` writes that stamp."""
    assert license_client.enterprise_build() is True
    assert license_client.community_build() is False
    assert at(T0)["state"] == "restricted" and at(T0)["reason"] == "no_licence"
    assert license_entitlements.restricted_refusal()


def test_the_build_writes_the_enterprise_line_for_a_release_and_refuses_open(tmp_path):
    import subprocess
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "docker" / "run" / "fs" / "ins" / "write_build_info.sh"

    def stamp(**env):
        target = tmp_path / "stamp.json"
        target.unlink(missing_ok=True)
        done = subprocess.run(["bash", str(script)], env={"PATH": "/usr/bin:/bin", "BUILD_INFO_TARGET": str(target), **env},
                              capture_output=True, text=True)
        return done.returncode, json.loads(target.read_text()) if target.exists() else None

    assert "licence" not in stamp(BUILD_VERSION="2026.10.11")[1], "unset is Community: no licence line"
    assert stamp(BUILD_VERSION="2026.10.21", BUILD_LICENCE="enterprise")[1]["licence"] == "enterprise"
    assert stamp(BUILD_VERSION="2026.10.11-lab", BUILD_LICENCE="open") == (1, None), "open is no longer a build"
    assert stamp(BUILD_VERSION="2026.10.11-lab", BUILD_LICENCE="enterprise") == (1, None)
    assert stamp(BUILD_VERSION="2026.10.11-uat", BUILD_LICENCE="enterprise") == (1, None)
    # And what carries it onward: a release build passes enterprise, and a Community image is never packaged.
    root = Path(__file__).resolve().parents[1]
    assert "BUILD_LICENCE=enterprise" in (root / "scripts" / "build_release.sh").read_text()
    assert "is a $is_lab image and this is a $KIND build. Nothing was written." in (root / "scripts" / "build_iso.sh").read_text()
    assert "A Community image is never packaged" in (root / "scripts" / "sign_release.py").read_text()
    assert "ENV BUILD_LICENCE" not in (root / "Dockerfile").read_text() and "BUILD_LICENCE" not in (
        root / "deploy" / "customer" / "docker-compose.yml").read_text()


# --- a UAT build (ADR 0085) ---------------------------------------------------------------------------------------

PRODUCTION_ROOTS = {"root-2026a": "upIWLEFjeb667i6SA-PnN-t2-HKq7xCG4rRTJfQiHuc"}
UAT = {"version": "2026.10.18-uat", "licence": "uat"}


@pytest.fixture
def build(tmp_path, monkeypatch):
    """`build(stamp, trust)` puts a build stamp and a trust file where an image has them. Nothing else moves."""
    from helpers import files

    real = files.get_abs_path
    names = ("build_info.json", license_client.UAT_TRUST_FILE)
    monkeypatch.setattr(files, "get_abs_path",
                        lambda *parts: str(tmp_path / parts[0]) if len(parts) == 1 and parts[0] in names else real(*parts))

    def make(stamp: dict | None, trust: dict | str | None) -> None:
        for name, content in zip(names, (stamp, trust)):
            (tmp_path / name).unlink(missing_ok=True)
            if content is not None:
                (tmp_path / name).write_text(content if isinstance(content, str) else json.dumps(content))
        license_client.uat_trust.cache_clear()
        license_client.enterprise_build.cache_clear()
        monkeypatch.setattr(files, "get_abs_path",
                            lambda *parts: str(tmp_path / parts[0]) if len(parts) == 1 and parts[0] in names else real(*parts))

    yield make
    license_client.uat_trust.cache_clear()
    license_client.enterprise_build.cache_clear()


def _dummy_trust(server: str = "https://license.uat.rezztor.test", ca_pem: str = "-----BEGIN CERTIFICATE-----x") -> dict:
    return {"root_keys": {"root-uat-test": keys.export_public(keys.new_private_key())}, "server": server, "ca_pem": ca_pem}


@pytest.mark.parametrize("stamp, uat", [
    ({"version": "2026.10.18"}, False),                                     # a release, with the file beside it
    ({"version": "2026.10.18", "licence": "uat"}, False),                   # uat without saying so in its version
    ({"version": "2026.10.18-uat"}, False),
    ({"version": "2026.10.18-uat", "licence": "open"}, False),
    (UAT, True),
])
def test_only_the_stamp_of_a_uat_image_replaces_the_trust(build, monkeypatch, stamp, uat):
    """A normal build trusts root-2026a and license.rezztor.com and nothing else, whatever file lies beside it and
    whatever the environment says; only a UAT stamp swaps in the baked roots and server, and then only those."""
    trust = _dummy_trust()
    build(stamp, trust)
    monkeypatch.delenv(license_client.ENV_SERVER, raising=False)
    for name in ("BUILD_LICENCE", "REZZTOR_UAT_TRUST", "REZZTOR_ISO_UAT"):
        monkeypatch.setenv(name, "uat")
    if uat:
        assert license_client._roots() == trust["root_keys"] and "root-2026a" not in license_client._roots()
        assert license_client.server_url() == "https://license.uat.rezztor.test"
    else:
        assert license_client.uat_trust() is None
        assert license_client._roots() == PRODUCTION_ROOTS == license_client.ROOT_KEYS
        assert license_client.server_url() == "https://license.rezztor.com"
        assert license_client._transport() is license_client._opener


def test_a_development_checkout_with_no_stamp_and_no_trust_file_trusts_production(build, monkeypatch):
    build(None, None)
    monkeypatch.delenv(license_client.ENV_SERVER, raising=False)
    assert license_client.uat_trust() is None
    assert license_client._roots() == PRODUCTION_ROOTS
    assert license_client.server_url() == "https://license.rezztor.com"


@pytest.mark.parametrize("stamp", [None, "not json", "[]"])
def test_an_unreadable_stamp_beside_a_trust_file_trusts_nothing(build, monkeypatch, stamp):
    """A corrupted UAT image (the stamp gone, not JSON, not an object) never falls back to the production root and
    license.rezztor.com: the trust file beside it says what the image was meant to be."""
    build(stamp, _dummy_trust())
    monkeypatch.delenv(license_client.ENV_SERVER, raising=False)
    assert license_client._roots() == {}
    with pytest.raises(license_client.LicenceError):
        license_client.server_url()


@pytest.mark.parametrize("trust", [
    None,                                                                   # the file is missing
    "not json",
    {"root_keys": {}, "server": "https://license.uat.rezztor.test", "ca_pem": "-----BEGIN CERTIFICATE-----"},
    {"root_keys": {"root-uat": "short"}, "server": "https://license.uat.rezztor.test", "ca_pem": "-----BEGIN CERTIFICATE-----"},
    {**_dummy_trust(), "ca_pem": ""},
    {**_dummy_trust(), "root_keys": dict(PRODUCTION_ROOTS)},              # a UAT build never takes a production root
    {**_dummy_trust(), "root_keys": {"root-uat": PRODUCTION_ROOTS["root-2026a"]}},
])
def test_a_uat_image_with_a_wrong_trust_file_trusts_nothing(build, monkeypatch, trust):
    build(UAT, trust)
    monkeypatch.delenv(license_client.ENV_SERVER, raising=False)
    assert license_client._roots() == {}
    with pytest.raises(license_client.LicenceError):
        license_client.server_url()


def test_a_uat_image_refuses_a_production_licence(server, build):
    """`server` signs with the root this test hands the product as ROOT_KEYS, standing in for production. A UAT
    image does not trust it, and a UAT trust file that names it is refused whole."""
    build(UAT, _dummy_trust(ca_pem=_pem(_ca("UAT test CA")[0])))  # a CA that loads: the refusal is the root
    with pytest.raises(license_client.LicenceError, match="unknown_kid"):
        license_client.activate(server.key, now=T0)
    assert stored()["licence"] is None and at(T0)["state"] == "restricted"
    build(UAT, {**_dummy_trust(), "root_keys": dict(server.roots)})
    with pytest.raises(license_client.LicenceError, match="unknown_kid"):
        license_client.activate(server.key, now=T0)
    assert stored()["licence"] is None


def _ca(cn: str):
    """A test CA, and a function that issues it a certificate for localhost with its key."""
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.datetime.now(datetime.timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])

    def cert(subject, public_key, ca: bool):
        builder = (x509.CertificateBuilder().subject_name(subject).issuer_name(name).public_key(public_key)
                   .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
                   .not_valid_after(now + datetime.timedelta(days=1))
                   .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True))
        if not ca:
            builder = builder.add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        return builder.sign(key, hashes.SHA256())

    def leaf():
        leaf_key = ec.generate_private_key(ec.SECP256R1())
        return cert(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]), leaf_key.public_key(), False), leaf_key

    return cert(name, key.public_key(), True), leaf


def _pem(cert) -> str:
    from cryptography.hazmat.primitives import serialization

    return cert.public_bytes(serialization.Encoding.PEM).decode()


@pytest.fixture
def tls_server(database, monkeypatch, tmp_path):
    """The stand-in licence server over HTTPS on 127.0.0.1, with a certificate for localhost from a test CA, as
    the UAT licence server is behind Caddy's `tls internal`. ROOT_KEYS stays the production list."""
    import ssl

    from cryptography.hazmat.primitives import serialization

    root, leaf = _ca("UAT test CA")
    cert, key = leaf()
    (tmp_path / "leaf.pem").write_text(_pem(cert))
    (tmp_path / "leaf.key").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                          serialization.NoEncryption()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(tmp_path / "leaf.pem", tmp_path / "leaf.key")
    fake = FakeServer()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    httpd.fake = fake  # type: ignore[attr-defined]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.delenv(license_client.ENV_SERVER, raising=False)
    monkeypatch.setattr(license_client, "state", REAL_STATE)
    monkeypatch.setattr(license_client, "installation_id", lambda: IID)
    monkeypatch.setattr(license_client, "product_version", lambda: "9.9-test")
    monkeypatch.setattr(license_client, "seats_used", lambda: 7)
    monkeypatch.setattr(license_client, "devices_used", lambda: 3)
    monkeypatch.setattr(license_client, "_devices_refused_at", 0)
    fake.url = f"https://localhost:{httpd.server_address[1]}"  # type: ignore[attr-defined]
    fake.ca_pem = _pem(root)  # type: ignore[attr-defined]
    yield fake
    httpd.shutdown()
    httpd.server_close()


def test_a_uat_image_activates_against_its_baked_server_checked_by_its_baked_ca(tls_server, build):
    """Roots, address and CA all from the trust file: no variable is set, and ROOT_KEYS is the production list."""
    assert license_client.ROOT_KEYS == PRODUCTION_ROOTS
    build(UAT, {"root_keys": tls_server.roots, "server": tls_server.url, "ca_pem": tls_server.ca_pem})
    license_client.activate(tls_server.key, now=T0)
    assert at(T0)["state"] == "licensed" and at(T0)["licence"]["lic"] == LIC
    assert tls_server.calls("/v1/activate"), "the call went to the baked server"


def test_a_uat_image_trusts_its_baked_ca_and_no_other(tls_server, build, monkeypatch):
    build(UAT, {"root_keys": tls_server.roots, "server": tls_server.url, "ca_pem": _pem(_ca("Another CA")[0])})
    with pytest.raises(license_client.LicenceError, match="could not be reached"):
        license_client.activate(tls_server.key, now=T0)
    # And an Enterprise image, which checks TLS with the system's CAs, does not know the UAT CA either.
    build({"version": "2026.10.18", "licence": "enterprise"}, None)
    monkeypatch.setenv(license_client.ENV_SERVER, tls_server.url)
    monkeypatch.setattr(license_client, "ROOT_KEYS", tls_server.roots)
    with pytest.raises(license_client.LicenceError, match="could not be reached"):
        license_client.activate(tls_server.key, now=T0)
    assert not tls_server.requests, "no request got past the TLS check"


def test_the_build_writes_the_uat_trust_only_for_a_version_that_says_uat(tmp_path):
    import subprocess
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "docker" / "run" / "fs" / "ins" / "write_build_info.sh"
    trust_file = tmp_path / "licence_trust.json"
    trust = json.dumps(_dummy_trust())

    def stamp(**env):
        target = tmp_path / "stamp.json"
        target.unlink(missing_ok=True)
        done = subprocess.run(["bash", str(script)], env={"PATH": "/usr/bin:/bin", "BUILD_INFO_TARGET": str(target), **env},
                              capture_output=True, text=True)
        return done.returncode, json.loads(target.read_text()) if target.exists() else None

    code, written = stamp(BUILD_VERSION="2026.10.18-uat", BUILD_LICENCE="uat", BUILD_UAT_TRUST=trust)
    assert code == 0 and written["licence"] == "uat" and json.loads(trust_file.read_text()) == json.loads(trust)
    # A normal build writes no trust file, and takes away one that came in with the source.
    assert "licence" not in stamp(BUILD_VERSION="2026.10.18")[1] and not trust_file.exists()
    assert stamp(BUILD_VERSION="2026.10.18", BUILD_LICENCE="uat", BUILD_UAT_TRUST=trust) == (1, None)
    assert stamp(BUILD_VERSION="2026.10.18-uat", BUILD_LICENCE="uat") == (1, None), "uat with nothing to trust"
    assert stamp(BUILD_VERSION="2026.10.18", BUILD_UAT_TRUST=trust) == (1, None), "a trust file without uat"
    assert stamp(BUILD_VERSION="2026.10.18-lab", BUILD_LICENCE="open", BUILD_UAT_TRUST=trust) == (1, None)
    assert not trust_file.exists()
