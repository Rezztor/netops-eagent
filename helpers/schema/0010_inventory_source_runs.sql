-- One row per run of an inventory source adapter (SolarWinds today).
--
-- Instance-wide with no row-level security, for the reason given in
-- 0009_inventory.sql: the inventory belongs to the instance, and access is
-- gated by RBAC on the endpoints. `counts` holds numbers only; `error` is the
-- adapter's message (never a credential). A dry run records its counts and
-- stores nothing else.

CREATE TABLE inventory_source_run (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source       TEXT NOT NULL,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    status       TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running','succeeded','failed','dry_run')),
    counts       JSONB NOT NULL DEFAULT '{}',
    error        TEXT NOT NULL DEFAULT '',
    batch_id     UUID,
    requested_by TEXT NOT NULL DEFAULT ''
);
CREATE INDEX inventory_source_run_source ON inventory_source_run (source, started_at DESC);
-- Latest observation per source/source_id, and per device.
CREATE INDEX inventory_observation_source ON inventory_observation (source, source_id, observed_at DESC);
