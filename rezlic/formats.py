"""Signed documents, version 2: envelope, payload rules and what a product accepts.

Implements spec/formats.md. Standard library and `cryptography` only; no clock, no environment, no I/O: the
caller passes `now`. Every refusal is a FormatError whose `reason` is a short machine-readable code
("bad_signature", "bad_field:seats", ...); the vectors in spec/vectors/ pin those codes.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from collections.abc import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

TYPES = ("licence", "lease", "cert")
TOLERANCE = 3600  # one hour, for every comparison of a signed time against a clock
DAY = 86400
KID_RE = re.compile(r"[a-z0-9-]{3,40}")
TRIAL_LIC_PREFIX = "REZ-TRIAL-"  # licence ids of the trial key; a root key never signs one, the trial key nothing else
# A licence's device limit when it carries no `devices` field (spec/formats.md). Any other edition, and every trial,
# has none. The licence server, the home signer and the product read this one table.
EDITION_DEVICES = {"free": 100, "pro": 500, "ultra": 500}
CONTROL_RE = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029]")  # what a customer name may not hold


class FormatError(Exception):
    """A document that breaks the spec. `reason` is a short code, never free text."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# --- encoding helpers -------------------------------------------------------------------------------------------

def b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64d(text, reason: str = "bad_base64") -> bytes:
    """Strict base64url without padding: wrong alphabet, padding or stray trailing bits are refused."""
    if not isinstance(text, str) or not re.fullmatch(r"[A-Za-z0-9_-]*", text) or len(text) % 4 == 1:
        raise FormatError(reason)
    raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    if b64e(raw) != text:
        raise FormatError(reason)
    return raw


def hash_b64(data: bytes) -> str:
    """The spec's "hash": SHA-256 as base64url."""
    return b64e(hashlib.sha256(data).digest())


def load_public(text) -> Ed25519PublicKey:
    """A public key from base64url of the raw 32 bytes."""
    raw = b64d(text, "bad_key")
    if len(raw) != 32:
        raise FormatError("bad_key")
    return Ed25519PublicKey.from_public_bytes(raw)


def payload_bytes(obj) -> bytes:
    """Compact UTF-8 JSON, produced once: these exact bytes are signed and carried. Keys are sorted so the same
    document always gives the same bytes."""
    return json.dumps(obj, separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode("utf-8")


def message(typ: str, payload: bytes) -> bytes:
    return b"rezztor." + typ.encode("ascii") + b".v2\n" + payload


# --- envelope ---------------------------------------------------------------------------------------------------

def sign(typ: str, kid: str, payload, private_key) -> dict:
    """Sign a payload (a dict, or bytes already serialised) and return the envelope. Does not validate the
    payload: callers that must produce a good document call the validator first."""
    if typ not in TYPES:
        raise ValueError("unknown typ")
    raw = payload if isinstance(payload, bytes) else payload_bytes(payload)
    return {"v": 2, "typ": typ, "kid": kid, "alg": "Ed25519", "payload": b64e(raw),
            "sig": b64e(private_key.sign(message(typ, raw)))}


def open_envelope(envelope, expected_typ: str, public_key_for_kid):
    """The verifier steps of the spec, in order. `public_key_for_kid` is a mapping or a callable returning a
    public key or None (it may raise FormatError itself, e.g. for a key not allowed to sign this type).
    Returns (payload dict, payload bytes) with the type's field rules already applied."""
    # 1. envelope shape, v, alg, typ
    if not isinstance(envelope, dict):
        raise FormatError("bad_envelope")
    if type(envelope.get("v")) is not int or envelope["v"] != 2:
        raise FormatError("bad_version")
    if envelope.get("alg") != "Ed25519":
        raise FormatError("bad_alg")
    if envelope.get("typ") != expected_typ:
        raise FormatError("bad_typ")
    kid = envelope.get("kid")
    if not isinstance(kid, str) or not KID_RE.fullmatch(kid):
        raise FormatError("bad_kid")
    # 2. the key
    lookup = public_key_for_kid.get if isinstance(public_key_for_kid, Mapping) else public_key_for_kid
    key = lookup(kid)
    if key is None:
        raise FormatError("unknown_kid")
    # 3. the signature
    raw = b64d(envelope.get("payload"), "bad_payload")
    sig = b64d(envelope.get("sig"), "bad_sig")
    if len(sig) != 64:
        raise FormatError("bad_sig")
    try:
        key.verify(sig, message(expected_typ, raw))
    except InvalidSignature:
        raise FormatError("bad_signature") from None
    # 4. only now the payload
    payload = _parse_object(raw)
    if payload.get("typ") != expected_typ:
        raise FormatError("payload_typ_mismatch")
    # 5. the type's rules
    VALIDATORS[expected_typ](payload)
    return payload, raw


def _reject_constant(name):
    raise ValueError(name)


def _no_repeats(pairs):
    """A key named twice is refused: json would keep the last, another parser the first, and they would then
    disagree about what was signed."""
    obj = dict(pairs)
    if len(obj) != len(pairs):
        raise ValueError("repeated key")
    return obj


def _parse_object(raw: bytes) -> dict:
    try:
        obj = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant, object_pairs_hook=_no_repeats)
    except ValueError:
        raise FormatError("bad_payload") from None
    if not isinstance(obj, dict):
        raise FormatError("bad_payload")
    return obj


def dump_envelope(envelope: dict) -> str:
    """The text of a licence file (.rezlic): the envelope as one JSON line."""
    return json.dumps(envelope, separators=(",", ":")) + "\n"


def load_envelope(text) -> dict:
    """Parse a licence file. The file is the envelope and nothing else."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    try:
        env = json.loads(text.strip(), parse_constant=_reject_constant, object_pairs_hook=_no_repeats)
    except ValueError:
        raise FormatError("bad_envelope") from None
    if not isinstance(env, dict):
        raise FormatError("bad_envelope")
    return env


# --- field rules ------------------------------------------------------------------------------------------------

def _bad(name):
    raise FormatError("bad_field:" + name)


def _int(p, name, lo=0, hi=None):
    v = p.get(name)
    if type(v) is not int or v < lo or (hi is not None and v > hi):  # type(): a bool is not an integer here
        _bad(name)
    return v


def _str(p, name, rx):
    v = p.get(name)
    if not isinstance(v, str) or not rx.fullmatch(v):
        _bad(name)
    return v


def _hash(p, name):
    v = p.get(name)
    try:
        if len(b64d(v)) != 32:
            _bad(name)
    except FormatError:
        _bad(name)


_LIC = re.compile(r"REZ-[A-Z0-9-]{4,36}")
_EDITION = re.compile(r"[a-z0-9-]{1,40}")
_FEATURE = re.compile(r"[a-z0-9_-]{1,40}")
_IID = re.compile(r"[A-Za-z0-9_-]{8,64}")
_USES = ("lease", "trial-licence")
_LIMITS = ("max_days", "max_seats", "max_instances")


def validate_cert(p: dict) -> None:
    _str(p, "kid", KID_RE)
    try:
        if len(b64d(p.get("pub"))) != 32:
            _bad("pub")
    except FormatError:
        _bad("pub")
    use = p.get("use")
    if not isinstance(use, list) or not use or len(set(map(str, use))) != len(use) \
            or any(u not in _USES for u in use):
        _bad("use")
    nbf = _int(p, "nbf")
    exp = _int(p, "exp")
    if exp < nbf or exp - nbf > 100 * DAY:
        _bad("exp")
    limits = p.get("limits")
    if not isinstance(limits, dict):
        _bad("limits")
    if "trial-licence" in use:
        if set(limits) != set(_LIMITS):  # exactly these three: an extra key is refused, not ignored
            _bad("limits")
        for name in _LIMITS:
            if type(limits[name]) is not int or limits[name] < 1:
                _bad("limits")
    elif limits != {}:
        _bad("limits")


def validate_licence(p: dict) -> None:
    _str(p, "lic", _LIC)
    _int(p, "ver", 1)
    c = p.get("customer")
    if not isinstance(c, str) or not 1 <= len(c) <= 200 or CONTROL_RE.search(c):
        _bad("customer")
    if p.get("kind") not in ("trial", "subscription"):
        _bad("kind")
    _str(p, "edition", _EDITION)
    f = p.get("features")
    if not isinstance(f, list) or any(not isinstance(x, str) or not _FEATURE.fullmatch(x) for x in f):
        _bad("features")
    _int(p, "seats", 1)
    _int(p, "instances", 1)
    if "devices" in p:  # optional: a licence without it takes its edition's limit in the product
        _int(p, "devices", 1)
    _int(p, "iat")
    nbf = _int(p, "nbf")
    if _int(p, "exp") < nbf:
        _bad("exp")
    _int(p, "lease_days", 1, 90)
    _int(p, "grace_days", 0, 90)
    offline = p.get("offline")
    if type(offline) is not bool:
        _bad("offline")
    if offline:
        _str(p, "offline_iid", _IID)
    elif "offline_iid" in p:
        _bad("offline_iid")


def validate_lease(p: dict) -> None:
    _str(p, "lic", _LIC)
    _int(p, "ver", 1)
    _str(p, "iid", _IID)
    seq = _int(p, "seq", 1)
    if seq == 1:
        if p.get("prev") != "":
            _bad("prev")
    else:
        _hash(p, "prev")
    iat = _int(p, "iat")
    if _int(p, "exp") < iat:
        _bad("exp")
    if _int(p, "now") != iat:
        _bad("now")
    try:
        if not 16 <= len(b64d(p.get("nonce"))) <= 32:
            _bad("nonce")
    except FormatError:
        _bad("nonce")
    status = p.get("status")
    if status not in ("ok", "revoked", "released"):
        _bad("status")
    if _int(p, "notice_until") != 0 and status != "revoked":
        _bad("notice_until")
    _hash(p, "licence_hash")


VALIDATORS = {"cert": validate_cert, "licence": validate_licence, "lease": validate_lease}


# --- what a product accepts -------------------------------------------------------------------------------------

def _in_window(cert: dict, t: int) -> bool:
    return cert["nbf"] - TOLERANCE <= t <= cert["exp"] + TOLERANCE


class TrustStore:
    """The root public keys (shipped in the product) plus the certificates accepted so far."""

    def __init__(self, roots: Mapping):
        self.roots = {kid: load_public(k) if isinstance(k, str) else k for kid, k in roots.items()}
        self.certs: dict[str, dict] = {}  # kid -> accepted cert payload
        self._pubs: dict[str, Ed25519PublicKey] = {}

    def accept_cert(self, envelope, now: int) -> dict:
        """Signed by a root key. Returns the payload. `now` is not used: the window is applied to each document's
        own `iat` (verify_licence, verify_lease), never to today's date, so a product can accept its stored
        certificates again at every start, long after they ended."""
        payload, _ = open_envelope(envelope, "cert", self.roots)
        kid = payload["kid"]
        # a certificate may not shadow a root, nor swap the public key of a kid already trusted
        if kid in self.roots or (kid in self.certs and self.certs[kid]["pub"] != payload["pub"]):
            raise FormatError("kid_conflict")
        self.certs[kid] = payload
        self._pubs[kid] = load_public(payload["pub"])
        return payload

    def _finder(self, use: str):
        def find(kid):
            cert = self.certs.get(kid)
            if cert is None:
                if use == "trial-licence" and kid in self.roots:
                    return self.roots[kid]
                if kid in self.roots:
                    raise FormatError("key_not_allowed")  # a root key signs licences and certificates only
                return None
            if use not in cert["use"]:
                raise FormatError("key_not_allowed")
            return self._pubs[kid]
        return find

    def verify_licence(self, envelope, now: int, iid=None):
        """Root-signed, or signed by a trial key within its certificate's limits; offline rules.
        `iid` is this installation's id: a product always passes it, and an offline licence bound to another
        installation is then refused (the server, which is no installation, passes none).
        `now` is not used: a licence outlives the 100-day certificate of the key that signed it, so the
        certificate is checked at the licence's own `iat`, not at today's date."""
        payload, raw = open_envelope(envelope, "licence", self._finder("trial-licence"))
        kid = envelope["kid"]
        is_root = kid in self.roots
        if is_root == payload["lic"].startswith(TRIAL_LIC_PREFIX):  # the id says which class of key may sign it
            raise FormatError("trial_lic_reserved" if is_root else "trial_lic_required")
        if payload["offline"]:
            if not is_root:
                raise FormatError("offline_not_root")
            if payload["exp"] - payload["nbf"] > 366 * DAY:
                raise FormatError("offline_term")
            if iid is not None and not hmac.compare_digest(payload["offline_iid"], str(iid)):
                raise FormatError("wrong_iid")
        if not is_root:
            cert = self.certs[kid]
            lim = cert["limits"]
            if not _in_window(cert, payload["iat"]):
                raise FormatError("cert_window")
            if payload["kind"] != "trial":
                raise FormatError("trial_only")
            if payload["exp"] - payload["iat"] > lim["max_days"] * DAY:
                raise FormatError("trial_term")
            if payload["seats"] > lim["max_seats"]:
                raise FormatError("trial_seats")
            if payload["instances"] > lim["max_instances"]:
                raise FormatError("trial_instances")
        return payload, raw

    def verify_replacement(self, held_envelope, new_envelope, now: int, iid=None):
        """spec/formats.md "Replacing a licence": may `new_envelope` take the place of the licence held?
        Same lic, a higher ver, a signer of the same class. Returns the new (payload, bytes); the licence already
        held (same ver, same bytes) is returned as it is. Anything else raises and the caller keeps what it holds."""
        held, held_raw = self.verify_licence(held_envelope, now, iid)
        if not isinstance(new_envelope, dict):
            raise FormatError("bad_envelope")
        if (new_envelope.get("kid") in self.roots) != (held_envelope["kid"] in self.roots):
            raise FormatError("signer_class")
        new, raw = self.verify_licence(new_envelope, now, iid)
        if new["lic"] != held["lic"]:
            raise FormatError("other_lic")
        if new["ver"] < held["ver"]:
            raise FormatError("lower_ver")
        if new["ver"] == held["ver"] and raw != held_raw:
            raise FormatError("same_ver_differs")
        return new, raw

    def verify_lease(self, envelope, licence: dict, licence_bytes: bytes, iid: str, nonce: str):
        """A lease from a key certified for `lease`, for this licence (and the version held), this
        installation and the nonce just sent."""
        payload, raw = open_envelope(envelope, "lease", self._finder("lease"))
        if payload["lic"] != licence["lic"]:
            raise FormatError("wrong_lic")
        if payload["ver"] != licence["ver"]:
            raise FormatError("wrong_ver")
        if not hmac.compare_digest(payload["licence_hash"], hash_b64(licence_bytes)):
            raise FormatError("wrong_licence_hash")
        if not hmac.compare_digest(payload["iid"], str(iid)):
            raise FormatError("wrong_iid")
        if not hmac.compare_digest(payload["nonce"], str(nonce)):
            raise FormatError("wrong_nonce")
        if licence["offline"]:
            raise FormatError("offline_licence_no_lease")
        if payload["exp"] != min(payload["iat"] + licence["lease_days"] * DAY, licence["exp"]):
            raise FormatError("bad_field:exp")
        if not _in_window(self.certs[envelope["kid"]], payload["iat"]):
            raise FormatError("cert_window")
        return payload, raw
