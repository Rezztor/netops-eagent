# backup_crypto.py DOX

## Purpose

- Seal and open backup archives with a passphrase the administrator types. A backup holds `usr/.env` (API keys, tokens, the Azure client secret or certificate key), every account's password hash and every user's files, so the downloaded file must be unreadable without it.

## Ownership

- `backup_crypto.py` owns the file format and the key derivation; `helpers/backup.py` is the only caller that builds or opens archives, `api/backup_create.py` also calls `check_passphrase` before building anything.
- Public: `encrypt_file(source, dest, passphrase)`, `decrypt_file(source, dest, passphrase)`, `is_encrypted(path)`, `check_passphrase(passphrase)`, `BackupCryptoError`, `MIN_PASSPHRASE` (12), `EXTENSION` (`.rzbak`).

## Runtime Contracts

- Format, big-endian: header `"RZBAKENC"` | version u8 (1) | log2(n) u8 (15) | r u8 (8) | p u8 (1) | chunk size u32 (1 MiB) | salt 16 bytes | nonce prefix 8 bytes; then the plaintext in chunks, each AES-256-GCM sealed (16-byte tag appended). Key = scrypt(NFC-normalised UTF-8 passphrase, salt, n, r, p).
- Chunk i uses nonce `prefix || i (u32)` and associated data `header || i (u32) || final (u8)`. The header is therefore authenticated, and a flipped byte, a dropped, repeated or reordered chunk, a truncation and trailing bytes all fail; the last chunk is the only one flagged final (an empty file is one empty final chunk).
- Streaming: one chunk (plus one look-ahead) in memory at a time, so a backup of any size is sealed and opened in constant memory.
- Parameters come from the file on decrypt, bounded (log2 n 10..17, r <= 8, p <= 4, chunk <= 64 MiB; scrypt then needs at most 128 MiB) so a hostile header cannot ask for unbounded memory or work.
- Output files are created `O_EXCL` with mode 0600. A failed or refused decrypt removes its output, so a caller never finds partial plaintext. Plaintext is written as chunks authenticate, so the destination must be a private directory (the callers use a fresh `mkdtemp`).
- Wrong passphrase, damaged header, bad tag and truncation all raise `BackupCryptoError` with the same message, so the error does not say which. An unknown version has its own message.
- The passphrase is never logged, stored or echoed; this module does no logging.

## Work Guidance

- Changing the format means a new `VERSION` and keeping the reader for the old one: customers keep these files for years.
- Never lower the minimum passphrase length or the scrypt parameters for new files without a reason; raising them is safe because the parameters travel in the header.

## Verification

- `tests/test_backup_encryption.py`.

## Child DOX Index

No child DOX files.
