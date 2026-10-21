"""The upstream brand stays out, and this is what holds it out.

This check used to be only a grep recipe in a markdown file -- so nothing ran it, and it regressed. Eight files had
drifted back outside the allowlist by the time anyone looked.

Its patterns had a second problem: `\\ba0[-_]` and `A0_` miss `run_A0.sh`,
`init_a0`, and bare `a0`, all of which were present. The patterns here are the
ones that would have caught what was actually there.

The allowlist is short on purpose. Every entry is a file where the name is
*supposed* to appear, and each has a reason.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Deliberately wider than the old recipe.
PATTERNS = [
    r"agent[-_ ]?zero",
    r"agent0",
    r"\ba0\b",
    r"\ba0[-_]",
    r"[-_]a0\b",
    r"A0_",
    r"_A0\.",
    r"run_A0",
    r"init_a0",
    r"frdel",
]

# Where the name may legitimately appear, and why.
ALLOWED = {
    # The retained MIT notice, and the clause that points at it. Required.
    "THIRD-PARTY-NOTICES.md",
    "LICENSE",
    # This test, and the convention it enforces.
    "tests/test_no_upstream_brand_leak.py",
    "tests/test_attribution_is_retained.py",
    # A rule that forbids calling upstream infrastructure has to name it.
    "CONTRIBUTING.md",
}

# Records of the fork itself: plans, decisions, research. They describe history
# and would be falsified by removing the name from them.
# graphify-out/ is generated knowledge-graph output; it quotes whatever the docs say.
ALLOWED_PREFIXES = ("docs/internal/", "graphify-out/")

# A line may opt out by saying why. One-off migrations that rewrite old values
# have to name the old value, and allowlisting the whole file to permit one
# statement would blind the check to everything else in it.
EXEMPT = "brand-leak-allowed:"
# The encoded bytes of an embedded image are not text: a letter pair inside them is chance, not a name. Only the
# payload is dropped (from "base64," to the end of its run of base64 characters); the rest of the line is checked.
EMBEDDED = re.compile(r"(data:[\w/+.-]+;base64,)[A-Za-z0-9+/=]+")


def _readable(line: str) -> str:
    return EMBEDDED.sub(r"\1", line)


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return [line for line in out.stdout.splitlines() if line]


def _is_allowed(path: str) -> bool:
    return path in ALLOWED or path.startswith(ALLOWED_PREFIXES)


def test_the_upstream_brand_appears_only_where_it_is_meant_to():
    compiled = [re.compile(p, re.IGNORECASE) for p in PATTERNS]
    offenders: dict[str, list[str]] = {}

    for path in _tracked_files():
        if _is_allowed(path):
            continue
        full = ROOT / path
        if not full.is_file():
            continue
        try:
            text = full.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable; nothing to leak

        hits = [
            f"{path}:{n}: {line.strip()[:100]}"
            for n, line in enumerate(text.splitlines(), 1)
            if EXEMPT not in line
            for rx in compiled
            if rx.search(_readable(line))
        ]
        if hits:
            offenders[path] = hits[:3]

    assert not offenders, "the upstream brand leaked back in:\n" + "\n".join(
        line for hits in offenders.values() for line in hits
    )


def test_no_filename_carries_the_upstream_brand():
    """`run_A0.sh` and three siblings survived the rebrand for a year because the
    documented check looked inside files and never at their names."""
    compiled = [re.compile(p, re.IGNORECASE) for p in PATTERNS]
    offenders = [
        path for path in _tracked_files()
        if not _is_allowed(path) and any(rx.search(Path(path).name) for rx in compiled)
    ]
    assert not offenders, f"filenames carry the upstream brand: {offenders}"


def test_an_embedded_image_hides_nothing_beside_it():
    rx = [re.compile(p, re.IGNORECASE) for p in PATTERNS]
    payload = "QUJD" * 500
    line = f'<img src="data:image/webp;base64,{payload}" alt="x">'
    assert _readable(line) == '<img src="data:image/webp;base64," alt="x">'
    # A name the patterns catch is still caught when it stands beside such an image.
    probe = next(w for w in ("".join(map(chr, (97, 103, 101, 110, 116, 32, 122, 101, 114, 111))),
                             "".join(map(chr, (97, 103, 101, 110, 116, 48))),
                             "".join(map(chr, (97, 48))))
                 if any(r.search(w) for r in rx))
    assert any(r.search(_readable(line + " " + probe)) for r in rx)
