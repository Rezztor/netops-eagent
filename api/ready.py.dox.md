# api/ready.py

## Purpose

- Report whether the dependencies this instance serves requests through are
  working right now, for a monitor to scrape.

## Contracts

- `Ready` is an `ApiHandler`. `GET` and `POST`.
- **Unauthenticated, deliberately.** Authenticating reads the session from
  PostgreSQL, so an authenticated readiness check fails in the auth layer
  with a 500 during the outage it exists to report -- verified by stopping
  the database container and watching the first version do exactly that.
  The body is bounded in exchange: `ok` and `ms` per check and nothing
  else, no exception text and no host names. A 503 already says what a
  caller could learn from it.
- Answers `200` with `{"ok": true, "checks": {...}}` when every check passes and
  `503` with the same shape when any fails, so a monitor can act on the status
  line and read the body only when it needs to.
- Each check reports `ok` and `ms`. Every exception is swallowed into
  `ok: false`: an endpoint whose job is to answer during a failure cannot
  be one that raises during a failure.

## Key Concepts

- **Separate from `/api/health`, on purpose.** ADR 0022 keeps the liveness probe
  shallow because gating it on a dependency turns one outage into a total one,
  including the interface an operator needs during it. That reasoning holds for
  startup -- migrations run before the server binds and `prepare_database()`
  raises rather than warns -- and does not cover steady state: if PostgreSQL
  stops, `/api/health` keeps answering 200, compose keeps the container
  `healthy`, and `restart: unless-stopped` does nothing, because it reacts to a
  process exiting rather than to one that is up and failing.
- The database check runs through `helpers.db.transaction`, the pool that serves
  requests, rather than opening a connection of its own: an exhausted pool looks
  identical from outside to a healthy one and is the more likely failure.
- Compose's healthcheck should stay pointed at `/api/health`. This endpoint is
  for monitoring that pages a person, not for anything that restarts a
  container.

## Work Guidance

- Add a check here only for something a request cannot be served without. A
  dependency the product degrades gracefully without -- search, a model provider
  -- belongs on a status screen, not in a readiness answer.
- Keep every check bounded in time; a readiness endpoint that hangs is worse
  than one that reports a failure.
- Keep request/response, tool, or helper semantics documented here at the same
  time as source changes.
