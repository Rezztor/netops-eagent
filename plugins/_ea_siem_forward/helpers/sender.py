"""The queue, the thread, and the socket.

The contract this module exists to keep is inherited from `audit.emit()` and
stated in ADR 0005: auditing must never break the operation it audits. Sending
the audit somewhere must not either. So:

- The sink callback formats a line and puts it on a bounded queue. It never
  touches a socket, resolves a name, or blocks.
- One background thread owns the socket and does the sending.
- A full queue drops the OLDEST line and counts it. A stalled or unreachable
  destination therefore costs bounded memory and *visible* loss, rather than
  unbounded growth or a blocked audit write.
- Every layer swallows its own exceptions.

UDP has no delivery acknowledgement, so nothing here can promise the events
arrived -- only that they were sent, and that failing to send them harmed
nothing else. The Settings card says as much to the operator.
"""

from __future__ import annotations

import queue
import socket
import threading
from typing import Any

from plugins._ea_siem_forward.helpers import cef
from plugins._ea_siem_forward.helpers import config as plugin_config

_lock = threading.Lock()
_queue: queue.Queue[str] | None = None
_thread: threading.Thread | None = None
_registered = False

# Counters, surfaced in the Settings card. Silent loss is the failure mode this
# design accepts; unreported silent loss is not.
_stats = {"sent": 0, "dropped_queue_full": 0, "send_errors": 0}


def stats() -> dict[str, int]:
    with _lock:
        return dict(_stats)


def reset_stats() -> None:
    with _lock:
        for key in _stats:
            _stats[key] = 0


def _hostname() -> str:
    try:
        # Syslog hostnames may not contain spaces; a container hostname never
        # does, but a misconfigured one could.
        return (socket.gethostname() or "rezztor-eagent").split()[0]
    except Exception:
        return "rezztor-eagent"


def _worker() -> None:
    """Owns the socket for the life of the process.

    One socket, reused: a UDP socket is connectionless, so this neither
    establishes nor holds anything on the destination -- it only avoids
    re-creating a file descriptor per event.
    """
    sock: socket.socket | None = None
    while True:
        try:
            line = _q().get()
        except Exception:
            return

        if line == _STOP:
            break

        try:
            config = plugin_config.get_config()
            if not (config["enabled"] and config["host"]):
                continue
            if sock is None:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.sendto(line.encode("utf-8", "replace"), (config["host"], config["port"]))
            with _lock:
                _stats["sent"] += 1
        except Exception:
            # A refused port, an unresolvable name, a socket that has gone bad:
            # counted, never raised, and never allowed to kill the thread -- the
            # destination may come back, and the next event should still try.
            with _lock:
                _stats["send_errors"] += 1
            try:
                if sock is not None:
                    sock.close()
            except Exception:
                pass
            sock = None


_STOP = "\x00__siem_forward_stop__"


def _q() -> queue.Queue[str]:
    global _queue
    if _queue is None:
        _queue = queue.Queue(maxsize=plugin_config.get_config()["queue_size"])
    return _queue


def _enqueue(line: str) -> None:
    q = _q()
    try:
        q.put_nowait(line)
        return
    except queue.Full:
        pass

    # Drop the oldest to make room. Newer audit events are the ones an incident
    # responder is looking at; a backlog of stale ones is not worth blocking for.
    try:
        q.get_nowait()
        with _lock:
            _stats["dropped_queue_full"] += 1
    except queue.Empty:
        pass
    try:
        q.put_nowait(line)
    except queue.Full:
        with _lock:
            _stats["dropped_queue_full"] += 1


def format_record(record: dict[str, Any]) -> str | None:
    """The formatted wire line for one audit record, or None if it is filtered
    out by `min_severity`."""
    config = plugin_config.get_config()
    fields = {k: v for k, v in record.items() if k not in ("event", "actor", "ts")}
    if cef.severity(str(record.get("event") or ""), fields) < config["min_severity"]:
        return None

    return cef.format_line(
        record,
        hostname=_hostname(),
        facility=config["facility"],
        syslog_format=config["syslog_format"],
        device_vendor=config["device_vendor"],
        device_product=config["device_product"],
        device_version=plugin_config.device_version(),
    )


def handle_record(record: dict[str, Any]) -> None:
    """The audit sink itself. Runs on whichever thread emitted the event, so it
    does the cheapest possible thing and returns.

    Both switches are checked here rather than only at registration time, so
    turning the plugin off -- or clearing the destination -- stops forwarding on
    the very next event instead of at the next restart.
    """
    try:
        if not plugin_config.is_enabled() or not plugin_config.is_configured():
            return
        line = format_record(record)
        if line is not None:
            _enqueue(line)
    except Exception:
        pass


def ensure_registered() -> None:
    """Register the sink and start the sender thread, at most once.

    Called from the startup extension (which only loads for an enabled plugin)
    and again when the configuration is saved -- so enabling the plugin and
    pointing it somewhere takes effect without a restart.
    """
    global _registered, _thread
    with _lock:
        if _registered:
            return
        _registered = True

    try:
        from plugins._audit.helpers import audit

        audit.register_sink(handle_record)
    except Exception:
        with _lock:
            _registered = False
        return

    with _lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(
                target=_worker, name="siem-forward-sender", daemon=True
            )
            _thread.start()


def send_test_event() -> dict[str, Any]:
    """Send one synthetic event now, on the calling thread, and report what
    happened -- the Settings card's "send test event" button.

    Deliberately not routed through the queue: an operator pressing test wants
    the actual result of the actual send, including the error if there is one,
    not confirmation that something was queued.
    """
    config = plugin_config.get_config()
    if not config["host"]:
        return {"ok": False, "error": "No destination host is configured."}

    record = {
        "v": 1,
        "ts": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(timespec="milliseconds"),
        "event": "siem_forward.test",
        "actor": {"user_id": None, "username": None, "role": None},
        "outcome": "success",
        "msg": "Test event from Rezztor EAgent.",
    }

    line = cef.format_line(
        record,
        hostname=_hostname(),
        facility=config["facility"],
        syslog_format=config["syslog_format"],
        device_vendor=config["device_vendor"],
        device_product=config["device_product"],
        device_version=plugin_config.device_version(),
    )

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(line.encode("utf-8", "replace"), (config["host"], config["port"]))
    except Exception as exc:
        return {"ok": False, "error": f"Could not send: {exc}", "line": line}

    # UDP: "sent" is the strongest claim available. Saying more would be a lie
    # the operator would only discover during an incident.
    return {"ok": True, "line": line}
