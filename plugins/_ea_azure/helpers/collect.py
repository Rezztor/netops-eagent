"""One Azure pull: list the subscriptions, read each one completely, and save it. The first pull, the Saturday
pull and Pull now all run these steps.

All or nothing per subscription: a subscription is applied only after every page of it was read, so a failed read
marks nothing removed. "Every page" includes Azure's own findings (Advisor, policy, change records, health): all four
are read with the resources, before anything of the subscription is written. Sign-in or a failed subscription list ends the pull before anything is saved. A pull is
recorded in `inventory_source_run` (source `azure`), one at a time, and audited with counts only.
"""

from __future__ import annotations

import datetime as _dt
import logging
import threading
import time
from typing import Any

from plugins._ea_azure.helpers import categories, client, config, queries, shape, signals, store
from plugins._ea_inventory.helpers import store as inv

try:  # pragma: no cover - the audit plugin is bundled but can be switched off
    from plugins._audit.helpers import audit
except Exception:  # noqa: BLE001
    audit = None  # type: ignore[assignment]

log = logging.getLogger(__name__)

SOURCE = "azure"
STUCK_MINUTES = 30
_UNKNOWN_TYPES_SHOWN = 20
_SIGNAL_CAP = 5000  # rows of one kind per subscription; the change records come newest first, so the cap keeps the latest
_QUERY_WINDOW, _QUERY_BURST = 5.0, 14  # Resource Graph allows 15 queries per 5 seconds for one user; stay under
_sleep = time.sleep
_sent: list[float] = []  # when this process last sent a query, newest last
_begin_lock = threading.Lock()
_progress: dict[str, Any] = {}  # ponytail: in this process only; a second worker would show no progress


class Busy(Exception):
    """A pull is running."""


class TooSoon(Exception):
    """The last pull finished less than `min_pull_interval_minutes` ago (the schedule is exempt)."""


class _TooMany(client.AzureError):
    pass


def _emit(event: str, **fields: Any) -> None:
    if audit is not None:
        audit.emit(event, **fields)


def _close_stuck() -> None:
    with inv.transaction() as conn:
        conn.execute(
            "UPDATE inventory_source_run SET status = 'failed', finished_at = now(), "
            "error = 'The pull did not finish and was closed after 30 minutes.' "
            "WHERE source = %s AND status = 'running' AND started_at < now() - make_interval(mins => %s)",
            (SOURCE, STUCK_MINUTES))


def _begin(trigger: str, actor: str) -> str:
    """Check that a pull may start and record it as running. Raises `Busy`, `TooSoon` or `client.NotConfigured`."""
    if not (config.is_enabled() and config.is_configured()):
        raise client.NotConfigured("Azure is not set up or is turned off.")
    cfg = config.get_config()
    with _begin_lock:
        _close_stuck()
        if inv.running_since(SOURCE, STUCK_MINUTES):
            raise Busy("An Azure pull is already running.")
        last = inv.last_run(SOURCE)
        if trigger != "schedule" and last and last["finished_at"]:
            wait = _dt.timedelta(minutes=cfg["min_pull_interval_minutes"])
            if _dt.datetime.now(_dt.timezone.utc) - _dt.datetime.fromisoformat(last["finished_at"]) < wait:
                raise TooSoon(f"The last Azure pull finished less than {cfg['min_pull_interval_minutes']} minutes ago.")
        run_id = inv.start_run(SOURCE, actor)
    _emit("azure.pull_started", trigger=trigger, requested_by=actor, run_id=run_id)
    return run_id


def _scope(listed: list[dict], cfg: dict) -> tuple[list[dict], list[dict]]:
    """(to read, skipped with the reason): a deleted subscription, or one `subscriptions_mode` leaves out."""
    chosen, mode = set(cfg["subscription_ids"]), cfg["subscriptions_mode"]
    read, skipped = [], []
    for sub in listed:
        if sub["state"].lower() == "deleted":
            skipped.append({**sub, "why": "The subscription is deleted."})
        elif (mode == "only" and sub["id"] not in chosen) or (mode == "except" and sub["id"] in chosen):
            skipped.append({**sub, "why": "Left out by the subscription setting."})
        else:
            read.append(sub)
    return read, skipped


def _pace() -> None:
    """Pause before a query when the last five seconds were already full of them (a pull sends five per subscription).
    Later pages of one query are paced by the client from Azure's quota headers."""
    while True:
        now = time.monotonic()
        _sent[:] = [t for t in _sent if now - t < _QUERY_WINDOW]
        if len(_sent) < _QUERY_BURST:
            break
        _sleep(_QUERY_WINDOW - (now - _sent[0]) + 0.1)
    _sent.append(time.monotonic())


def _signals(query: str, sub_id: str, cap: int) -> list[dict]:
    """Up to `cap` rows of one of Azure's own tables for one subscription. Over the cap is not an error."""
    rows: list[dict] = []
    _pace()
    for page in client.graph(query, [sub_id]):
        rows += page
        if len(rows) >= cap:
            break
    return rows[:cap]


def _read(query: str, sub_id: str, seen: int, limit: int) -> list[dict]:
    rows: list[dict] = []
    _pace()
    for page in client.graph(query, [sub_id]):
        rows += page
        if seen + len(rows) > limit:
            raise _TooMany(f"The tenant has more than {limit} resources; raise the limit in the settings.")
    return rows


def _collect(run_id: str, cfg: dict, counts: dict[str, Any]) -> str:
    listed = client.subscriptions()
    todo, skipped = _scope(listed, cfg)
    counts["subscriptions"] = {"listed": len(listed), "read": 0, "failed": 0, "skipped": len(skipped)}
    per = counts["per_subscription"]
    per += [{"id": s["id"], "name": s["name"], "status": "skipped", "resources": 0, "error": s["why"]} for s in skipped]
    if not todo:
        raise client.AzureError("No subscription can be read. Give the application the Reader role on a subscription "
                                "or a management group.")
    account = store.account(cfg["tenant_id"])
    ids = [s["id"] for s in todo]
    rows = [r for page in client.graph(queries.CONTAINERS, ids) for r in page]
    found = [c for r in rows if (c := shape.container(r))]
    by_id = {c["id"].rsplit("/", 1)[-1]: c for c in found if c["id"].count("/") == 2}
    groups = [c for c in found if "/resourcegroups/" in c["id"]]
    now = _dt.datetime.now(_dt.timezone.utc)
    store.apply_containers(account, [
        {"id": s["id"], "name": s["name"], "state": s["state"], "region": by_id.get(s["id"], {}).get("region", ""),
         "tags": by_id.get(s["id"], {}).get("tags", {}), "props": by_id.get(s["id"], {}).get("props", {})}
        for s in todo], groups, now)

    unknown: set[str] = set()
    total = 0
    _progress.update(run_id=run_id, total=len(todo), done=0, current="")
    for i, sub in enumerate(todo):
        _progress.update(done=i, current=sub["name"])
        entry = {"id": sub["id"], "name": sub["name"], "status": "failed", "resources": 0, "error": ""}
        per.append(entry)
        try:
            shaped = [s for r in _read(queries.RESOURCES, sub["id"], total, cfg["max_resources"]) if (s := shape.reduce(r))]
            if any(s["type"] == shape.SITES for s in shaped):
                try:
                    shape.merge_runtime(shaped, _read(queries.SITE_CONFIG, sub["id"], 0, cfg["max_resources"]))
                except client.ReadFailed:  # unverified: the table may not exist; the runtime stays ""
                    pass
            asked = {"advisor": _signals(queries.ADVISOR, sub["id"], _SIGNAL_CAP),
                     "policy": _signals(queries.POLICY, sub["id"], _SIGNAL_CAP),
                     "change": _signals(queries.CHANGES, sub["id"], _SIGNAL_CAP),
                     "health": _signals(queries.HEALTH, sub["id"], _SIGNAL_CAP)}
            # ponytail: a second transaction after the reads, so a database failure here can leave this
            # subscription's findings newer than its resources; one transaction needs a hook in store.apply_subscription
            kept = signals.apply(account, sub["id"], {k: [x for r in rows if (x := shape.signal(k, r))] for k, rows in asked.items()},
                                 _dt.datetime.now(_dt.timezone.utc))
            applied = store.apply_subscription(account, sub["id"], shaped, run_id=run_id,
                                               now=_dt.datetime.now(_dt.timezone.utc))
        except _TooMany:
            raise
        except client.ReadFailed as exc:
            entry["error"] = str(exc)
            counts["subscriptions"]["failed"] += 1
            continue
        except client.AzureError as exc:  # sign-in lost halfway: stop, keep what is saved
            entry["error"] = str(exc)
            counts["subscriptions"]["failed"] += len(todo) - i
            per += [{"id": s["id"], "name": s["name"], "status": "failed", "resources": 0, "error": str(exc)}
                    for s in todo[i + 1:]]
            break
        except Exception as exc:  # noqa: BLE001 - one subscription must not take the others down
            log.error("azure pull: %s could not be stored (%s)", sub["id"], type(exc).__name__)
            entry["error"] = "This subscription could not be stored."
            counts["subscriptions"]["failed"] += 1
            continue
        total += applied["seen"]
        entry.update(status="ok", resources=applied["seen"])
        counts["subscriptions"]["read"] += 1
        counts["resources"] += applied["seen"]
        for kind, n in kept.items():
            counts["signals"][kind] += n
            if len(asked[kind]) >= _SIGNAL_CAP and kind not in counts["signals_capped"]:
                counts["signals_capped"].append(kind)
        for key in ("added", "changed", "removed", "returned"):
            counts[key] += applied[key]
        unknown |= {s["type"] for s in _flat(shaped) if not categories.known(s["type"])}
        counts["unknown_types"] = sorted(unknown)[:_UNKNOWN_TYPES_SHOWN]
    sub_counts = counts["subscriptions"]
    if not sub_counts["read"]:
        return "failed"
    return "partial" if sub_counts["failed"] else "succeeded"


def _flat(shaped: list[dict]) -> list[dict]:
    return [x for s in shaped for x in (s, *s["children"])]


def _run(run_id: str, trigger: str) -> dict[str, Any]:
    started = time.monotonic()
    cfg = config.get_config()
    counts: dict[str, Any] = {
        "trigger": trigger, "subscriptions": {"listed": 0, "read": 0, "failed": 0, "skipped": 0}, "resources": 0,
        "added": 0, "changed": 0, "removed": 0, "returned": 0, "unknown_types": [], "duration_s": 0, "per_subscription": [],
        "signals": dict.fromkeys(signals.KINDS, 0), "signals_capped": []}
    status, error = "failed", ""
    try:
        status = _collect(run_id, cfg, counts)
        if status == "failed":
            error = "No subscription could be read."
        else:
            try:
                store.prune(cfg["keep_removed_days"], cfg["keep_history_days"])
            except Exception as exc:  # noqa: BLE001 - housekeeping must not fail a pull that read everything
                log.error("azure pull: prune failed (%s)", type(exc).__name__)
    except client.AzureError as exc:
        error = str(exc)
    except Exception as exc:  # noqa: BLE001 - recorded on the run; the loop must go on
        log.error("azure pull stopped (%s)", type(exc).__name__)
        error = "The pull stopped on an unexpected error."
    finally:
        counts["duration_s"] = round(time.monotonic() - started, 1)
        inv.finish_run(run_id, status, counts, error)
        _progress.clear()
    _emit("azure.pull_finished", run_id=run_id, status=status, trigger=trigger,
          **{k: v for k, v in counts.items() if k not in ("per_subscription", "unknown_types", "trigger")})
    return {"run_id": run_id, "status": status, "error": error, "counts": counts}


def pull(trigger: str, actor: str) -> dict[str, Any]:
    """Blocking. `trigger`: schedule | screen | chat. Raises `Busy`, `TooSoon` or `client.NotConfigured`."""
    return _run(_begin(trigger, actor), trigger)


def start(trigger: str, actor: str) -> str:
    """Start a pull in the background and return its run id. Raises like `pull`."""
    run_id = _begin(trigger, actor)
    threading.Thread(target=_run, args=(run_id, trigger), daemon=True, name="azure-pull").start()
    return run_id


def status() -> dict[str, Any]:
    """`{"running": run | None, "last_run": run | None}`; a running run carries its `progress`."""
    _close_stuck()
    found = store.runs(5)
    running = next((r for r in found if r["status"] == "running"), None)
    if running and _progress.get("run_id") == running["id"]:
        running = {**running, "progress": {k: _progress[k] for k in ("total", "done", "current") if k in _progress}}
    return {"running": running, "last_run": next((r for r in found if r["status"] != "running"), None)}
