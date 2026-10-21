-- What a device reports about itself: its ports and its parts (ADR 0045 "observed", ADR 0049).
--
-- Instance-wide with no row-level security, for the reason given in 0009_inventory.sql. These rows
-- never go through a proposal: a refresh replaces a device's whole snapshot, rows the device no
-- longer reports are removed, and `observed_at` says when it was last read. `is_uplink` is the one
-- column a person writes (an annotation on an observed row) and survives a refresh while the port exists.

CREATE TABLE inventory_interface (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id     UUID NOT NULL REFERENCES inventory_device (id),
    name          TEXT NOT NULL,
    alias         TEXT NOT NULL DEFAULT '',
    description   TEXT NOT NULL DEFAULT '',
    admin_up      BOOLEAN,
    oper_up       BOOLEAN,
    speed         TEXT NOT NULL DEFAULT '',
    media         TEXT NOT NULL DEFAULT '',
    present       BOOLEAN,
    poe_watts     NUMERIC,
    mode          TEXT NOT NULL DEFAULT '' CHECK (mode IN ('', 'access', 'trunk', 'routed')),
    vlans         INTEGER[] NOT NULL DEFAULT '{}',
    ip_prefixes   TEXT[] NOT NULL DEFAULT '{}',
    mac           TEXT NOT NULL DEFAULT '',
    neighbor_name TEXT NOT NULL DEFAULT '',
    neighbor_port TEXT NOT NULL DEFAULT '',
    is_uplink     BOOLEAN NOT NULL DEFAULT false,
    observed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (device_id, name)
);

CREATE TABLE inventory_component (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id    UUID NOT NULL REFERENCES inventory_device (id),
    kind         TEXT NOT NULL
        CHECK (kind IN ('chassis', 'module', 'psu', 'fan', 'transceiver', 'stack_member', 'licence')),
    name         TEXT NOT NULL,
    part_number  TEXT NOT NULL DEFAULT '',
    serial       TEXT NOT NULL DEFAULT '',
    description  TEXT NOT NULL DEFAULT '',
    expires_at   DATE,
    observed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (device_id, kind, name)
);
