"""A secret from the environment, or from a file the environment names.

`NAME` holds the value; `NAME_FILE` holds the path of a mounted secret (a Docker
secret under `/run/secrets/`), which keeps the value out of `docker inspect`, the
host's `.env` and the process environment. Both set is refused rather than
resolved: two sources that can disagree are how a rotated secret keeps being
ignored. The file is read at the moment of use, so a rotated secret is picked up.
"""

from __future__ import annotations

import os


class SecretConfigError(Exception):
    """Misconfigured. The message is written to be shown to an administrator."""


def get(name: str) -> str:
    """The value, stripped; empty when neither form is set."""
    value = (os.getenv(name) or "").strip()
    path = (os.getenv(f"{name}_FILE") or "").strip()
    if not path:
        return value
    if value:
        raise SecretConfigError(f"Both {name} and {name}_FILE are set. Use one.")
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError as error:
        raise SecretConfigError(f"{name}_FILE names a file that cannot be read: {error.strerror}.") from error
