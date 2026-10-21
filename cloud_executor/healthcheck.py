"""Container health check: GET /healthz over the executor's own socket. Exit 0 when it answers 200."""

import http.client
import os
import socket
import sys


class _Unix(http.client.HTTPConnection):
    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.settimeout(5)
        self.sock.connect(os.getenv("REZZTOR_CLOUD_SOCKET") or "/run/cloud-socket/cloud.sock")


def main() -> None:
    try:
        conn = _Unix("localhost")
        conn.request("GET", "/healthz")
        sys.exit(0 if conn.getresponse().status == 200 else 1)
    except OSError:
        sys.exit(1)


if __name__ == "__main__":
    main()
