"""The appliance's network list, read from the directory the Compose file mounts read-only.

Cached by (mtime, size) so a file the console rewrites is picked up on the next call. A
missing, unreadable or broken file is "nothing known", never an error: the executor then
behaves as it did before the list existed. The shape and the reasoning live in
`plugins/_ea_ansible/helpers/network_list.py`.
"""

from __future__ import annotations

import json
import os
from typing import Any

from plugins._ea_ansible.helpers import network_list

ENV_FILE = "REZZTOR_NETWORKS_FILE"
DEFAULT_FILE = "/etc/rezztor/networks/networks.json"

_cache: tuple[Any, dict[str, Any]] | None = None


def load() -> dict[str, Any]:
    global _cache
    path = os.environ.get(ENV_FILE) or DEFAULT_FILE
    try:
        stat = os.stat(path)
        key = (path, stat.st_mtime_ns, stat.st_size)
        if _cache and _cache[0] == key:
            return _cache[1]
        with open(path, encoding="utf-8") as handle:
            listing = network_list.parse(json.load(handle))
    except (OSError, ValueError):
        key, listing = None, network_list.parse(None)
    _cache = (key, listing)
    return listing
