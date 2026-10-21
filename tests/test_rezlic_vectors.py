"""The copy of `rezlic/` in this repository passes every vector of the licence contract.

`rezlic/` and `tests/fixtures/licence_vectors/` are copied byte for byte from the licence-server repository
(`rezlic/`, `spec/vectors/`). The runner below is that repository's `tests/test_vectors.py`; only the path differs.
PINS holds the SHA-256 of each copied module, so an edit made here instead of there fails: change the file in
the licence-server repository, copy it again, and update the pin in the same change.
"""
import hashlib
import json
from pathlib import Path

import pytest

from rezlic import formats as F
from rezlic import keys as K
from rezlic.verdict import next_mark, verdict

ROOT = Path(__file__).resolve().parent.parent
FILES = sorted((ROOT / "tests" / "fixtures" / "licence_vectors").glob("*.json"))
PINS = {
    "__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "formats.py": "32cbbe5b7fb9cabe578d73c135b69a8b88d1c8ab5180dc423361b7c77b0f42a9",
    "keys.py": "2ccdf2c3e8e83cff6637cdeb4dbed4939f73851717a21faefb460d7be3a36e5e",
    "request_code.py": "5bf7fb32cd133117704f2a8a51c5ff931949c37f4d2fc0a9c8153404795746dc",
    "verdict.py": "43aac52bf2f90cc4fb229c77abc5fe5e3655209b10d4afd0d432498a65caa6e3",
}
CASES = [(f.name, c) for f in FILES for c in json.loads(f.read_text(encoding="utf-8"))["cases"]]


def test_vectors_exist():
    assert len(FILES) == 7 and CASES


def test_the_copy_is_the_one_that_was_reviewed():
    found = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT / "rezlic").glob("*.py")}
    assert found == PINS


def run(c):
    op = c["op"]
    if op == "normalise":
        n = K.normalise(c["input"])
        assert n == c["normalised"] and K.key_hash(n) == c["hash"]
        return "ok"
    if op == "verdict":
        v = verdict(c["licence"], c["lease"], c["now"], c["mark"])
        assert v._asdict() == c["expect"]
        return c["expect"]
    if op == "next_mark":
        return next_mark(c["mark"], c["clock"], c["lease_now"])
    store = F.TrustStore(c["roots"])
    for e in c.get("before", []) + c.get("certs", []):
        store.accept_cert(e, c.get("certs_now", c["now"]))
    if op == "accept_cert":
        store.accept_cert(c["envelope"], c["now"])
    elif op == "verify_licence":
        store.verify_licence(c["envelope"], c["now"], c["iid"])
    elif op == "replace_licence":
        store.verify_replacement(c["held"], c["envelope"], c["now"], c["iid"])
    elif op == "verify_lease":
        lic, raw = store.verify_licence(c["licence"], c["now"])
        store.verify_lease(c["envelope"], lic, raw, c["iid"], c["nonce"])
    else:
        raise AssertionError("unknown op " + op)
    return "ok"


@pytest.mark.parametrize("fname,case", CASES, ids=[f"{f}:{c['name']}" for f, c in CASES])
def test_vector(fname, case):
    try:
        outcome = run(case)
    except (F.FormatError, K.ActivationKeyError) as e:
        outcome = e.reason
    assert outcome == case["expect"]
