from collections import deque
import asyncio
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
import faulthandler
import os
import re
import sys
import threading
import logging
import time
from typing import Callable, Iterator
import urllib.request

import uvicorn

from helpers import process
from helpers.print_style import PrintStyle


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    try:
        return max(minimum, float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class StartupConfig:
    timeout_seconds: int
    max_attempts: int
    retry_delay_seconds: float

    @classmethod
    def from_env(cls) -> "StartupConfig":
        return cls(
            timeout_seconds=_env_int("REZZTOR_STARTUP_TIMEOUT_SECONDS", 90, minimum=15),
            max_attempts=_env_int("REZZTOR_STARTUP_MAX_ATTEMPTS", 2, minimum=1),
            retry_delay_seconds=_env_float(
                "REZZTOR_STARTUP_RETRY_DELAY_SECONDS", 2.0, minimum=0.0
            ),
        )


@dataclass
class StartupStageRecord:
    name: str
    timestamp: float
    detail: str | None = None


class StartupMonitor:
    def __init__(
        self,
        bind_host: str,
        probe_host: str,
        port: int,
        attempt: int,
        max_attempts: int,
        timeout_seconds: int,
    ) -> None:
        self.bind_host = bind_host
        self.probe_host = probe_host
        self.port = port
        self.attempt = attempt
        self.max_attempts = max_attempts
        self.timeout_seconds = timeout_seconds
        self.start_time = time.monotonic()
        self._stage = "created"
        self._stage_detail: str | None = None
        self._stage_started_at = self.start_time
        self._history: deque[StartupStageRecord] = deque(maxlen=30)
        self._history.append(StartupStageRecord(self._stage, self.start_time))
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._server: uvicorn.Server | None = None
        self._watchdog_thread: threading.Thread | None = None

    def _prefix(self) -> str:
        return f"[startup attempt {self.attempt}/{self.max_attempts}]"

    def mark(self, stage: str, detail: str | None = None) -> None:
        now = time.monotonic()
        with self._lock:
            self._stage = stage
            self._stage_detail = detail
            self._stage_started_at = now
            self._history.append(StartupStageRecord(stage, now, detail))
            elapsed = now - self.start_time

        suffix = f" ({detail})" if detail else ""
        # Not PrintStyle.debug: that returns early unless runtime.is_development(),
        # which is `not is_dockerized()` -- so the entire stage timeline was
        # suppressed in every container this ships to. A timeout still dumped the
        # history, so a hang was well instrumented and a slow start that somebody
        # was watching live said nothing at all. That is backwards: the timeline
        # is most useful before the thing fails.
        PrintStyle().print(f"{self._prefix()} {stage}{suffix} at +{elapsed:.1f}s")

    @contextmanager
    def stage(self, stage: str, detail: str | None = None) -> Iterator[None]:
        self.mark(f"{stage}.start", detail)
        try:
            yield
        except BaseException as e:
            message = f"{type(e).__name__}: {e}"
            self.mark(f"{stage}.error", message[:200])
            raise
        else:
            self.mark(f"{stage}.done", detail)

    def lifespan(self):
        @asynccontextmanager
        async def _lifespan(_app):
            self.mark("starlette.lifespan.startup")
            try:
                yield
            finally:
                self.mark("starlette.lifespan.shutdown")

        return _lifespan

    def attach_server(self, server: uvicorn.Server) -> None:
        with self._lock:
            self._server = server

    def start_watchdog(self) -> None:
        if self._watchdog_thread and self._watchdog_thread.is_alive():
            return
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            daemon=True,
            name=f"StartupWatchdog-{self.attempt}",
        )
        self._watchdog_thread.start()

    def mark_ready(self, source: str = "health_check") -> None:
        if self._ready.is_set():
            return
        self.mark("ready", source)
        self._ready.set()
        self._stop.set()

    def is_ready(self) -> bool:
        return self._ready.is_set()

    def close(self) -> None:
        self._stop.set()

    def stop_event(self) -> threading.Event:
        return self._stop

    def _watchdog_loop(self) -> None:
        next_progress_log = self.start_time + 10
        while not self._stop.wait(timeout=1):
            if self._ready.is_set():
                return

            now = time.monotonic()
            if now >= next_progress_log:
                stage, detail, stage_elapsed, total_elapsed, _history = self.snapshot()
                detail_text = f" ({detail})" if detail else ""
                PrintStyle.warning(
                    f"{self._prefix()} still waiting for readiness after "
                    f"{total_elapsed:.1f}s; current stage '{stage}' has been active "
                    f"for {stage_elapsed:.1f}s{detail_text}"
                )
                next_progress_log = now + 10

            if now - self.start_time >= self.timeout_seconds:
                self._handle_timeout()
                return

    def snapshot(
        self,
    ) -> tuple[str, str | None, float, float, list[StartupStageRecord]]:
        now = time.monotonic()
        with self._lock:
            return (
                self._stage,
                self._stage_detail,
                now - self._stage_started_at,
                now - self.start_time,
                list(self._history),
            )

    def _handle_timeout(self) -> None:
        stage, detail, stage_elapsed, total_elapsed, history = self.snapshot()
        detail_text = f" ({detail})" if detail else ""
        PrintStyle.error(
            f"{self._prefix()} startup timed out after {total_elapsed:.1f}s while "
            f"waiting for bind={self.bind_host}:{self.port} "
            f"probe=http://{self.probe_host}:{self.port}/api/health; current stage "
            f"'{stage}' has been active for {stage_elapsed:.1f}s{detail_text}"
        )

        PrintStyle.error(f"{self._prefix()} recent stage history follows:")
        for record in history:
            relative = record.timestamp - self.start_time
            suffix = f" ({record.detail})" if record.detail else ""
            PrintStyle.standard(f"  +{relative:5.1f}s {record.name}{suffix}")

        active_threads = ", ".join(
            f"{thread.name}(alive={thread.is_alive()}, daemon={thread.daemon})"
            for thread in threading.enumerate()
        )
        PrintStyle.error(f"{self._prefix()} active threads: {active_threads}")
        PrintStyle.error(
            f"{self._prefix()} dumping all thread stack traces for startup diagnosis"
        )
        try:
            faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
        except Exception as e:
            PrintStyle.error(f"{self._prefix()} failed to dump thread traces: {e}")

        with self._lock:
            server = self._server

        if server is not None:
            PrintStyle.warning(
                f"{self._prefix()} requesting uvicorn shutdown after startup timeout"
            )
            server.should_exit = True

        if not self._stop.wait(timeout=3):
            PrintStyle.error(
                f"{self._prefix()} forcing process exit so the supervisor can restart it"
            )
            os._exit(1)


def get_health_probe_host(bind_host: str) -> str:
    if bind_host in {"0.0.0.0", "::", "[::]", ""}:
        return "127.0.0.1"
    return bind_host


_TOKEN_SEGMENT = re.compile(r"(/(?:mcp|a2a)/t-)[^/?\s]+")


class _StripQueryStrings(logging.Filter):
    """Keep the query string out of uvicorn's access log.

    Access logs answer "which endpoint, what status, how often", and the query
    string is not part of that answer. It is, however, where this application's
    sensitive material travels: `/reset?token=...` carries a live
    password-reset token, and the file endpoints carry a user's own paths. ADR
    0005 puts stdout on the wire to a SIEM, so switching access logs on without
    this would write single-use credentials into the one log that is deliberately
    kept and forwarded -- the same mistake as printing a task token, arriving by
    a different route.

    Uvicorn formats the request line as `"GET /path?query HTTP/1.1"` in the third
    positional argument of its access record, which is what this rewrites.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            path = args[2]
            # The MCP and A2A endpoints carry the instance token in the path
            # (/mcp/t-<token>/sse), so stripping the query alone wrote it to
            # the access log on every call.
            path = _TOKEN_SEGMENT.sub(r"\1<redacted>", path)
            if "?" in path:
                head, _, _ = path.partition("?")
                path = f"{head}?<redacted>"
            if path != args[2]:
                record.args = args[:2] + (path,) + args[3:]
        return True


def install_access_log_redaction() -> None:
    """Attach the filter to uvicorn's access logger, once.

    Installed whether or not access logs are currently on: the setting is a
    toggle in the interface, and an operator who turns it on at three in the
    morning must not have to restart the process to get the redaction with it.
    """
    logger = logging.getLogger("uvicorn.access")
    if any(isinstance(existing, _StripQueryStrings) for existing in logger.filters):
        return
    logger.addFilter(_StripQueryStrings())


def run_uvicorn_with_retries(
    *,
    host: str,
    port: int,
    build_asgi_app: Callable[[StartupMonitor], object],
    flush_callback: Callable[[str], None],
    access_log: bool = False,
    log_level: str = "info",
    ws: str = "wsproto",
    startup_config: StartupConfig | None = None,
) -> None:
    startup_config = startup_config or StartupConfig.from_env()
    health_host = get_health_probe_host(host)
    # Unconditionally: access logs are a toggle in the interface, and turning
    # them on must not depend on having restarted the process since.
    install_access_log_redaction()
    # Same reasoning as mark() above: what it binds to and what it probes is the
    # first thing anyone asks when a container comes up and nothing answers.
    PrintStyle().print(
        f"[startup] bind={host}:{port} probe=http://{health_host}:{port}/api/health "
        f"timeout={startup_config.timeout_seconds}s attempts={startup_config.max_attempts}"
    )

    for attempt in range(1, startup_config.max_attempts + 1):
        startup_monitor = StartupMonitor(
            bind_host=host,
            probe_host=health_host,
            port=port,
            attempt=attempt,
            max_attempts=startup_config.max_attempts,
            timeout_seconds=startup_config.timeout_seconds,
        )
        try:
            if _run_server_attempt(
                host=host,
                health_host=health_host,
                port=port,
                startup_monitor=startup_monitor,
                build_asgi_app=build_asgi_app,
                flush_callback=flush_callback,
                access_log=access_log,
                log_level=log_level,
                ws=ws,
            ):
                return
        except BaseException as e:
            if isinstance(e, SystemExit) and startup_monitor.is_ready():
                raise

            PrintStyle.error(
                f"[startup attempt {attempt}/{startup_config.max_attempts}] "
                f"server startup failed before readiness with "
                f"{type(e).__name__}: {e}"
            )
            if attempt >= startup_config.max_attempts:
                raise
        else:
            if attempt >= startup_config.max_attempts:
                raise RuntimeError(
                    "Uvicorn exited before readiness on the final startup attempt."
                )

            PrintStyle.warning(
                f"[startup attempt {attempt}/{startup_config.max_attempts}] "
                "server exited before readiness; retrying"
            )

        if startup_config.retry_delay_seconds > 0:
            PrintStyle.warning(
                f"[startup attempt {attempt}/{startup_config.max_attempts}] "
                f"sleeping {startup_config.retry_delay_seconds:.1f}s before retry"
            )
            time.sleep(startup_config.retry_delay_seconds)

    raise RuntimeError("Server failed to reach readiness after all startup attempts.")


def _run_server_attempt(
    *,
    host: str,
    health_host: str,
    port: int,
    startup_monitor: StartupMonitor,
    build_asgi_app: Callable[[StartupMonitor], object],
    flush_callback: Callable[[str], None],
    access_log: bool,
    log_level: str,
    ws: str,
) -> bool:
    startup_monitor.start_watchdog()
    try:
        asgi_app = build_asgi_app(startup_monitor)

        with startup_monitor.stage("uvicorn.config.create"):
            config = uvicorn.Config(
                asgi_app,
                host=host,
                port=port,
                log_level=log_level,
                access_log=access_log,
                ws=ws,
            )

        with startup_monitor.stage("uvicorn.server.create"):
            server = uvicorn.Server(config)

        startup_monitor.attach_server(server)
        process.set_server(_UvicornServerWrapper(server, flush_callback))

        startup_monitor.mark("health.thread.start")
        threading.Thread(
            target=wait_for_health,
            args=(health_host, port, startup_monitor),
            daemon=True,
            name=f"StartupHealth-{startup_monitor.attempt}",
        ).start()

        PrintStyle().debug(f"Starting server at http://{host}:{port} ...")
        startup_monitor.mark("uvicorn.run.enter")
        _serve_uvicorn(server)

        if startup_monitor.is_ready():
            return True

        PrintStyle.warning(
            f"[startup attempt {startup_monitor.attempt}/{startup_monitor.max_attempts}] "
            "uvicorn exited before the health probe observed readiness"
        )
        return False
    finally:
        startup_monitor.close()
        process.set_server(None)
        flush_callback("server_exit")


def wait_for_health(host: str, port: int, startup_monitor: StartupMonitor) -> None:
    url = f"http://{host}:{port}/api/health"
    while not startup_monitor.stop_event().is_set():
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    startup_monitor.mark_ready("health_probe")
                    PrintStyle().print("Rezztor EAgent is running.")
                    return
        except Exception:
            pass
        startup_monitor.stop_event().wait(1)


class _UvicornServerWrapper:
    def __init__(
        self, server: uvicorn.Server, flush_callback: Callable[[str], None]
    ) -> None:
        self._server = server
        self._flush_callback = flush_callback

    def shutdown(self) -> None:
        self._flush_callback("shutdown")
        self._server.should_exit = True


def _serve_uvicorn(server: uvicorn.Server) -> None:
    # Avoid uvicorn.Server.run(), which delegates to asyncio.run(...) and can
    # conflict with the global nest_asyncio patch used by the runtime.
    # The project requires uvicorn>=0.38.0, where loop setup is exposed via
    # Config.get_loop_factory().
    loop_factory = server.config.get_loop_factory()
    with asyncio.Runner(loop_factory=loop_factory) as runner:
        runner.run(server.serve())
