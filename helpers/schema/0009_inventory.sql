-- Device inventory: sites, devices, observations and change proposals.
--
-- **No row-level security, and unlike conversations or memory that is the point
-- rather than an omission.** The inventory is the instance's estate, not any
-- one user's data: every engineer reads the same devices, and the agent reads
-- them on a background thread with no user in scope. A policy keyed on an owner
-- would hide the whole table from exactly the callers it exists for. Access is
-- gated by RBAC on the endpoints (inventory.view / .manage / .approve in
-- helpers/api_policy.py) -- the same arrangement as scheduled_tasks and
-- runner_slots. The application role gets its grants from the default
-- privileges set in 0001, so nothing is granted here.
--
-- Nothing is ever deleted. A device that leaves service becomes
-- `decommissioned`, and every change to a device passes through a proposal that
-- a person approved, so the rows below are also the history of who decided what.

CREATE TABLE inventory_site (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name       TEXT NOT NULL,
    location   TEXT NOT NULL DEFAULT '',
    notes      TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX inventory_site_name ON inventory_site (lower(name));

CREATE TABLE inventory_device (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name             TEXT NOT NULL,
    role             TEXT NOT NULL DEFAULT '',
    vendor           TEXT NOT NULL DEFAULT '',
    platform         TEXT NOT NULL DEFAULT 'other'
        CHECK (platform IN ('ios','iosxe','nxos','eos','junos','fortios','panos','sonic','other')),
    model            TEXT NOT NULL DEFAULT '',
    -- NULL, not '', when unknown: a placeholder serial must not collide with
    -- another device's placeholder under the unique index below.
    serial           TEXT,
    asset_tag        TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'planned'
        CHECK (status IN ('active','planned','staged','offline','decommissioned')),
    site_id          UUID REFERENCES inventory_site (id),
    mgmt_ip          TEXT,
    software_version TEXT NOT NULL DEFAULT '',
    ha_group         TEXT NOT NULL DEFAULT '',
    tags             TEXT[] NOT NULL DEFAULT '{}',
    custom           JSONB NOT NULL DEFAULT '{}',
    -- {field: {source, approved_by, approved_at, observed_at}}
    field_sources    JSONB NOT NULL DEFAULT '{}',
    last_seen_at     TIMESTAMPTZ,
    -- Bumped on every approved change; a proposal records the version it was
    -- made against and is refused if the device has moved on.
    version          INTEGER NOT NULL DEFAULT 1,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX inventory_device_name ON inventory_device (lower(name));
CREATE UNIQUE INDEX inventory_device_serial ON inventory_device (serial) WHERE serial IS NOT NULL;
-- A decommissioned device gives its address back.
CREATE UNIQUE INDEX inventory_device_mgmt_ip ON inventory_device (mgmt_ip)
    WHERE mgmt_ip IS NOT NULL AND status <> 'decommissioned';
CREATE INDEX inventory_device_site ON inventory_device (site_id);

-- What a source said about a device, kept for later phases. Phase 1 only
-- creates the table and a function that adds to it.
CREATE TABLE inventory_observation (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id   UUID REFERENCES inventory_device (id),
    source      TEXT NOT NULL,
    source_id   TEXT NOT NULL DEFAULT '',
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload     JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX inventory_observation_device ON inventory_observation (device_id, observed_at DESC);

CREATE TABLE inventory_proposal (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind         TEXT NOT NULL CHECK (kind IN ('create','update','decommission')),
    device_id    UUID REFERENCES inventory_device (id),
    device_name  TEXT NOT NULL DEFAULT '',
    base_version INTEGER,
    -- {field: {old, new}}
    changes      JSONB NOT NULL DEFAULT '{}',
    source       TEXT NOT NULL DEFAULT 'manual',
    evidence     JSONB NOT NULL DEFAULT '{}',
    status       TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','approved','rejected','superseded')),
    requested_by TEXT NOT NULL DEFAULT '',
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_by   TEXT NOT NULL DEFAULT '',
    decided_at   TIMESTAMPTZ,
    decision_note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX inventory_proposal_status ON inventory_proposal (status, requested_at DESC);
CREATE INDEX inventory_proposal_device ON inventory_proposal (device_id);
