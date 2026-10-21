"""Static contract tests for the Access (user management) settings UI.

These assert the wiring between markup, store and API rather than rendering the
page, matching the convention used by the other *_static tests in this suite.
"""

import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

ACCESS = PROJECT_ROOT / "webui" / "components" / "settings" / "access"


def read(*parts) -> str:
    return (PROJECT_ROOT.joinpath(*parts)).read_text(encoding="utf-8")


# --- wiring -----------------------------------------------------------------


# --- store behaviour --------------------------------------------------------


# --- guard rails mirrored from the server -----------------------------------


# --- self-service account surfaces ------------------------------------------


def test_interstitial_is_not_the_enforcement_point():
    """The server refuses the endpoints; the overlay only explains why."""
    api_source = read("helpers", "api.py")
    assert "requires_password_change_complete" in api_source
    assert "PASSWORD_CHANGE_EXEMPT_ENDPOINTS" in api_source


