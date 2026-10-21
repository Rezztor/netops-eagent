"""Activation keys and key files. Standard library and `cryptography` only; randomness from `secrets`."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .formats import KID_RE, FormatError, b64d, b64e, hash_b64, load_public

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXY"  # Crockford base32 without Z: 31 characters, so mod 31 is exact


class ActivationKeyError(ValueError):
    """`reason` is one of bad_length, bad_char, bad_check."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _check_char(chars: str) -> str:
    return ALPHABET[sum((i + 1) * ALPHABET.index(c) for i, c in enumerate(chars)) % 31]


def generate() -> str:
    """A new activation key, REZ-XXXXX-XXXXX-XXXXX-XXXXX-XXXXC. Shown once; the server stores only its hash."""
    body = "".join(secrets.choice(ALPHABET) for _ in range(24))
    raw = body + _check_char(body)
    return "REZ-" + "-".join(raw[i:i + 5] for i in range(0, 25, 5))


def normalise(text) -> str:
    """The 25-character key from whatever a person typed, or ActivationKeyError."""
    if not isinstance(text, str):
        raise ActivationKeyError("bad_length")
    s = re.sub(r"[\s-]", "", text).upper()
    if s.startswith("REZ"):  # Z is not in the alphabet, so a leading REZ is always the prefix
        s = s[3:]
    s = s.replace("O", "0").replace("I", "1").replace("L", "1")
    if len(s) != 25:
        raise ActivationKeyError("bad_length")
    if any(c not in ALPHABET for c in s):
        raise ActivationKeyError("bad_char")
    if s[24] != _check_char(s[:24]):
        raise ActivationKeyError("bad_check")
    return s


def key_hash(normalised: str) -> str:
    return hash_b64(normalised.encode("ascii"))


# --- key pairs and key files ------------------------------------------------------------------------------------

def new_private_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


MIN_PASSPHRASE = 16  # characters, for an encrypted (root) key
_SCRYPT = {"n": 2**17, "r": 8, "p": 1}  # 128 MiB per guess: the passphrase is the only thing between a stolen file and the key
_MAGIC = "rezlic-key"


def _scrypt_key(passphrase: bytes, salt: bytes) -> bytes:
    return hashlib.scrypt(passphrase, salt=salt, **_SCRYPT, maxmem=2**28, dklen=32)


def private_to_pem(key, passphrase: bytes | None = None) -> bytes:
    """Unencrypted: PKCS8 PEM. With a passphrase: a JSON envelope, the PKCS8 DER under AES-256-GCM with a
    scrypt key (cryptography's own PKCS8 encryption is PBKDF2 and cannot be made memory-hard)."""
    if not passphrase:
        return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                 serialization.NoEncryption())
    if len(passphrase.decode("utf-8", "replace")) < MIN_PASSPHRASE:
        raise ValueError(f"the passphrase must be at least {MIN_PASSPHRASE} characters")
    der = key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    head = {"format": _MAGIC, "v": 1, "kdf": "scrypt", **_SCRYPT, "salt": b64e(salt)}
    ct = AESGCM(_scrypt_key(passphrase, salt)).encrypt(nonce, der, json.dumps(head, sort_keys=True).encode())
    return (json.dumps({**head, "nonce": b64e(nonce), "ct": b64e(ct)}, indent=1) + "\n").encode()


def private_from_pem(data: bytes, passphrase: bytes | None = None):
    """Reads the envelope above, or an older PEM (plain or PKCS8-encrypted, so existing keys keep working)."""
    if not data.lstrip().startswith(b"{"):
        return serialization.load_pem_private_key(data, passphrase or None)
    try:
        env = json.loads(data)
        head = {k: env[k] for k in ("format", "v", "kdf", "n", "r", "p", "salt")}
        if head != {"format": _MAGIC, "v": 1, "kdf": "scrypt", **_SCRYPT, "salt": head["salt"]}:
            raise ValueError("unknown key file format")
        if not passphrase:
            raise ValueError("this key file needs its passphrase")
        der = AESGCM(_scrypt_key(passphrase, b64d(head["salt"], "bad_key"))).decrypt(
            b64d(env["nonce"], "bad_key"), b64d(env["ct"], "bad_key"), json.dumps(head, sort_keys=True).encode())
    except (KeyError, TypeError, InvalidTag, FormatError):
        raise ValueError("wrong passphrase or damaged key file") from None
    return serialization.load_der_private_key(der, None)


def write_private(path, key, passphrase: bytes | None = None) -> None:
    """Write a key file readable by its owner only; refuses to overwrite."""
    data = private_to_pem(key, passphrase)  # first: a refused passphrase must not leave an empty file
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def read_private(path, passphrase: bytes | None = None):
    with open(path, "rb") as f:
        return private_from_pem(f.read(), passphrase)


def is_encrypted(path) -> bool:
    with open(path, "rb") as f:
        data = f.read()
    return b"ENCRYPTED" in data or _MAGIC.encode() in data


def export_public(key) -> str:
    """Base64url of the raw 32 bytes; accepts a private or a public key."""
    pub = key.public_key() if hasattr(key, "public_key") else key
    return b64e(pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))


import_public = load_public


def valid_kid(kid) -> bool:
    return isinstance(kid, str) and KID_RE.fullmatch(kid) is not None
