-- Cloud inventory (Azure today): what a read-only pull saw, with a change log between pulls.
--
-- Instance-wide with no row-level security, for the reason given in 0009_inventory.sql: the
-- estate belongs to the instance and access is RBAC on the endpoints (inventory.view reads,
-- inventory.manage pulls). The application role gets its grants from the default privileges
-- set in 0001. Nothing here is ever written by a person: a pull replaces what it read.
--
-- Every foreign key is DEFERRABLE INITIALLY IMMEDIATE. Checked per statement as usual, but a
-- backup restore (`db.load_data`) loads tables in name order with `SET CONSTRAINTS ALL
-- DEFERRED`, and that only reaches constraints declared deferrable. cloud_change sorts before
-- cloud_resource and a child row before its parent in the same table.

-- a pull can end with some subscriptions read and some not
ALTER TABLE inventory_source_run DROP CONSTRAINT inventory_source_run_status_check;
ALTER TABLE inventory_source_run ADD CONSTRAINT inventory_source_run_status_check
    CHECK (status IN ('running','succeeded','failed','dry_run','partial'));

CREATE TABLE cloud_account (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider   TEXT NOT NULL CHECK (provider IN ('azure')),
    native_id  TEXT NOT NULL,                      -- the tenant id, lower-cased
    name       TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (provider, native_id)
);

CREATE TABLE cloud_container (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id    UUID NOT NULL REFERENCES cloud_account (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
    parent_id     UUID REFERENCES cloud_container (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,   -- a resource group names its subscription
    kind          TEXT NOT NULL,                   -- azure: 'subscription' | 'resource_group'
    native_id     TEXT NOT NULL,
    name          TEXT NOT NULL,
    state         TEXT NOT NULL DEFAULT '',        -- subscription: Enabled, Warned, PastDue, Disabled, Deleted
    region        TEXT NOT NULL DEFAULT '',
    tags          JSONB NOT NULL DEFAULT '{}',
    props         JSONB NOT NULL DEFAULT '{}',     -- subscription: {"management_groups": [{"id", "name"}, ...]}
    readable      BOOLEAN NOT NULL DEFAULT true,   -- false: not in the last subscription list
    last_read_at  TIMESTAMPTZ,                     -- subscription: its last complete read; the "as of" of its resources
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    removed_at    TIMESTAMPTZ,
    UNIQUE (account_id, native_id)
);
CREATE INDEX cloud_container_parent ON cloud_container (parent_id);

CREATE TABLE cloud_resource (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id      UUID NOT NULL REFERENCES cloud_account (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
    subscription_id UUID NOT NULL REFERENCES cloud_container (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
    container_id    UUID NOT NULL REFERENCES cloud_container (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,   -- its resource group
    parent_id       UUID REFERENCES cloud_resource (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,             -- a subnet names its network
    native_id       TEXT NOT NULL,
    name            TEXT NOT NULL,
    type            TEXT NOT NULL,                 -- lower-case, as Resource Graph returns it
    kind            TEXT NOT NULL DEFAULT '',
    category        TEXT NOT NULL CHECK (category IN ('compute','web','containers','networking','storage',
                        'databases','identity_security','integration','monitoring','other')),
    region          TEXT NOT NULL DEFAULT '',
    state           TEXT NOT NULL DEFAULT 'ok' CHECK (state IN ('running','stopped','failed','ok','unknown')),
    state_detail    TEXT NOT NULL DEFAULT '',      -- Azure's own word: PowerState/deallocated, Stopped, Failed, Paused
    sku             JSONB NOT NULL DEFAULT '{}',
    tags            JSONB NOT NULL DEFAULT '{}',
    props           JSONB NOT NULL DEFAULT '{}',   -- allow-listed fields only (shape.py)
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    changed_at      TIMESTAMPTZ,
    removed_at      TIMESTAMPTZ,
    UNIQUE (account_id, native_id)
);
CREATE INDEX cloud_resource_place ON cloud_resource (subscription_id, container_id, category) WHERE removed_at IS NULL;
CREATE INDEX cloud_resource_type ON cloud_resource (type);
CREATE INDEX cloud_resource_name ON cloud_resource (lower(name));
CREATE INDEX cloud_resource_tags ON cloud_resource USING gin (tags);

CREATE TABLE cloud_relation (
    from_id      UUID NOT NULL REFERENCES cloud_resource (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
    kind         TEXT NOT NULL,
    to_native_id TEXT NOT NULL,                    -- kept even when the target is outside what we can read
    to_id        UUID REFERENCES cloud_resource (id) ON DELETE SET NULL DEFERRABLE INITIALLY IMMEDIATE,
    props        JSONB NOT NULL DEFAULT '{}',      -- peering: {"name", "state"}
    PRIMARY KEY (from_id, kind, to_native_id)
);
CREATE INDEX cloud_relation_to ON cloud_relation (to_id);

CREATE TABLE cloud_change (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id      UUID NOT NULL REFERENCES inventory_source_run (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
    resource_id UUID NOT NULL REFERENCES cloud_resource (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    change      TEXT NOT NULL CHECK (change IN ('added','changed','removed','returned')),
    fields      JSONB NOT NULL DEFAULT '{}'        -- changed: {"props.https_only": {"old": false, "new": true}}
);
CREATE INDEX cloud_change_run ON cloud_change (run_id);
CREATE INDEX cloud_change_resource ON cloud_change (resource_id, at DESC);
