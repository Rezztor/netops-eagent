"""The upstream MIT notice, and that it stays.

This product is a derivative work of the MIT-licensed Agent Zero. MIT allows it on one condition: "The above copyright
notice and this permission notice shall be included in all copies or substantial portions of the Software."
THIRD-PARTY-NOTICES.md holds the only copy. It was removed once (2026-09-30, with the test that guarded it) and every
build until it came back was outside that licence.
"""

from pathlib import Path

NOTICE = Path(__file__).resolve().parents[1] / "THIRD-PARTY-NOTICES.md"


def test_the_upstream_notice_is_retained_whole():
    text = NOTICE.read_text(encoding="utf-8")
    assert "Copyright (c) 2025 Agent Zero, s.r.o" in text
    assert "Permission is hereby granted, free of charge" in text
    assert "The above copyright notice and this permission notice shall be included" in text
    assert 'THE SOFTWARE IS PROVIDED "AS IS"' in text


def test_the_notice_ships_in_the_image():
    """The image copies the source tree minus .dockerignore; the notice must not be one of the files it drops."""
    ignored = (NOTICE.parent / ".dockerignore").read_text(encoding="utf-8").split()
    assert not any(p in ("*.md", "**/*.md", NOTICE.name) for p in ignored)
