import asyncio
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from extensions.python.banners import _10_unsecured_connection as unsecured_connection


def _run(hostname: str, protocol: str) -> list[dict]:
    banners: list[dict] = []
    asyncio.run(
        unsecured_connection.UnsecuredConnectionCheck(agent=None).execute(
            banners=banners,
            frontend_context={"hostname": hostname, "protocol": protocol},
        )
    )
    return banners


def _ids(banners: list[dict]) -> set[str]:
    return {banner.get("id") for banner in banners}


def test_warns_when_credentials_would_cross_the_network_in_clear_text():
    banners = _run("eagent.example.com", "http:")
    assert "credentials-unencrypted" in _ids(banners)

    row = next(b for b in banners if b["id"] == "credentials-unencrypted")
    assert row["type"] == "warning"
    assert "clear text" in row["html"]
    # Banner HTML is injected into the page; it must not carry inline handlers.
    assert "onclick" not in row["html"]


def test_no_warning_over_https():
    assert _ids(_run("eagent.example.com", "https:")) == set()


def test_no_warning_from_local_or_private_addresses():
    for hostname in ("localhost", "127.0.0.1", "192.168.1.10", "10.0.0.5", "172.16.0.9", "box.local"):
        assert _ids(_run(hostname, "http:")) == set(), hostname


def test_missing_credentials_banner_is_gone():
    """Authentication is mandatory now, so there is no unauthenticated state to
    warn about; the old banner pointed at settings fields that no longer exist."""
    banners = _run("eagent.example.com", "http:")
    assert "unsecured-connection" not in _ids(banners)
    assert all("section-auth" not in b.get("html", "") for b in banners)
