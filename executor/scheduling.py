"""Who runs now, who waits, and who is turned away.

The executor used to hold a single lock and return 429 to anything that arrived
while a job was running. That was a defensible answer while the agent was the
only caller and could simply retry: network change is not a throughput problem,
and the comment saying so is still true.

It stops being a defensible answer once a person is holding the other end. An
engineer who clicks "run" and is told to try again shortly, twice, has learned
that the button is unreliable -- and the cure is not more concurrency, it is a
queue one job deep enough to absorb the gap between two people pressing things.

So: still one job at a time by default, but a second arrival waits its turn
instead of being refused. Refusal is kept for the two cases where waiting would
be the wrong answer -- a queue so long the wait is meaningless, and a caller
submitting faster than any person could.

The rate limit lives here rather than in the application because a limit only the
application enforces is not a limit. The console and the shell wrapper reach the
same executor.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Any, Callable

from plugins._ea_ansible.helpers import job_schema

ENV_MAX_CONCURRENT = "REZZTOR_ANSIBLE_MAX_CONCURRENT_JOBS"
ENV_MAX_QUEUED = "REZZTOR_ANSIBLE_MAX_QUEUED_JOBS"
ENV_MAX_PER_MINUTE = "REZZTOR_ANSIBLE_MAX_JOBS_PER_MINUTE"

DEFAULT_MAX_CONCURRENT = 1
DEFAULT_MAX_QUEUED = 20
DEFAULT_MAX_PER_MINUTE = 10

RATE_WINDOW_SECONDS = 60.0

#: Re-exported from the shared contract so this module reads completely, and
#: so nothing here is tempted to define a second answer to "is it done".
TERMINAL_STATUSES = job_schema.TERMINAL_STATUSES


class Refused(Exception):
    """Turned away rather than queued. The message is shown to a person."""


def _int_env(name: str, default: int) -> int:
    try:
        value = int(str(os.getenv(name) or default))
    except ValueError:
        return default
    return value if value > 0 else default


def limits() -> dict[str, int]:
    """What this executor is actually enforcing, for /healthz to report.

    A configuration screen that shows a number nobody enforces is worse than a
    screen that shows nothing, so the number on the screen comes from here.
    """
    return {
        "max_concurrent_jobs": _int_env(ENV_MAX_CONCURRENT, DEFAULT_MAX_CONCURRENT),
        "max_queued_jobs": _int_env(ENV_MAX_QUEUED, DEFAULT_MAX_QUEUED),
        "max_jobs_per_minute": _int_env(ENV_MAX_PER_MINUTE, DEFAULT_MAX_PER_MINUTE),
    }


class Scheduler:
    """One lock, a count of what is running, and a queue of what is not.

    Deliberately not a thread pool. A pool sized at one is a queue with extra
    machinery, and the interesting behaviour here is the refusals.
    """

    def __init__(self, runner: Callable[..., None]) -> None:
        self._runner = runner
        self._lock = threading.Lock()
        self._running = 0
        self._queued: deque[tuple[str, tuple[Any, ...]]] = deque()
        self._submissions: deque[float] = deque()

    # --- admission -----------------------------------------------------------

    def submit(self, job_id: str, args: tuple[Any, ...]) -> str:
        """Start it or queue it. Returns the status to record.

        Raises `Refused` when neither is appropriate.
        """
        caps = limits()
        with self._lock:
            self._trim_window()
            if len(self._submissions) >= caps["max_jobs_per_minute"]:
                raise Refused(
                    f"More than {caps['max_jobs_per_minute']} jobs were submitted in the "
                    "last minute. Nothing has been queued; try again shortly."
                )

            if self._running < caps["max_concurrent_jobs"]:
                self._submissions.append(time.monotonic())
                self._running += 1
                self._start(job_id, args)
                return "running"

            if len(self._queued) >= caps["max_queued_jobs"]:
                raise Refused(
                    f"The executor is busy and {len(self._queued)} jobs are already "
                    "waiting. Nothing has been queued."
                )

            self._submissions.append(time.monotonic())
            self._queued.append((job_id, args))
            return "queued"

    def _trim_window(self) -> None:
        cutoff = time.monotonic() - RATE_WINDOW_SECONDS
        while self._submissions and self._submissions[0] < cutoff:
            self._submissions.popleft()

    # --- completion ----------------------------------------------------------

    def finished(self) -> str | None:
        """Called by a job thread as it exits. Starts the next one, if any.

        Returns the job id it started, for a caller that wants to log it. The
        started job moves itself out of `queued`: the store is not this module's
        business, and a scheduler that wrote to it would be two things.
        """
        with self._lock:
            self._running -= 1
            if not self._queued:
                return None
            caps = limits()
            if self._running >= caps["max_concurrent_jobs"]:
                return None
            job_id, args = self._queued.popleft()
            self._running += 1
            self._start(job_id, args)
            return job_id

    def _start(self, job_id: str, args: tuple[Any, ...]) -> None:
        """Caller holds the lock. The thread must not touch it before returning."""
        threading.Thread(
            target=self._runner,
            args=(job_id, *args),
            daemon=True,
            name=f"ansible-job-{job_id[:8]}",
        ).start()

    # --- reporting -----------------------------------------------------------

    def busy(self) -> bool:
        with self._lock:
            return self._running > 0

    def depth(self) -> dict[str, int]:
        with self._lock:
            return {"running": self._running, "queued": len(self._queued)}

    def reset_for_tests(self) -> None:
        """Named for what it is. Production code never calls it."""
        with self._lock:
            self._running = 0
            self._queued.clear()
            self._submissions.clear()
