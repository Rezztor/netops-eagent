"""`fetch_public_http_resource` connects to the address it validated.

The check resolved the name and `requests` resolved it again, so a zone that
answers a public address first and a private one next (TTL 0) passed the check
and the GET landed on the private address.
"""

from __future__ import annotations

import http.server
import ipaddress
import socket
import threading

import pytest

from helpers import network


@pytest.fixture
def local_server():
    seen: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.headers["Host"])
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server.server_address[1], seen
    finally:
        server.shutdown()
        server.server_close()


def test_the_get_goes_to_the_validated_address_not_a_second_lookup(monkeypatch, local_server):
    port, seen = local_server
    public = "93.184.216.34"
    monkeypatch.setattr(
        network, "resolve_host_ips", lambda _host: (ipaddress.ip_address(public),)
    )

    looked_up: list[str] = []
    real_getaddrinfo = socket.getaddrinfo

    def getaddrinfo(host, *args, **kwargs):
        # The rebinding zone: whatever is asked, the answer is now loopback.
        looked_up.append(host)
        return real_getaddrinfo("127.0.0.1", *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)

    result = network.fetch_public_http_resource(
        f"http://rebind.example:{port}/x", max_bytes=1024
    )

    assert result.content == b"ok"
    assert looked_up == [public], "the transport resolved the hostname a second time"
    assert seen == [f"rebind.example:{port}"], "the Host header must keep the name"
