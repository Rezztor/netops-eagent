"""The brand palette lives in three places, and nothing links them.

`frontend/src/styles/tokens.css` is the source of truth. One other file carries
its own copy of the same values, for a reason that is not going away:

- `helpers/login_page.py` renders before a session exists and therefore before
  the bundle is allowed to load, so it cannot import a token from it.

A third copy, the appliance's first-boot setup page, went with the appliance on
2026-09-18; the test that checked it went too, and its dangling constant
followed on 2026-09-18 because a `Path` that is only joined never touches disk
and so never complains.

Copies drift. These tests are the thing that notices -- the failure mode without
them is a rebrand that lands everywhere except the sign-in page, which is the one
screen every user sees first.

`tokens.css` also states the dark palette twice, once for the system preference
and once for an explicit toggle, because a value defined only inside a media
query never applies in the un-stamped default state. Two statements of the same
palette is a third place to drift, so it is checked too.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TOKENS = REPO / "frontend" / "src" / "styles" / "tokens.css"
LOGIN = REPO / "helpers" / "login_page.py"

_BLOCK = re.compile(r":root[^{]*\{([^}]*)\}")
_DECL = re.compile(r"(--[a-z0-9-]+)\s*:\s*([^;]+);")


def _blocks(css: str) -> list[dict[str, str]]:
    """Every `:root` rule in source order, as token -> value."""
    return [
        {name: value.strip() for name, value in _DECL.findall(body)}
        for body in _BLOCK.findall(css)
    ]


def _colours(block: dict[str, str]) -> dict[str, str]:
    """Only the tokens holding a colour: the rest are type, space and structure."""
    return {
        name: value.lower()
        for name, value in block.items()
        if value.lstrip().startswith("#")
    }


@pytest.fixture(scope="module")
def token_blocks() -> list[dict[str, str]]:
    blocks = _blocks(TOKENS.read_text(encoding="utf-8"))
    # light, prefers-color-scheme dark, [data-theme="dark"]
    assert len(blocks) == 3, f"expected three :root rules in tokens.css, found {len(blocks)}"
    return blocks


@pytest.fixture(scope="module")
def login_blocks() -> list[dict[str, str]]:
    blocks = _blocks(LOGIN.read_text(encoding="utf-8"))
    assert len(blocks) == 2, f"expected two :root rules in login_page.py, found {len(blocks)}"
    return blocks


def test_the_two_dark_palettes_in_tokens_agree(token_blocks):
    """The media-query dark set and the explicit-toggle dark set must be identical.

    They exist separately so that both an unset OS preference and an explicit
    choice resolve correctly. Nothing keeps them equal but this.
    """
    _, media_dark, stamped_dark = token_blocks
    assert _colours(media_dark) == _colours(stamped_dark)


@pytest.mark.parametrize("theme,index", [("light", 0), ("dark", -1)])
def test_login_page_matches_the_token_palette(token_blocks, login_blocks, theme, index):
    """Shared tokens must hold the same value in both files.

    Only the intersection is compared: the login page has no sidebar, so it has
    no reason to define --rail, and requiring it to restate the whole token set
    would make the check noise rather than signal.
    """
    canonical = _colours(token_blocks[index])
    rendered = _colours(login_blocks[index])

    shared = sorted(set(canonical) & set(rendered))
    assert shared, "the login page shares no colour tokens with tokens.css at all"

    drifted = {
        name: (canonical[name], rendered[name])
        for name in shared
        if canonical[name] != rendered[name]
    }
    assert not drifted, (
        f"{theme} palette has drifted between tokens.css and the login page "
        f"(token: tokens.css -> login_page.py): {drifted}"
    )




def test_the_accent_is_distinguishable_from_the_ok_state(token_blocks):
    """Brand and state must stay separately readable.

    The chosen accent is a teal and `--ok` is a green: adjacent hues, about 25
    degrees apart. That is the thinnest margin of the five palettes considered
    (ADR 0017) and it is deliberate -- but it means a later nudge toward green
    would make a link read as a status badge, which is exactly the confusion the
    accent/state split in tokens.css exists to prevent.

    Measured as CIE Lab dE76 rather than RGB distance, because RGB distance is
    not perceptual: it compresses differences between dark colours and would call
    these two nearly identical when the eye separates them easily.

    The floor is a guardrail set just under the shipped value, not a target. A
    deliberate redesign should move it and say so in the ADR; a careless nudge
    should fail here.
    """
    for block, theme in ((token_blocks[0], "light"), (token_blocks[-1], "dark")):
        colours = _colours(block)
        delta = _delta_e(colours["--accent"], colours["--ok"])
        assert delta >= 20, (
            f"the {theme} accent {colours['--accent']} has drifted toward "
            f"--ok {colours['--ok']} (dE76 {delta:.1f}, floor 20): a link would "
            f"start reading as a status chip"
        )


def _delta_e(a: str, b: str) -> float:
    """CIE Lab dE76 between two hex colours."""
    la, lb = _lab(a), _lab(b)
    return sum((x - y) ** 2 for x, y in zip(la, lb)) ** 0.5


def _lab(value: str) -> tuple[float, float, float]:
    channels = _rgb(value)

    def linear(c: float) -> float:
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(c) for c in channels)
    # sRGB -> XYZ (D65), then XYZ -> Lab against the D65 white point.
    x = (r * 0.4124 + g * 0.3576 + b * 0.1805) / 0.95047
    y = r * 0.2126 + g * 0.7152 + b * 0.0722
    z = (r * 0.0193 + g * 0.1192 + b * 0.9505) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def _rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _luminance(hex_colour: str) -> float:
    rgb = [int(hex_colour.lstrip("#")[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


@pytest.mark.parametrize("surface", ["--bg", "--surface", "--surface-alt", "--accent-wash"])
def test_muted_ink_meets_aa_on_every_surface(token_blocks, surface):
    """--ink-3 carries hints, timestamps and placeholders at the smallest sizes,
    so it is held to WCAG AA for body text (4.5:1) in both themes. It was
    #748c90, 3.3:1 on --bg."""
    light, dark, _ = token_blocks
    for block in (light, {**light, **dark}):
        ratio = _contrast(block["--ink-3"], block[surface])
        assert ratio >= 4.5, f"--ink-3 on {surface}: {ratio:.2f}:1"
