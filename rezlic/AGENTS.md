# Licence Formats (rezlic) DOX

## Purpose

- Hold the licence contract's shared formats: signed documents and their verification (`formats.py`), activation keys (`keys.py`), the verdict table and the clock mark (`verdict.py`), and the offline request code (`request_code.py`).

## Ownership

- **The licence-server repository owns every `*.py` file here.** They are copied from its `rezlic/` directory byte for byte. The contract they implement is that repository's `spec/formats.md` and `spec/protocol.md`.
- This repository owns only this file and the proof that the copy is good: `tests/test_rezlic_vectors.py` and `tests/fixtures/licence_vectors/` (copied from the licence server's `spec/vectors/`).
- `helpers/license_client.py` is the only product code that calls into this package, apart from `api/license_activate.py` reading `RequestCodeError`.

## Local Contracts

- Never edit a file here. A change is made in the licence-server repository, copied again in full, and the SHA-256 pins in `tests/test_rezlic_vectors.py` are updated in the same change. If something is missing, it is missing there.
- Copy the vectors with the code: a copy that is newer than its vectors proves nothing.
- The package ships in the product image. It holds no key: the root public keys are `ROOT_KEYS` in `helpers/license_client.py`.
- Standard library and `cryptography` only, as upstream of the copy requires. It does no I/O and reads no clock; the caller passes the time.

## Work Guidance

- Product-side behaviour (storage, the calls, the states, the page) belongs in `helpers/license_client.py` and `helpers/license_entitlements.py`, not here.

## Verification

- `pytest tests/test_rezlic_vectors.py` (every vector, and the pins that show the copy is the reviewed one) and `pytest tests/test_license_request.py` (the request code).

## Child DOX Index

No child DOX files.
