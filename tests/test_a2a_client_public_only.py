"""The A2A client reaches only public addresses: the model chooses the URL (audit 6, M2)."""

import pytest

from helpers import fasta2a_client
from helpers.network import UnsafeUrlError


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    ["127.0.0.1:5432", "http://localhost:8080", "http://10.0.0.5/a2a", "http://169.254.169.254/latest/meta-data",
     "http://[::1]:80", "http://user:pw@8.8.8.8", "ftp://8.8.8.8"],  # fmt: skip
)
async def test_a_non_public_agent_url_is_refused_before_any_request(monkeypatch, url):
    def never(*args, **kwargs):
        raise AssertionError("a connection was opened")

    monkeypatch.setattr(fasta2a_client, "AgentConnection", never)
    with pytest.raises(UnsafeUrlError):
        await fasta2a_client.connect_to_agent(url)


# --- the checked address is the one connected to ---------------------------------

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


@pytest.fixture
def local_agent():
    """An agent on loopback that records each request's Host header."""
    seen: list[tuple[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers.get("Host", "")))
            if self.path.startswith("/bounce"):
                self.send_response(302)
                self.send_header("Location", "http://169.254.169.254/.well-known/agent.json")
                self.end_headers()
                return
            body = json.dumps({"name": "pinned"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1], seen
    server.shutdown()


@pytest.mark.asyncio
async def test_every_request_goes_to_the_checked_address(local_agent, monkeypatch):
    """httpx resolved the name again on connect, so a zone answering a public
    address to the check and a private one to the connect got inside. The name
    here does not resolve at all: reaching the agent proves the connect used
    the address the check returned, while Host still names the agent."""
    port, seen = local_agent
    monkeypatch.setattr(
        fasta2a_client.network, "validate_public_http_url", lambda url: (__import__("ipaddress").ip_address("127.0.0.1"),)
    )

    conn = await fasta2a_client.connect_to_agent(f"http://agent.invalid:{port}")
    try:
        assert (await conn.get_agent_card())["name"] == "pinned"
        assert seen == [("/.well-known/agent.json", f"agent.invalid:{port}")]
        with pytest.raises(Exception, match="not the agent this connection checked"):
            await conn._http_client.get(f"http://elsewhere.invalid:{port}/")
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_a_redirect_is_not_followed(local_agent):
    port, seen = local_agent
    conn = fasta2a_client.AgentConnection(f"http://agent.invalid:{port}/bounce", address="127.0.0.1")
    try:
        with pytest.raises(RuntimeError):
            await conn.get_agent_card()
    finally:
        await conn.close()
    assert [path for path, _ in seen] == ["/bounce/.well-known/agent.json"]


# --- behind the customer's egress proxy ------------------------------------------


def _no_proxy_env(monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "no_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.asyncio
async def test_a_configured_proxy_carries_the_connection_and_resolves_the_name(local_agent, monkeypatch):
    """The pinned transport ignored HTTP(S)_PROXY, so A2A broke wherever the
    customer has only an egress proxy. The proxy (here the local server, which
    sees the absolute URL) gets every request; the name is never resolved here."""
    port, seen = local_agent
    _no_proxy_env(monkeypatch)
    monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{port}")

    def never(url):
        raise AssertionError("resolved locally")

    monkeypatch.setattr(fasta2a_client.network, "validate_public_http_url", never)
    conn = await fasta2a_client.connect_to_agent("http://agent.invalid:9/")
    try:
        assert seen == [("http://agent.invalid:9/.well-known/agent.json", "agent.invalid:9")]
        with pytest.raises(Exception, match="not the agent this connection checked"):
            await conn._http_client.get("http://elsewhere.invalid:9/")
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["http://10.0.0.5/a2a", "http://169.254.169.254/", "http://localhost/", "http://[::1]/"])
async def test_a_proxy_does_not_open_a_non_public_address(monkeypatch, url):
    _no_proxy_env(monkeypatch)
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setattr(fasta2a_client, "AgentConnection", lambda *a, **k: (_ for _ in ()).throw(AssertionError("opened")))
    with pytest.raises(UnsafeUrlError):
        await fasta2a_client.connect_to_agent(url)


@pytest.mark.asyncio
async def test_no_proxy_keeps_the_pinned_connection(local_agent, monkeypatch):
    port, seen = local_agent
    _no_proxy_env(monkeypatch)
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setenv("NO_PROXY", "agent.invalid")
    monkeypatch.setattr(
        fasta2a_client.network, "validate_public_http_url", lambda url: (__import__("ipaddress").ip_address("127.0.0.1"),)
    )
    conn = await fasta2a_client.connect_to_agent(f"http://agent.invalid:{port}")
    try:
        assert seen == [("/.well-known/agent.json", f"agent.invalid:{port}")]
    finally:
        await conn.close()
