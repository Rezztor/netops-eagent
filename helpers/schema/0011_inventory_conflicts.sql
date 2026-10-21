-- Conflicts between an automatic source's observed value and a human's value, and the
-- fields an engineer pinned against a source (ADR 0043).
--
-- Instance-wide with no row-level security, for the reason given in 0009_inventory.sql.
-- A conflict never changes a device: accepting one creates an ordinary proposal that a
-- person approves. `current_value` / `proposed_value` are JSON so any field fits.

CREATE TABLE inventory_conflict (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id      UUID NOT NULL REFERENCES inventory_device (id),
    field          TEXT NOT NULL,
    current_value  JSONB,
    current_source TEXT NOT NULL DEFAULT '',
    proposed_value JSONB,
    source         TEXT NOT NULL,
    evidence       JSONB NOT NULL DEFAULT '{}',
    observed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    status         TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open','accepted','kept','pinned','superseded')),
    decided_by     TEXT NOT NULL DEFAULT '',
    decided_at     TIMESTAMPTZ,
    note           TEXT NOT NULL DEFAULT '',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- One open conflict per device, field and source; a changed observation supersedes it.
CREATE UNIQUE INDEX inventory_conflict_open ON inventory_conflict (device_id, field, source)
    WHERE status = 'open';
CREATE INDEX inventory_conflict_status ON inventory_conflict (status, created_at DESC);

CREATE TABLE inventory_pin (
    device_id UUID NOT NULL REFERENCES inventory_device (id),
    field     TEXT NOT NULL,
    source    TEXT NOT NULL,
    pinned_by TEXT NOT NULL DEFAULT '',
    pinned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (device_id, field, source)
);
