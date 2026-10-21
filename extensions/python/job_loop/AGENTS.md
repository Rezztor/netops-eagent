# Job Loop Extensions DOX

## Purpose

- Own periodic backend maintenance jobs.

## Ownership

- Ordered Python files own cleanup of expired API chats, cache trimming, the licence's background work, and future job-loop tasks.
- `_40_close_idle_sockets.py` closes WebSocket connections idle past the session idle limit (`helpers.ws.idle_sids`).
- `_30_license_heartbeat.py` calls `helpers.license_client.tick` on a worker thread once a loop: the clock mark, a pending request repeated, and the daily check-in. It is the only scheduled call to the licence server; its schedule lives in the database, not in the process.
- `_60_update_check.py` calls `helpers.update_spool.daily_refresh` once a loop: with a spool, the instance setting `update_daily_check` on, the host idle and no request pending, it writes one `refresh` request when the host's last check (or this process's last ask) is a day old. The host tool asks the update service (ADR 0091); the application never does.

## Local Contracts

- Jobs must be idempotent and safe to run repeatedly.
- Keep cleanup scoped to owned caches, temporary contexts, or documented runtime state.

## Work Guidance

- Avoid expensive work on every loop; use timestamps or thresholds when practical.

## Verification

- Run targeted cleanup/cache tests or smoke-test job-loop startup after changes.

## Child DOX Index

No child DOX files.
