"""The licence client: what this installation holds, what it concludes from it, and its calls to the licence server.

The contract is the licence-server repository's `spec/formats.md` and `spec/protocol.md`; the formats themselves
are `rezlic/`, copied from there and never edited here. This module adds only what a product needs around them:

- **Trust.** `ROOT_KEYS` is the whole list of root public keys. It is a constant of the build. No setting,
  environment variable or file adds or replaces one: whoever administers an installation is the customer, and a
  key they could add is a licence they could sign. A UAT image (ADR 0085) trusts the roots baked into it at build
  time instead, and only those: `uat_trust()`.
- **Storage.** One row of `licence_state` (migration 0024). Nothing is believed because it is stored: the
  verdict is worked out again from the signed documents every time it is asked for.
- **The calls.** `/v1/activate`, `/v1/checkin`, `/v1/deactivate`, `/v1/keys`. A request is saved before it is
  sent and repeated unchanged, nonce included, until a verified answer arrives. An answer is verified whole
  before any of it is stored, in one statement, so the licence and lease held stay until new ones verify.
  Anything else the server says (400, 403, 429, 5xx, nothing, a body that is not the protocol's) is recorded
  and changes nothing.
- **The clock.** The mark of "The clock" in the formats: a reading of this machine's clock only raises it, an
  accepted lease sets it.

The installation token and the activation key are secrets. They are in the table and in request bodies and
nowhere else: `state()` returns neither, and every `LicenceError.reason` is a fixed sentence built without them.
"""

from __future__ import annotations

import functools
import http.client
import json
import logging
import os
import re
import secrets
import ssl
import threading
import time
import urllib.error
import urllib.request
from typing import Any, NamedTuple
from urllib.parse import urlsplit

from rezlic import keys
from rezlic.formats import DAY, TOLERANCE, FormatError, TrustStore, b64d, b64e, hash_b64, load_envelope
from rezlic.request_code import build_request_code
from rezlic.verdict import next_mark, verdict

from helpers import db

#: The root public keys this build trusts, by key id: base64url of the raw 32 bytes. See the module docstring.
ROOT_KEYS = {"root-2026a": "upIWLEFjeb667i6SA-PnN-t2-HKq7xCG4rRTJfQiHuc"}

DEFAULT_SERVER = "https://license.rezztor.com"
#: For development and tests only. https, or http to this machine; anything else is refused, not corrected.
ENV_SERVER = "REZZTOR_LICENCE_SERVER_URL"
#: Beside build_info.json, written by the image build only when it is a UAT build (ADR 0085).
UAT_TRUST_FILE = "licence_trust.json"

TIMEOUT_SECONDS = 15
MAX_REQUEST = 4096       # the protocol's limit on a request body
MAX_ANSWER = 65536       # an answer is a few kilobytes; more than this is not one
RETRY_WAITS = (60, 300, 1800, 3600)  # after the 1st, 2nd, 3rd failure, then hourly
CHECKIN_EVERY = DAY
MARK_STEP = 600          # the mark is written when the clock has passed it by this much, not on every tick

_IID = re.compile(r"[A-Za-z0-9_-]{8,64}")
_EMPTY: dict[str, Any] = {
    "licence": None, "certs": [], "lease": None, "token": None, "pending": None, "mark": 0,
    "last_attempt_at": 0, "last_success_at": 0, "last_result": "", "failures": 0, "next_attempt_at": 0,
}
_JSON = ("licence", "certs", "lease", "pending")

# ponytail: one lock for one web process. Two processes would only repeat a request with the same nonce, which
# the server answers twice with the same stored answer; a row lock if the application ever runs as several.
_lock = threading.Lock()

#: Where this module says what it did. The application's own logger, at INFO: the same stdout ADR 0005 sends the
#: audit events to, so a line here is in `docker logs` and nowhere else to look.
log = logging.getLogger(__name__)


class LicenceError(Exception):
    """A licence action that did not complete. `reason` is one plain sentence, safe to show, log and audit.
    `final` means the server answered that it did not act on the request (400, 403)."""

    def __init__(self, reason: str, final: bool = False, status: int | None = None):
        super().__init__(reason)
        self.reason = reason
        self.final = final
        self.status = status  # the HTTP status the server answered with, when it answered


# --- who this installation is -----------------------------------------------------------------------------------

def installation_id() -> str:
    """The installation id: random, made on first start, kept in usr/.env. Not derived from hardware."""
    from helpers import runtime

    return runtime.get_persistent_id()


def product_version() -> str:
    """1 to 40 printable ASCII characters, as the protocol wants; "unknown" when the build carries none."""
    try:
        from helpers import git

        text = str(git.get_git_info().get("version") or "")
    except Exception:  # noqa: BLE001 - a version lookup must not stop a check-in
        text = ""
    return re.sub(r"[^\x20-\x7e]", "", text)[:40].strip() or "unknown"


def seats_used() -> int:
    """Active accounts: enabled ones. A disabled account uses no seat."""
    from helpers import users

    return sum(1 for user in users.list_users() if not user.get("disabled"))


#: Azure resource types that count as network devices toward the licence's `devices` limit (ADR 0093): firewalls,
#: load balancers and gateways, the things that are a box in a network diagram. Not policies, connections, the
#: local network gateway (the on-premises device, already in the inventory) or global services.
DEVICE_CLOUD_TYPES = (
    "microsoft.network/azurefirewalls", "microsoft.network/networkvirtualappliances",
    "microsoft.network/loadbalancers", "microsoft.network/applicationgateways",
    "microsoft.network/virtualnetworkgateways", "microsoft.network/vpngateways",
    "microsoft.network/p2svpngateways", "microsoft.network/expressroutegateways", "microsoft.network/natgateways",
)


def devices_used() -> int:
    """Network devices in use: active inventory devices, and Azure firewalls, load balancers and gateways that the
    last pull still saw. A device is counted while it is active, whatever made it so."""
    with db.transaction(db.NO_SCOPE) as conn:
        row = conn.execute(
            "SELECT (SELECT count(*) FROM inventory_device WHERE status = 'active')"
            " + (SELECT count(*) FROM cloud_resource WHERE removed_at IS NULL AND type = ANY(%s))",
            [list(DEVICE_CLOUD_TYPES)],
        ).fetchone()
    return int(row[0])


def server_url() -> str:
    uat = uat_trust()
    url = (os.getenv(ENV_SERVER) or (uat["server"] if uat else DEFAULT_SERVER)).strip().rstrip("/")
    parts = urlsplit(url)
    local = parts.hostname in ("localhost", "127.0.0.1", "::1")
    if not parts.hostname or not (parts.scheme == "https" or (parts.scheme == "http" and local)):
        raise LicenceError(f"{ENV_SERVER} must be an https:// address (http:// only for localhost).")
    return url


# --- storage ----------------------------------------------------------------------------------------------------

def _load() -> dict[str, Any]:
    with db.transaction(db.NO_SCOPE) as conn:
        row = conn.execute(f"SELECT {', '.join(_EMPTY)} FROM licence_state WHERE id").fetchone()
    return dict(zip(_EMPTY, row)) if row else dict(_EMPTY)


def _save(**fields: Any) -> None:
    """Write the named columns in one statement. Column names come from this module only."""
    from psycopg.types.json import Jsonb

    names = list(fields)
    assert names and all(name in _EMPTY for name in names)
    values = [Jsonb(fields[n]) if n in _JSON and fields[n] is not None else fields[n] for n in names]
    with db.transaction(db.NO_SCOPE) as conn:
        conn.execute(
            f"INSERT INTO licence_state (id, {', '.join(names)}) VALUES (TRUE, {', '.join(['%s'] * len(names))}) "
            f"ON CONFLICT (id) DO UPDATE SET {', '.join(f'{n} = EXCLUDED.{n}' for n in names)}",
            values,
        )


class Held(NamedTuple):
    """The stored row, and what of it passes verification now."""

    row: dict[str, Any]
    trust: TrustStore
    certs: dict[str, Any]            # kid -> certificate envelope
    licence: dict[str, Any] | None   # verified payloads and their exact bytes
    licence_raw: bytes | None
    lease: dict[str, Any] | None
    lease_raw: bytes | None
    problem: str                     # the code of the rule a stored document broke, or ""


def _lease_nonce(envelope: Any) -> str:
    """The nonce inside a stored lease. It was compared with the request's when the lease was accepted, which
    is what proved it fresh; verifying the stored lease again checks everything else."""
    try:
        return str(json.loads(b64d(envelope["payload"]))["nonce"])
    except Exception:  # noqa: BLE001 - whatever is wrong with it, verify_lease says so
        return ""


def _held(row: dict[str, Any]) -> Held:
    iid = installation_id()
    trust = TrustStore(_roots())
    certs: dict[str, Any] = {}
    for envelope in row["certs"] or []:
        try:
            certs[trust.accept_cert(envelope, 0)["kid"]] = envelope
        except FormatError:
            pass  # not trusted; a document that needed it fails below
    licence = licence_raw = lease = lease_raw = None
    problem = ""
    if row["licence"] is not None:
        try:
            licence, licence_raw = trust.verify_licence(row["licence"], 0, iid)
        except FormatError as exc:
            problem = exc.reason
    if licence is not None and not licence["offline"] and row["lease"] is not None:
        try:
            lease, lease_raw = trust.verify_lease(row["lease"], licence, licence_raw, iid, _lease_nonce(row["lease"]))
        except FormatError as exc:
            problem = exc.reason
    return Held(row, trust, certs, licence, licence_raw, lease, lease_raw, problem)


# --- what the product concludes ---------------------------------------------------------------------------------

_MESSAGES = {
    "ok": None,
    "revoked_notice": "This licence has been revoked. The installation is restricted when the notice period ends.",
    "ended": "The lease has ended and has not been renewed. The installation is restricted when the grace period ends.",
    "no_licence": "No licence is installed.",
    "invalid": "The stored licence did not pass verification.",
    "not_yet_valid": "The licence is not valid yet.",
    "no_lease": "The licence has no lease for this installation. Enter the activation key again.",
    "released": "This installation was released from its licence.",
    "revoked": "This licence has been revoked.",
    "trial_ended": "The trial has ended.",
    "grace_over": "The licence has ended and the grace period is over.",
}


@functools.lru_cache(maxsize=1)
def enterprise_build() -> bool:
    """True only when this image was built as Enterprise: the stamp says ``"licence": "enterprise"``.

    Read once from the stamp the image build writes (`BUILD_LICENCE=enterprise`, which a release build passes
    and a self-built image does not). No setting, environment variable, database row or request switches it on.
    A source checkout has no stamp. The old open lab stamp is not Enterprise.
    """
    try:
        return _stamp_licence() == "enterprise"
    except (OSError, ValueError, AttributeError):
        return False


def community_build() -> bool:
    """True unless this image was built as Enterprise or UAT.

    A source checkout, a self-built image and the old open lab stamp are Community: every feature, no licence
    server. A UAT image is not Community. A broken UAT image (the trust file present, the stamp unreadable)
    is not either: it stays on the licence path and trusts nothing, rather than becoming a free build.
    """
    return not enterprise_build() and uat_trust() is None


def _stamp_licence() -> str:
    from helpers import files

    with open(files.get_abs_path("build_info.json"), encoding="utf-8") as handle:
        stamp = json.load(handle)
    if not isinstance(stamp, dict):
        raise ValueError("stamp is not an object")
    return str(stamp.get("licence") or "")


def _stamp_says(licence: str, suffix: str) -> bool:
    from helpers import files

    with open(files.get_abs_path("build_info.json"), encoding="utf-8") as handle:
        stamp = json.load(handle)
    if not isinstance(stamp, dict):
        raise AttributeError("stamp is not an object")
    return stamp.get("licence") == licence and str(stamp.get("version", "")).endswith(suffix)


#: What a UAT image that cannot read its trust trusts: no root and no server.
_TRUST_NOTHING: dict[str, Any] = {"root_keys": {}, "server": "", "ca_pem": ""}


@functools.lru_cache(maxsize=1)
def uat_trust() -> dict[str, Any] | None:
    """In a UAT image (ADR 0085), what it trusts instead of `ROOT_KEYS` and `DEFAULT_SERVER`; None in any other.

    Read once, and only when the stamp the image build wrote says `"licence": "uat"` with a version that ends in
    -uat. Then the file beside the stamp gives the root keys, the server and the CA certificate its TLS is checked
    against. No setting, environment variable, database row or request reads or changes it. A UAT image whose
    file is missing or wrong, or names a production root, trusts nothing: it never falls back to `ROOT_KEYS`.
    Nor does an image whose stamp cannot be read while the trust file lies beside it: that is a broken UAT image,
    and it must not fall back to the production root and `license.rezztor.com` either.
    """
    from helpers import files

    try:
        if not _stamp_says("uat", "-uat"):
            return None
    except (OSError, ValueError, AttributeError):
        return dict(_TRUST_NOTHING) if os.path.lexists(files.get_abs_path(UAT_TRUST_FILE)) else None
    try:
        with open(files.get_abs_path(UAT_TRUST_FILE), encoding="utf-8") as handle:
            trust = json.load(handle)
        roots = {str(kid): str(key) for kid, key in trust["root_keys"].items()}
        server, ca_pem = str(trust["server"]), str(trust["ca_pem"])
        if not roots or set(roots.values()) & set(ROOT_KEYS.values()) or "BEGIN CERTIFICATE" not in ca_pem:
            raise ValueError("not a UAT trust file")
        TrustStore(roots)  # every key must load
        return {"root_keys": roots, "server": server, "ca_pem": ca_pem}
    except (OSError, ValueError, KeyError, AttributeError, TypeError, FormatError):
        return dict(_TRUST_NOTHING)


def _roots() -> dict[str, str]:
    uat = uat_trust()
    return uat["root_keys"] if uat else ROOT_KEYS


#: What a Community build answers instead of a verdict. It names no customer and no licence anybody issued.
_COMMUNITY_STATE: dict[str, Any] = {
    "state": "community", "reason": "community", "problem": "", "clock_behind": False,
    "message": "Community build: free, no limits.",
    "end": None, "restricted_at": None, "days_left": None, "lease": None, "has_token": False, "pending": None,
    "last_attempt_at": 0, "last_success_at": 0, "last_result": "", "next_attempt_at": 0, "licence": None,
}


def state(now: int | None = None) -> dict[str, Any]:
    """The verdict of "What the product concludes", from the stored documents, verified again now.

    `state` is community, licensed, grace1, grace2 or restricted. Holds no secret: neither the token nor a
    pending body. A Community build answers "community" without reading anything. Enterprise and UAT read the
    stored documents, as before.
    """
    clock = int(time.time() if now is None else now)
    if community_build():
        return {**_COMMUNITY_STATE, "now": clock}
    held = _held(_load())
    row, licence, lease = held.row, held.licence, held.lease
    v = verdict(licence, lease, clock, row["mark"])
    reason = "invalid" if held.problem and v.reason in ("no_licence", "no_lease") else v.reason
    used = row["mark"] if v.clock_behind else clock  # the time the table worked from

    restricted_at = None
    if v.state != "restricted" and licence is not None and v.end is not None:
        restricted_at = v.end + licence["grace_days"] * DAY
        if v.end + TOLERANCE > restricted_at:
            restricted_at = v.end + TOLERANCE
        if licence["kind"] == "trial":
            restricted_at = min(restricted_at, licence["exp"] + TOLERANCE)
        if v.reason == "revoked_notice":
            restricted_at = min(restricted_at, lease["notice_until"])  # type: ignore[index]
    return {
        "state": v.state,
        "reason": reason,
        "message": _MESSAGES.get(reason),
        "problem": held.problem,
        "clock_behind": v.clock_behind,
        "now": used,
        "end": v.end,
        "restricted_at": restricted_at,
        "days_left": None if restricted_at is None else max(0, -(-(restricted_at - used) // DAY)),
        "licence": licence,
        "lease": lease,
        "has_token": bool(row["token"]),
        "pending": row["pending"]["kind"] if row["pending"] else None,
        "last_attempt_at": row["last_attempt_at"],
        "last_success_at": row["last_success_at"],
        "last_result": row["last_result"],
        "next_attempt_at": row["next_attempt_at"],
    }


# --- the wire ---------------------------------------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None  # a redirect is not an answer of the protocol; it becomes an HTTPError and changes nothing


_opener = urllib.request.build_opener(_NoRedirect)


def _transport() -> urllib.request.OpenerDirector:
    """The system's CAs, or in a UAT image the baked CA certificate and no other (ADR 0085)."""
    uat = uat_trust()
    if not uat:
        return _opener
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  # verifies host and chain, and starts with no CA at all
    if uat["ca_pem"]:
        context.load_verify_locations(cadata=uat["ca_pem"])
    return urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=context))


def _call(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """One request. Returns the JSON object of a 200, or raises LicenceError. Never logs a body."""
    headers = {"User-Agent": f"RezztorEAgent/{product_version()}", "Accept": "application/json"}
    data = None
    if body is not None:
        # Sorted, so a request read back from the table is sent as the same bytes it was sent as first.
        data = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
        if len(data) > MAX_REQUEST:
            raise LicenceError("The request is larger than the licence server accepts.", final=True)
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(server_url() + path, data=data, method=method, headers=headers)
    try:
        with _transport().open(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read(MAX_ANSWER + 1)
            status = response.status
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            raise LicenceError("The licence server refused the request.", final=True) from None
        raise LicenceError(f"The licence server answered HTTP {exc.code}.", final=exc.code == 400,
                           status=exc.code) from None
    except (OSError, http.client.HTTPException):  # URLError and timeouts are OSError
        raise LicenceError("The licence server could not be reached.") from None
    try:
        answer = json.loads(raw) if status == 200 and len(raw) <= MAX_ANSWER else None
    except ValueError:
        answer = None
    if not isinstance(answer, dict):
        raise LicenceError("The licence server sent an answer that is not valid.")
    return answer


def _nonce() -> str:
    return b64e(secrets.token_bytes(16))


def _accept(held: Held, answer: dict[str, Any], body: dict[str, Any], activation: bool) -> dict[str, Any]:
    """Verify an activation or check-in answer whole; return the columns to store. Raises FormatError and then
    nothing is stored. Mirrors the reference client (licence-server tests/helpers.py, FakeProduct.accept)."""
    iid, trust, certs = body["iid"], held.trust, dict(held.certs)
    answer_certs = answer.get("certs", [])
    if not isinstance(answer_certs, list):
        raise FormatError("bad_answer")
    for envelope in answer_certs:
        certs[trust.accept_cert(envelope, 0)["kid"]] = envelope
    envelope, licence, raw = held.row["licence"], held.licence, held.licence_raw
    if "licence" in answer:
        envelope = answer["licence"]
        licence, raw = trust.verify_licence(envelope, 0, iid)
        # "Replacing a licence": another licence id comes in only with a person's activation key.
        if held.licence is not None and not (activation and licence["lic"] != held.licence["lic"]):
            licence, raw = trust.verify_replacement(held.row["licence"], envelope, 0, iid)
    if licence is None or raw is None:
        raise FormatError("no_licence")
    lease, _ = trust.verify_lease(answer.get("lease"), licence, raw, iid, body["nonce"])
    # ponytail: certificates are kept for good (about four a year); prune by `exp` if the list ever matters.
    fields = {"licence": envelope, "lease": answer["lease"], "certs": list(certs.values()),
              "mark": next_mark(held.row["mark"], 0, lease["now"])}
    if activation:
        token = answer.get("token")
        try:
            if len(b64d(token)) != 32:
                raise FormatError("bad_token")
        except FormatError:
            raise FormatError("bad_token") from None
        fields["token"] = token
    return fields


def _attempt(row: dict[str, Any], now: int) -> None:
    """Send the pending request and act on the answer. Either everything of a verified answer is stored and the
    request is no longer pending, or only the result of the attempt is recorded."""
    global _devices_refused_at
    kind, body = row["pending"]["kind"], row["pending"]["body"]
    try:
        try:
            answer = _call("POST", "/v1/" + kind, body)
        except LicenceError as exc:
            # A licence server older than ADR 0093 refuses a check-in that names its device count (400, or 422
            # from a framework's own validation). Say it once more without, as a new request, and leave the
            # count out for a while; nothing was acted on, so nothing else changes.
            if kind != "checkin" or "devices" not in body or exc.status not in (400, 422):
                raise
            _devices_refused_at = now
            _log_devices_refused(exc.status or 0, now)
            body = {**{k: v for k, v in body.items() if k != "devices"}, "nonce": _nonce()}
            _pend(row, kind, body)
            answer = _call("POST", "/v1/" + kind, body)
        if kind == "deactivate":
            # The one unsigned answer the product acts on, and only against itself.
            if answer.get("ok") is not True:
                raise LicenceError("The licence server sent an answer that is not valid.")
            fields: dict[str, Any] = {"lease": None, "token": None}
        else:
            try:
                fields = _accept(_held(row), answer, body, kind == "activate")
            except FormatError as exc:
                raise LicenceError(
                    f"The licence server's answer did not pass verification ({exc.reason})."
                ) from None
    except LicenceError as exc:
        if exc.final and kind != "checkin":
            # The server did not act on it, and a person is there to try again: the key is not kept waiting.
            _save(pending=None, last_attempt_at=now, last_result=exc.reason)
        else:
            failures = row["failures"] + 1
            _save(last_attempt_at=now, last_result=exc.reason, failures=failures,
                  next_attempt_at=now + RETRY_WAITS[min(failures, len(RETRY_WAITS)) - 1])
        raise
    _save(**fields, pending=None, last_attempt_at=now, last_success_at=now, last_result="ok", failures=0,
          next_attempt_at=now + CHECKIN_EVERY)


def _pend(row: dict[str, Any], kind: str, body: dict[str, Any]) -> None:
    """Keep a new request before it is sent, so a restart repeats it with the same nonce."""
    row["pending"] = {"kind": kind, "body": body}
    _save(pending=row["pending"])


def _own_iid() -> str:
    iid = installation_id()
    if not _IID.fullmatch(iid):
        raise LicenceError(
            "This installation's id is not one the licence server accepts (8 to 64 letters, digits, - or _)."
        )
    return iid


# --- what an administrator, or the daily job, asks for ----------------------------------------------------------

_KEY_PROBLEMS = {
    "bad_length": "That is not a complete activation key: it has 25 characters after REZ.",
    "bad_char": "That activation key holds a character that no key contains.",
    "bad_check": "That activation key has a typing mistake: its check character does not match.",
}


def _not_on_community() -> None:
    """A Community build never calls the licence server, and stores nothing of a licence exchange."""
    if community_build():
        raise LicenceError("This is a Community build. It does not use a licence server.", final=True)


def activate(key_text: str, now: int | None = None) -> None:
    """Exchange an activation key for a licence, a first lease and the installation token."""
    _not_on_community()
    try:
        key = keys.normalise(key_text)  # refused here, before any network call
    except keys.ActivationKeyError as exc:
        raise LicenceError(_KEY_PROBLEMS[exc.reason], final=True) from None
    iid = _own_iid()
    with _lock:
        row = _load()
        pending = row["pending"]
        try:
            same = pending["kind"] == "activate" and keys.normalise(pending["body"]["key"]) == key  # type: ignore[index]
        except (TypeError, KeyError, keys.ActivationKeyError):
            same = False
        if not same:
            shown = "REZ-" + "-".join(key[i:i + 5] for i in range(0, 25, 5))
            _pend(row, "activate", {"key": shown, "iid": iid, "version": product_version(), "nonce": _nonce()})
        _attempt(row, int(time.time() if now is None else now))


#: When a licence server last refused a check-in that carried `devices`, and how long the count is then left out.
#: ponytail: kept in this process only; a restart costs one refused request to learn it again.
_devices_refused_at = 0
DEVICES_RETRY = 7 * DAY

#: When the count was sent again after the week, so that a second line is written once and not every day.
_devices_sent_again_at = 0


def _log_devices_refused(status: int, now: int) -> None:
    """Say that the licence server refused the device count, and until when the count is left out.

    The refusal is quiet in the product's own words -- the check-in is sent again without the count and nothing
    else changes -- so without this line the appliance looked as if it had never told the server how many devices
    it has. The status code and the day the count comes back are what an operator needs; no body, no token and
    no device count are written.
    """
    until = now + DEVICES_RETRY
    log.info(
        "The licence server answered HTTP %s to a check-in that named the device count. "
        "The count is left out of the check-ins until %s (UTC).",
        status,
        time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(until)),
    )


def _log_devices_sent_again(now: int) -> None:
    """Say that the device count is sent again, after the week or after a restart."""
    global _devices_sent_again_at
    if _devices_sent_again_at:
        return
    _devices_sent_again_at = now
    log.info("The device count is sent with the check-ins again.")


def _new_checkin(row: dict[str, Any], now: int) -> bool:
    """Make the next check-in the pending request. False when there is nothing to check in with."""
    held = _held(row)
    if held.licence is None or held.lease is None or held.lease_raw is None or not row["token"]:
        return False
    _refresh_keys(held)
    body = {
        "lic": held.licence["lic"], "ver": held.licence["ver"], "iid": _own_iid(), "token": row["token"],
        "seq": held.lease["seq"], "prev": hash_b64(held.lease_raw), "seats": seats_used(),
        "version": product_version(), "nonce": _nonce(),
    }
    if not _devices_refused_at or now >= _devices_refused_at + DEVICES_RETRY:
        body["devices"] = devices_used()
        if _devices_refused_at:
            # The week is over (or the process was restarted and the count is in again): say so once.
            _log_devices_sent_again(now)
    _pend(row, "checkin", body)
    return True


def checkin(now: int | None = None) -> None:
    """Ask for a new lease now, or repeat the request that is still waiting for its answer."""
    _not_on_community()
    clock = int(time.time() if now is None else now)
    with _lock:
        row = _load()
        if row["pending"] is None and not _new_checkin(row, clock):
            raise LicenceError("This installation holds no lease to renew. Enter the activation key.", final=True)
        _attempt(row, clock)


def deactivate(now: int | None = None) -> None:
    """Free this installation's slot. On the server's yes the lease and the token are discarded."""
    _not_on_community()
    with _lock:
        row = _load()
        held = _held(row)
        if held.licence is None or held.licence["offline"] or not row["token"]:
            raise LicenceError("This installation holds no activation to release.", final=True)
        if not (row["pending"] and row["pending"]["kind"] == "deactivate"):
            _pend(row, "deactivate", {"lic": held.licence["lic"], "iid": _own_iid(), "token": row["token"],
                                      "nonce": _nonce()})
        _attempt(row, int(time.time() if now is None else now))


_FILE_PROBLEMS = {
    "bad_envelope": "That is not a licence file.",
    "wrong_iid": "That licence file was issued for another installation.",
    "not_offline": "That file is an online licence. Enter the activation key instead.",
    "lower_ver": "That licence file is older than the licence already installed.",
    "same_ver_differs": "That licence file has the version already installed but is not the same licence.",
}


def load_licence_file(text: Any) -> None:
    """Install an offline licence: verified for this installation before anything is stored."""
    _not_on_community()
    with _lock:
        row = _load()
        held = _held(row)
        try:
            envelope = load_envelope(text) if isinstance(text, (str, bytes)) else text
            licence, raw = held.trust.verify_licence(envelope, 0, installation_id())
            if not licence["offline"]:
                raise FormatError("not_offline")
            if held.licence is not None and licence["lic"] == held.licence["lic"]:
                held.trust.verify_replacement(row["licence"], envelope, 0, installation_id())
        except FormatError as exc:
            raise LicenceError(
                _FILE_PROBLEMS.get(exc.reason, f"That licence file did not pass verification ({exc.reason})."),
                final=True,
            ) from None
        if raw == held.licence_raw:
            return  # the licence already held: nothing changes
        # An offline licence has no leases, so the lease, the token and any request of the licence before it go.
        _save(licence=envelope, lease=None, token=None, pending=None)


def request_code(deployment_name: str) -> str:
    """The offline request code: names this installation to the vendor. No network call, nothing stored."""
    _not_on_community()
    return build_request_code(instance_id=installation_id(), deployment_name=deployment_name,
                              product_version=product_version())


def _refresh_keys(held: Held) -> None:
    """GET /v1/keys: learn a rotated key before meeting it in an answer. Best effort; only certificates a root
    key signed are kept."""
    try:
        listed = _call("GET", "/v1/keys").get("certs")
    except LicenceError:
        return
    certs = dict(held.certs)
    for envelope in listed if isinstance(listed, list) else []:
        try:
            certs[held.trust.accept_cert(envelope, 0)["kid"]] = envelope
        except FormatError:
            pass
    if certs != held.certs:
        held.row["certs"] = list(certs.values())  # the caller goes on with this row
        _save(certs=held.row["certs"])


def tick(now: int | None = None) -> None:
    """The background half, called every minute by the job loop: keep the mark, repeat a pending request and
    make the daily check-in, with the protocol's waits after a failure. Never raises for a failed call.

    A Community build does nothing: no row is read and the licence server is not called.
    """
    if community_build():
        return
    clock = int(time.time() if now is None else now)
    with _lock:
        row = _load()
        if clock >= row["mark"] + MARK_STEP:
            row["mark"] = next_mark(row["mark"], clock)
            _save(mark=row["mark"])
        # A clock set back makes every stored time look like the future; then the wait is not trusted either.
        if clock < row["next_attempt_at"] and clock >= row["last_attempt_at"]:
            return
        try:
            if row["pending"] is None and not _new_checkin(row, clock):
                return
            _attempt(row, clock)
        except LicenceError:
            pass  # recorded by _attempt; the licence page shows it
