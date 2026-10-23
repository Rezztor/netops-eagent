"""Passphrase encryption for backup archives.

A backup holds the instance's keys, every account's password hash and every
user's files, so the file an administrator downloads is sealed with a passphrase
they type. The archive can be large, so it is sealed as a stream of chunks and
never held in memory.

File layout (all integers big-endian):

    header   magic "RZBAKENC" | version u8 | log2(n) u8 | r u8 | p u8 |
             chunk size u32 | salt 16 bytes | nonce prefix 8 bytes
    chunks   AES-256-GCM of each plaintext chunk (+16 byte tag), back to back

The key is scrypt(passphrase, salt). Chunk i is sealed under nonce
`prefix || i (u32)`, with the whole header, the index and a "this is the last
chunk" flag as associated data. So a flipped byte, a dropped, repeated or
reordered chunk, a truncated file and bytes appended after the end all fail
authentication, as does the header being edited.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import struct
import unicodedata

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"RZBAKENC"
VERSION = 1
MIN_PASSPHRASE = 12
EXTENSION = ".rzbak"

CHUNK = 1 << 20
TAG = 16
_HEADER = struct.Struct(">8sBBBBI16s8s")
_LOG2_N, _R, _P = 15, 8, 1
# A header comes from the uploaded file: bound what it may ask scrypt and the
# reader to allocate.
_MAX_LOG2_N, _MAX_R, _MAX_P, _MAX_CHUNK = 20, 16, 4, 64 << 20

WRONG = "The backup could not be decrypted: the passphrase is wrong or the file is damaged."


class BackupCryptoError(Exception):
    """A message that is safe to show to the administrator as it is."""


def check_passphrase(passphrase: object) -> str:
    if not isinstance(passphrase, str) or len(passphrase) < MIN_PASSPHRASE:
        raise BackupCryptoError(f"The backup passphrase must be at least {MIN_PASSPHRASE} characters.")
    return passphrase


def is_encrypted(path: str) -> bool:
    with open(path, "rb") as source:
        return source.read(len(MAGIC)) == MAGIC


def _key(passphrase: str, salt: bytes, log2_n: int, r: int, p: int) -> AESGCM:
    n = 1 << log2_n
    secret = unicodedata.normalize("NFC", passphrase).encode("utf-8")
    return AESGCM(hashlib.scrypt(secret, salt=salt, n=n, r=r, p=p, maxmem=256 * n * r, dklen=32))


def _aad(header: bytes, index: int, final: bool) -> bytes:
    return header + struct.pack(">IB", index, final)


def _create(path: str):
    # 0600 and never over a file that exists: the plaintext archive goes through here too.
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb")


def _chunks(source, size: int):
    """(chunk, is_last) for a stream; an empty stream is one empty last chunk."""
    current = source.read(size)
    index = 0
    while True:
        following = source.read(size) if len(current) == size else b""
        yield index, current, not following
        if not following:
            return
        current, index = following, index + 1


def encrypt_file(source_path: str, dest_path: str, passphrase: str) -> None:
    check_passphrase(passphrase)
    salt, prefix = secrets.token_bytes(16), secrets.token_bytes(8)
    header = _HEADER.pack(MAGIC, VERSION, _LOG2_N, _R, _P, CHUNK, salt, prefix)
    aead = _key(passphrase, salt, _LOG2_N, _R, _P)
    dest = _create(dest_path)
    try:
        with open(source_path, "rb") as source, dest:
            dest.write(header)
            for index, chunk, last in _chunks(source, CHUNK):
                dest.write(aead.encrypt(prefix + struct.pack(">I", index), chunk, _aad(header, index, last)))
    except BaseException:
        _remove(dest_path)
        raise


def decrypt_file(source_path: str, dest_path: str, passphrase: str) -> None:
    """Write the plaintext to `dest_path` (0600), or raise and leave nothing there."""
    dest = _create(dest_path)
    try:
        with open(source_path, "rb") as source, dest:
            raw = source.read(_HEADER.size)
            if len(raw) < _HEADER.size:
                raise BackupCryptoError(WRONG)
            magic, version, log2_n, r, p, size, salt, prefix = _HEADER.unpack(raw)
            if magic != MAGIC:
                raise BackupCryptoError(WRONG)
            if version != VERSION:
                raise BackupCryptoError("This backup was made by a newer version and cannot be read here.")
            if not (10 <= log2_n <= _MAX_LOG2_N and 1 <= r <= _MAX_R and 1 <= p <= _MAX_P and 0 < size <= _MAX_CHUNK):
                raise BackupCryptoError(WRONG)
            aead = _key(passphrase or "", salt, log2_n, r, p)
            for index, chunk, last in _chunks(source, size + TAG):
                if len(chunk) < TAG:
                    raise BackupCryptoError(WRONG)
                try:
                    dest.write(aead.decrypt(prefix + struct.pack(">I", index), chunk, _aad(raw, index, last)))
                except InvalidTag:
                    raise BackupCryptoError(WRONG) from None
    except BaseException:
        _remove(dest_path)
        raise


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
