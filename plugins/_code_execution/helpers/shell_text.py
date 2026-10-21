"""Terminal output cleanup and shell setup, shared by every backend.

Extracted from `shell_ssh.py` when paramiko was dropped. Nothing here is
transport-specific -- `shell_local` (a local TTY) and `shell_openssh` (an
OpenSSH subprocess) both need exactly the same treatment of what a shell
sends back, because the mess they are cleaning up comes from the shell,
not from how the bytes arrived.
"""

import re

# Injected into every new shell to keep it safe for non-interactive use.
# Pagers (more/less) would otherwise block forever waiting for input that
# never arrives and spin at 100% CPU; see issue #1697.
PAGER_DISABLE_COMMAND = "export GIT_PAGER=cat; export PAGER=cat"


def clean_string(input_string):
    # Remove ANSI escape codes
    ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    cleaned = ansi_escape.sub("", input_string)

    # remove null bytes
    cleaned = cleaned.replace("\x00", "")

    # remove ipython \r\r\n> sequences from the start
    cleaned = re.sub(r'^[ \r]*(?:\r*\n>[ \r]*)*', '', cleaned)
    # also remove any amount of '> ' sequences from the start
    cleaned = re.sub(r'^(>\s*)+', '', cleaned)

    # Replace '\r\n' with '\n'
    cleaned = cleaned.replace("\r\n", "\n")

    # remove leading \r and spaces
    cleaned = cleaned.lstrip("\r ")

    # Split the string by newline characters to process each segment separately
    lines = cleaned.split("\n")

    for i in range(len(lines)):
        # Handle carriage returns '\r' by splitting and taking the last part
        parts = [part for part in lines[i].split("\r") if part.strip()]
        if parts:
            lines[i] = parts[
                -1
            ].rstrip()  # Overwrite with the last part after the last '\r'

    return "\n".join(lines)
