"""Turn a failed job's output into a code and a one-line cause.

The agent used to see a raw Ansible dump and guess ("credentials?"). A fixed
list of patterns, checked in order, is a deliberately small classifier: the
first match wins, so the specific causes sit above the general ones.
"""

from __future__ import annotations

import re

#: (code, pattern, cause). Order matters: `Permission denied` inside an
#: UNREACHABLE message is a credential problem, not a connection one.
_RULES: tuple[tuple[str, str, str], ...] = (
    # Found on IOS 12.4 in the test lab of 2026-10-11: the job ended in a second and the cause was "unknown".
    ("ssh_algorithms", r"no match for method|no matching (?:key exchange|host key|cipher|mac)|kex error",
     "The device and the executor share no SSH algorithm: the device's software offers only older ones "
     "(SHA-1 key exchange, ssh-rsa, CBC ciphers). Tag the device legacy-ssh in Inventory to reach it with "
     "those, or upgrade its software."),
    ("platform_mismatch", r"unable to open (?:\w+ )?cli|unable to enter configuration mode",
     "The device does not speak the CLI dialect its platform setting expects "
     "(for example community SONiC set up as Dell Enterprise SONiC). Check the device's platform in Inventory."),
    ("credential_failed", r"authentication failed|permission denied|invalid (?:user|password|credentials)|login incorrect|access denied",
     "The device rejected the stored credential."),
    ("host_unreachable", r"no route to host|network is unreachable|connection refused|name or service not known|connection timed out",
     "The executor cannot reach the device on its management address. Request the read again: the pre-check names the network and says why."),
    ("timeout", r"timed out|timeout",
     "The device or the job took too long to answer."),
    ("command_rejected", r"invalid input|unknown command|command not found|% ambiguous|syntax error|incomplete command",
     "The device refused one of the commands."),
    ("connection_failed", r"unable to open shell|failed to connect|connection (?:closed|reset)|\bunreachable\b|ssh: ",
     "The executor could not open a session to the device."),
)


def classify(output: str) -> dict[str, str]:
    text = str(output or "")
    for code, pattern, cause in _RULES:
        if re.search(pattern, text, re.IGNORECASE):
            return {"code": code, "cause": cause}
    last = next((ln.strip() for ln in reversed(text.splitlines()) if ln.strip()), "")
    return {"code": "unknown", "cause": last[:200] or "The job failed without saying why."}
