"""How many SolarWinds calls one conversation may make in a minute.

There is no "who may reach what" question here the way `_ea_mail` has one --
every account that can use this tool at all reaches the one instance an
administrator configured, and the query templates in `capabilities.py` are
already the access-control boundary. What still needs bounding is a
conversation that has gone wrong: a loop, or an instruction the agent read
somewhere and took seriously, hammering a piece of shared monitoring
infrastructure other tools and people depend on.

Recorded before the call, not after, so a query that hangs and times out still
counts -- the same reasoning `_ea_mail/helpers/policy.py`'s per-sender cap
uses.
"""

from __future__ import annotations

import json
import os
import threading
import time

from helpers import files

from plugins._ea_solarwinds.helpers import config as plugin_config

_LEDGER = "usr/solarwinds_calls.json"
_WINDOW_SECONDS = 60
_LOCK = threading.RLock()


class RateLimited(Exception):
    pass


def _ledger_path() -> str:
    return files.get_abs_path(_LEDGER)


def _read_ledger() -> dict[str, list[float]]:
    try:
        with open(_ledger_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_ledger(data: dict[str, list[float]]) -> None:
    path = _ledger_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    os.replace(tmp, path)


def check_and_record(context_id: str) -> None:
    """Count this call against the conversation's minute, or refuse it."""
    limit = plugin_config.get_config()["max_calls_per_minute"]
    now = time.time()
    cutoff = now - _WINDOW_SECONDS

    with _LOCK:
        ledger = _read_ledger()
        recent = [
            stamp
            for stamp in ledger.get(context_id, [])
            if isinstance(stamp, (int, float)) and stamp > cutoff
        ]
        if len(recent) >= limit:
            raise RateLimited(
                f"This conversation has already made {limit} SolarWinds calls in "
                "the last minute, which is the limit. Try again shortly."
            )
        recent.append(now)
        ledger[context_id] = recent
        ledger = {key: kept for key, value in ledger.items() if (kept := [s for s in value if s > cutoff])}
        _write_ledger(ledger)
