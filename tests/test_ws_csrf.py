import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers.ws import validate_ws_origin


def test_validate_ws_origin_allows_same_origin_with_explicit_port():
    ok, reason = validate_ws_origin(
        {
            "HTTP_ORIGIN": "http://localhost:5000",
            "HTTP_HOST": "localhost:5000",
        }
    )
    assert ok is True
    assert reason is None


def test_validate_ws_origin_allows_default_https_port_without_explicit_port():
    ok, reason = validate_ws_origin(
        {
            "HTTP_ORIGIN": "https://example.com",
            "HTTP_HOST": "example.com",
        }
    )
    assert ok is True
    assert reason is None


def test_validate_ws_origin_rejects_missing_origin():
    ok, reason = validate_ws_origin(
        {
            "HTTP_HOST": "localhost:5000",
        }
    )
    assert ok is False
    assert reason == "missing_origin"


def test_validate_ws_origin_rejects_cross_origin():
    ok, reason = validate_ws_origin(
        {
            "HTTP_ORIGIN": "http://evil.test",
            "HTTP_HOST": "localhost:5000",
        }
    )
    assert ok is False
    assert reason == "origin_host_mismatch"


def test_validate_ws_origin_rejects_foreign_origin_even_when_it_looks_like_a_tunnel():
    """Remote Control (the serveo/cloudflare tunnel) has been removed, and with
    it the rule that widened the WebSocket origin allow-list to whatever URL a
    live third-party tunnel reported. A foreign origin is now always rejected.
    """
    ok, reason = validate_ws_origin(
        {
            "HTTP_ORIGIN": "https://rezztor-eagent.tailabc.ts.net",
            "HTTP_HOST": "127.0.0.1:80",
        }
    )

    assert ok is False
    assert reason == "origin_host_mismatch"


def test_validate_ws_origin_rejects_unrelated_origin():
    ok, reason = validate_ws_origin(
        {
            "HTTP_ORIGIN": "https://evil.example",
            "HTTP_HOST": "127.0.0.1:80",
        }
    )

    assert ok is False
    assert reason == "origin_host_mismatch"
