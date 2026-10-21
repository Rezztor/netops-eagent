#!/usr/bin/env python3
"""Go through the closed console of a running test machine as a person would.

    support-access.py PORT CONSOLE_PASSWORD SUPPORT_PRIVATE_KEY.pem

Signs in over SSH as rezadmin (which must give the setup menu and no shell),
chooses "Support access", reads the challenge off the screen, signs it with the
throwaway support key of this test build and types the code. Passes when a
root shell opened and, after it was left, the menu is back. The code is made
here with openssl and not with scripts/sign_release.py, so that the format is
written down twice (ADR 0083). Run by test-iso.sh; standard library only.
"""

import base64
import os
import pty
import re
import select
import subprocess
import sys
import tempfile
import time


def main() -> int:
    port, password, key = sys.argv[1], sys.argv[2], sys.argv[3]
    with tempfile.TemporaryDirectory() as work:
        ask = os.path.join(work, "askpass.sh")
        with open(ask, "w") as handle:
            handle.write("#!/bin/sh\necho '%s'\n" % password.replace("'", "'\\''"))
        os.chmod(ask, 0o700)
        env = {**os.environ, "SSH_ASKPASS": ask, "SSH_ASKPASS_REQUIRE": "force", "TERM": "xterm", "DISPLAY": ":0"}
        ssh = ["ssh", "-tt", "-q", "-p", port, "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
               "-o", "PubkeyAuthentication=no", "rezadmin@127.0.0.1"]  # fmt: skip
        child, fd = pty.fork()
        if child == 0:
            os.execvpe("ssh", ssh, env)
        seen = ""

        def wait_for(pattern: str, seconds: float = 60) -> "re.Match[str]":
            nonlocal seen
            until = time.time() + seconds
            while time.time() < until:
                found = re.search(pattern, seen)
                if found:
                    seen = seen[found.end() :]
                    return found
                if select.select([fd], [], [], 1)[0]:
                    try:
                        seen += os.read(fd, 4096).decode("utf-8", "replace")
                    except OSError:
                        break
            raise SystemExit(f"FAIL support access: the console never showed /{pattern}/. Last seen: {seen[-300:]!r}")

        def send(text: str) -> None:
            os.write(fd, text.encode())
            time.sleep(0.5)

        wait_for(r"What do you want to change\?")
        send("\x1b[B" * 20)  # the last entry: a list stops at its end, and support access comes last
        send("\r")
        challenge = wait_for(r"Challenge\s+(RZ-[0-9A-F]{8}-[A-Z2-7]{8})").group(1)
        wait_for(r"Support code")
        expires = int(time.time()) + 3600
        body = os.path.join(work, "body")
        with open(body, "wb") as handle:
            handle.write(b"rezztor-support-1|" + challenge.encode() + b"|" + str(expires).encode())
        signature = subprocess.run(["openssl", "pkeyutl", "-sign", "-inkey", key, "-rawin", "-in", body],
                                   check=True, capture_output=True).stdout  # fmt: skip
        send(base64.b32encode(expires.to_bytes(4, "big") + signature).decode().rstrip("=") + "\r")
        wait_for(r"Code accepted")
        wait_for(r"A root shell follows")
        send("echo uid-$(id -u)-shell-$((6*7))\r")
        wait_for(r"uid-0-shell-42")
        send("exit\r")
        wait_for(r"Press Enter")
        send("\r")
        wait_for(r"What do you want to change\?")
        send("\x1b\x1b")  # leave the menu, which ends the session
        os.close(fd)
    print(f"support access: challenge {challenge}, a signed code opened a root shell, and the menu came back")
    return 0


if __name__ == "__main__":
    sys.exit(main())
