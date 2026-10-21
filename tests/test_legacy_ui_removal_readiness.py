"""Stage 7 guard: do not remove `webui/` while it is the only way to do things.

Stage 7 in the enhancement plan is "remove the old webui/". That is correct as a
goal and wrong as an action until the replacement covers what is being removed.
This test measures the gap and refuses the removal while one exists, so the
decision is made against evidence rather than against a plan written before the
replacement was built.

It works in both directions:

  - While `webui/` exists, it reports the gap (and passes -- the gap is expected
    during the port, and a failing test every day is a test people switch off).
  - Once `webui/` is gone, the same measurement becomes an assertion. If it was
    removed while the new frontend still could not reach an endpoint only the old
    one used, this fails and names them.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

LEGACY_UI = PROJECT_ROOT / "webui"
FRONTEND = PROJECT_ROOT / "frontend" / "src"

# Endpoints whose absence from the new frontend is deliberate rather than a gap.
# Each needs a reason, because "not ported" and "forgotten" look identical from
# a count.
INTENTIONALLY_NOT_PORTED = {
    # The inherited interface's own promotional and onboarding surfaces.
    "banners",
    # Handled by the backend-rendered page, not by the application.
    "logout",
    # Covered by /api/bootstrap, which returns the same identity in one call.
    "whoami",
    # Retired by ADR 0014. This is the plugin UI injection mechanism: it hands
    # the page a manifest of asset URLs to load at runtime, which a compiled
    # bundle has no way to consume. It goes with webui/ rather than being
    # reimplemented.
    "load_webui_extensions",
    # Superseded. The inherited interface uploaded attachments here first and
    # then referenced the saved filenames when sending a message. The new client
    # posts the files with the message in one multipart request, because
    # api/message.py only reads attachments from a multipart body -- its JSON
    # branch discards them. Two round trips and an orphaned file on failure,
    # replaced by one.
    "upload",
}

def _endpoints_called_in(paths, known: set[str]) -> set[str]:
    """Which known endpoints appear as a string literal in these files.

    Deliberately not a parse of the call syntax. The first version of this
    matched `callJsonApi("x")` and friends, and undercounted the gap by thirty
    endpoints: the inherited interface also uses `sendJsonData`, bare `fetch`,
    and multi-line calls where the endpoint sits on its own line. Matching
    against the list of endpoints that actually exist is both simpler and
    robust to however the call happens to be written.
    """
    blob = []
    for path in paths:
        try:
            blob.append(path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
    text = "\n".join(blob)
    return {
        endpoint
        for endpoint in known
        if re.search(rf'["\'`]/?(?:api/)?{re.escape(endpoint)}["\'`]', text)
    }


def _real_endpoints() -> set[str]:
    from helpers import api_schema

    return {spec["endpoint"] for spec in api_schema.collect_endpoints()}


def measure_gap() -> tuple[set[str], set[str], set[str]]:
    """(legacy, next, gap) — endpoints each interface reaches, and the shortfall."""
    real = _real_endpoints()

    legacy_sources = [
        p
        for p in LEGACY_UI.glob("**/*")
        if p.suffix in {".js", ".html"} and "vendor" not in p.parts
    ] if LEGACY_UI.is_dir() else []
    # Plugin-owned UI is served by the same interface and dies with it.
    legacy_sources += [
        p for p in PROJECT_ROOT.glob("plugins/*/webui/**/*") if p.suffix in {".js", ".html"}
    ]
    legacy_sources += [
        p
        for p in PROJECT_ROOT.glob("plugins/*/extensions/webui/**/*")
        if p.suffix in {".js", ".html"}
    ]
    next_sources = [
        p
        for p in FRONTEND.glob("**/*")
        if p.suffix in {".ts", ".tsx"} and "__tests__" not in p.parts
    ]

    legacy = _endpoints_called_in(legacy_sources, real)
    nxt = _endpoints_called_in(next_sources, real)
    gap = legacy - nxt - INTENTIONALLY_NOT_PORTED
    return legacy, nxt, gap


def test_the_legacy_interface_is_not_removed_before_the_replacement_covers_it():
    if LEGACY_UI.is_dir():
        # The port is in progress. Nothing to enforce yet.
        return

    _, _, gap = measure_gap()
    assert not gap, (
        "webui/ has been removed, but the new frontend never calls "
        f"{len(gap)} endpoint(s) that only it reached: {sorted(gap)}. "
        "Removing it deletes the only way to use those features."
    )


def test_the_new_frontend_can_satisfy_a_forced_password_change():
    """Not a parity nicety: `must_change_password` makes the backend refuse every
    other endpoint, so a frontend that reports the flag without offering a way to
    clear it strands the user completely."""
    sources = "\n".join(
        p.read_text(encoding="utf-8")
        for p in FRONTEND.glob("**/*.tsx")
        if "__tests__" not in p.parts
    )
    assert "change_password" in sources
    assert "ChangePasswordScreen" in sources


def test_the_gap_report_is_accurate_enough_to_act_on():
    """Guards the measurement itself: if the call-site patterns stop matching,
    the gap silently reads as zero and this test would wave through a removal
    that should not happen."""
    legacy, nxt, _ = measure_gap()
    if LEGACY_UI.is_dir():
        assert len(legacy) > 60, (
            "the inherited interface should reach dozens of endpoints; "
            "the detection patterns have probably stopped matching"
        )
    assert len(nxt) >= 10, (
        "the new frontend should reach at least its own screens' endpoints; "
        "the detection patterns have probably stopped matching"
    )
