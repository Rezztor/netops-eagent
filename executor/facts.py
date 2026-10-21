"""What this image actually contains.

`/healthz` reported the catalogue commit and nothing else about the image, which
is enough for a readiness probe and not enough for a person. "Is `cisco.ios`
11.5.1 in there, or did the last rebuild move it?" is a question the console has
to be able to answer without anyone opening a shell.

Read off the filesystem rather than by running `ansible-galaxy collection list`.
The API deliberately shells out to nothing, and a collection's version is sitting
in its `MANIFEST.json` anyway -- running a program to read a file it also reads is
a subprocess for no information.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache

#: Where the Dockerfile installs collections. Ansible's own variable, so an
#: image that moves them moves this too without a second place to remember.
ENV_COLLECTIONS_PATH = "ANSIBLE_COLLECTIONS_PATH"
DEFAULT_COLLECTIONS_PATH = "/usr/share/ansible/collections"


@lru_cache(maxsize=1)
def installed_collections() -> dict[str, str]:
    """`namespace.name` -> version, for every collection in the image.

    Cached: the image is immutable, so a rebuild is the only thing that can
    change this and a rebuild is a new process.
    """
    root = os.getenv(ENV_COLLECTIONS_PATH) or DEFAULT_COLLECTIONS_PATH
    base = os.path.join(root, "ansible_collections")
    found: dict[str, str] = {}
    try:
        namespaces = sorted(os.listdir(base))
    except OSError:
        return found

    for namespace in namespaces:
        namespace_dir = os.path.join(base, namespace)
        try:
            names = sorted(os.listdir(namespace_dir))
        except OSError:
            continue
        for name in names:
            manifest = os.path.join(namespace_dir, name, "MANIFEST.json")
            try:
                with open(manifest, encoding="utf-8") as handle:
                    info = json.load(handle).get("collection_info") or {}
            except (OSError, ValueError):
                continue
            found[f"{namespace}.{name}"] = str(info.get("version") or "")
    return found


@lru_cache(maxsize=1)
def ansible_version() -> str:
    """Empty when Ansible is not installed, which is the case in the
    application's test suite and must not be an error there."""
    try:
        import ansible  # noqa: PLC0415 - optional, see docstring

        return str(getattr(ansible, "__version__", "") or "")
    except Exception:  # noqa: BLE001 - a missing Ansible is a fact, not a fault
        return ""
