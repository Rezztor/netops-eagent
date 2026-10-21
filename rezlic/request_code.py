"""The activation request code: what a deployment says about itself.

A customer with no route to the licence server cannot activate. The way out is
an exchange of two artifacts by email:

    instance  --[ request code ]-->  Rezztor  --[ signed licence ]-->  instance

This module owns the first one. The instance prints a short code naming itself;
the operator pastes it into the licence console, which signs a licence bound to
that instance and nothing else.

**This file lives in two repositories and the two copies must stay
byte-identical** — `helpers/license_request.py` in the product, and
`app/license_request.py` in the licence server. The product encodes and the
server decodes, so a change made on one side and not the other turns every
request code into a checksum failure. The licence server's
`tests/test_licence_format_contract.py` diffs them.

Why a packed binary format and not JSON
---------------------------------------
The request code travels the way a human sends things -- pasted into an email,
sometimes retyped, occasionally read aloud. JSON with gzip and base32 measured
189 characters; this measures 80. It is also checksummed, which matters more
than the length: a mistyped character has to fail loudly, because a request
code that decodes to the *wrong* instance id produces a licence that installs
nowhere and gives nobody a reason why.

Crockford's base32 alphabet excludes I, L, O and U, so nothing in the output
can be confused with 1 or 0, and the decoder folds the confusable characters
back anyway.

What it is not
--------------
Not secret, and not authenticated. It travels by email and says only what a
deployment already knows about itself. Anyone can compose one for an instance
id they choose -- but a licence bound to an id you invented only runs on an
instance carrying that id, which is no more than someone could achieve by
setting their own. Binding raises the cost of over-deployment; it does not
make it impossible, and this file does not pretend otherwise.
"""

from __future__ import annotations

import re
import struct
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone

PREFIX = "REZ-REQ-"
FORMAT_VERSION = 1
GROUP_SIZE = 5

# Crockford base32: no I, L, O or U.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_DECODE = {ch: i for i, ch in enumerate(_ALPHABET)}
# The characters Crockford leaves out, folded back to what they were meant to
# be. Someone retyping from an email will write O for 0 sooner or later.
_DECODE.update({"O": 0, "I": 1, "L": 1, "U": _ALPHABET.index("V")})

# Bit 0 of the flags byte: the instance id was a 32-character hex string and is
# stored as its 16 raw bytes. Anything else is stored as UTF-8, which costs 16
# bytes but keeps an operator-set id working instead of failing to encode.
_FLAG_ID_IS_HEX = 0b1

MAX_NAME = 40
MAX_VERSION = 24
_HEX32 = re.compile(r"^[0-9a-f]{32}$")


class RequestCodeError(Exception):
    """A request code that could not be read. `reason` is shown to a person."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ActivationRequest:
    """What a request code carries."""

    instance_id: str
    deployment_name: str
    product_version: str
    issued_at: datetime

    def age_days(self, now: datetime | None = None) -> int:
        now = now or datetime.now(timezone.utc)
        return max(0, (now - self.issued_at).days)


def _b32_encode(data: bytes) -> str:
    if not data:
        return ""
    bits = len(data) * 8
    value = int.from_bytes(data, "big")
    # Round up to whole 5-bit groups, left-padding the most significant group.
    chars = (bits + 4) // 5
    value <<= chars * 5 - bits
    return "".join(_ALPHABET[(value >> shift) & 31] for shift in range((chars - 1) * 5, -1, -5))


def _b32_decode(text: str, byte_length: int) -> bytes:
    value = 0
    for ch in text:
        try:
            value = value * 32 + _DECODE[ch]
        except KeyError:
            raise RequestCodeError(f"the code contains a character that is not valid: {ch!r}")
    bits = len(text) * 5
    value >>= bits - byte_length * 8
    return value.to_bytes(byte_length, "big")


def _pack_text(value: str, limit: int, field: str) -> bytes:
    encoded = value.encode("utf-8")[:limit]
    if len(encoded) > 255:
        raise RequestCodeError(f"{field} is too long")
    return bytes([len(encoded)]) + encoded


def build_request_code(
    *,
    instance_id: str,
    deployment_name: str,
    product_version: str,
    issued_at: datetime | None = None,
) -> str:
    """Encode what this deployment is, for an operator to paste into the console."""
    instance_id = (instance_id or "").strip()
    if not instance_id:
        raise RequestCodeError("this instance has no id to request activation with")

    issued_at = issued_at or datetime.now(timezone.utc)

    if _HEX32.match(instance_id):
        flags = _FLAG_ID_IS_HEX
        id_bytes = bytes.fromhex(instance_id)
    else:
        flags = 0
        id_bytes = instance_id.encode("utf-8")[:255]

    body = (
        bytes([FORMAT_VERSION, flags, len(id_bytes)])
        + id_bytes
        + struct.pack("!I", int(issued_at.timestamp()))
        + _pack_text(product_version, MAX_VERSION, "version")
        + _pack_text(deployment_name, MAX_NAME, "deployment name")
    )
    body += struct.pack("!I", zlib.crc32(body))

    encoded = _b32_encode(body)
    grouped = "-".join(encoded[i : i + GROUP_SIZE] for i in range(0, len(encoded), GROUP_SIZE))
    return PREFIX + grouped


def parse_request_code(code: str) -> ActivationRequest:
    """Decode a request code, or raise RequestCodeError saying why not.

    Tolerant of how the code arrives: case, grouping dashes, surrounding
    whitespace and a missing prefix are all accepted, because it will be copied
    out of an email client that may have done any of them. What is *not*
    tolerated is a failed checksum -- see the module docstring.
    """
    if not isinstance(code, str) or not code.strip():
        raise RequestCodeError("no request code was provided")

    # Whitespace and dashes go first, together. Stripping the prefix before the
    # dashes looked right and was not: a code whose dashes had already been
    # removed in transit no longer starts with "REZ-REQ-", so the prefix was
    # decoded as payload and every such code failed its checksum. Normalise
    # everything, then look for the prefix in its de-dashed form.
    cleaned = re.sub(r"[\s\-]+", "", code).upper()
    bare_prefix = PREFIX.replace("-", "")
    if cleaned.startswith(bare_prefix):
        cleaned = cleaned[len(bare_prefix) :]
    if not cleaned:
        raise RequestCodeError("no request code was provided")

    # Every 8 characters carry 5 bytes; a partial group means truncation.
    byte_length = len(cleaned) * 5 // 8
    if byte_length < 12:
        raise RequestCodeError("that code is too short to be an activation request")

    raw = _b32_decode(cleaned, byte_length)
    body, checksum = raw[:-4], raw[-4:]
    if struct.unpack("!I", checksum)[0] != zlib.crc32(body):
        # The single most likely failure, and the one worth being clearest
        # about: a wrong character must never decode to a plausible instance id.
        raise RequestCodeError(
            "that code did not survive the journey -- a character is wrong or "
            "missing. Ask for it again rather than guessing."
        )

    try:
        version, flags, id_len = body[0], body[1], body[2]
        cursor = 3
        id_bytes = body[cursor : cursor + id_len]
        cursor += id_len
        (issued_ts,) = struct.unpack("!I", body[cursor : cursor + 4])
        cursor += 4
        ver_len = body[cursor]
        cursor += 1
        product_version = body[cursor : cursor + ver_len].decode("utf-8")
        cursor += ver_len
        name_len = body[cursor]
        cursor += 1
        deployment_name = body[cursor : cursor + name_len].decode("utf-8")
    except (IndexError, struct.error, UnicodeDecodeError):
        raise RequestCodeError("that code is not an activation request")

    if version != FORMAT_VERSION:
        raise RequestCodeError(
            f"that code is format version {version}; this console reads version "
            f"{FORMAT_VERSION}. The instance is running a newer or older build."
        )

    instance_id = id_bytes.hex() if flags & _FLAG_ID_IS_HEX else id_bytes.decode("utf-8")
    return ActivationRequest(
        instance_id=instance_id,
        deployment_name=deployment_name,
        product_version=product_version,
        issued_at=datetime.fromtimestamp(issued_ts, tz=timezone.utc),
    )
