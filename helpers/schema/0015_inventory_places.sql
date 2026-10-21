-- Inventory places: site > location > building > floor > room > rack, one self-referencing table.
--
-- Additive only. `inventory_site` and the device columns `site_id`, `building`, `floor`, `room` and
-- `rack` stay (the adopt tool reads them) and new code does not write them. Like the rest of the
-- inventory this table has no row-level security (see 0009_inventory.sql).
--
-- The rules live here, not in the application: the ID is the parent's ID plus the code, the kind is
-- the depth, so a child can name only a parent exactly one level up and no cycle can exist. The ID
-- never changes; the application role may update only what a rename or a deactivation needs.
-- COLLATE "C" makes `ORDER BY id` tree order and lets `LIKE 'DIFC-GA-%'` use the index.
--
-- The two foreign keys are DEFERRABLE INITIALLY IMMEDIATE: checked per statement as usual, but a
-- backup restore (`db.load_data`, SET CONSTRAINTS ALL DEFERRED) may insert rows in any order.

CREATE TABLE inventory_place (
    id         TEXT COLLATE "C" PRIMARY KEY CHECK (id ~ '^[A-Z0-9]{1,8}(-[A-Z0-9]{1,8}){0,5}$'),
    parent_id  TEXT COLLATE "C" REFERENCES inventory_place (id) DEFERRABLE INITIALLY IMMEDIATE,
    kind       TEXT NOT NULL,
    code       TEXT NOT NULL CHECK (code ~ '^[A-Z0-9]{1,8}$'),
    name       TEXT NOT NULL CHECK (btrim(name) <> '' AND length(name) <= 128),
    active     BOOLEAN NOT NULL DEFAULT true,
    room_type  TEXT NOT NULL DEFAULT ''
        CHECK (room_type IN ('', 'server_room', 'comms_room', 'riser', 'office', 'store', 'other')),
    address    TEXT NOT NULL DEFAULT '',
    contact    TEXT NOT NULL DEFAULT '',
    notes      TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- the ID is the parent's ID plus the code, so a child cannot name the wrong parent
    CONSTRAINT inventory_place_id_is_parent_plus_code
        CHECK (id = CASE WHEN parent_id IS NULL THEN code ELSE parent_id || '-' || code END),
    -- the kind is the depth; with the check above a parent is always exactly one kind up, and no cycle can exist
    CONSTRAINT inventory_place_kind_is_depth
        CHECK (kind = (ARRAY['site','location','building','floor','room','rack'])[cardinality(string_to_array(id, '-'))]),
    CONSTRAINT inventory_place_room_type_on_rooms CHECK (room_type = '' OR kind = 'room'),
    CONSTRAINT inventory_place_address_on_sites CHECK ((address = '' AND contact = '') OR kind = 'site')
);
CREATE UNIQUE INDEX inventory_place_name ON inventory_place (COALESCE(parent_id, ''), lower(name));

-- IDs never change: the application role may update only what a rename or a deactivation needs.
REVOKE UPDATE ON inventory_place FROM eagent_app;
GRANT UPDATE (name, active, room_type, address, contact, notes, updated_at) ON inventory_place TO eagent_app;

ALTER TABLE inventory_device
    ADD COLUMN place_id TEXT COLLATE "C" REFERENCES inventory_place (id) DEFERRABLE INITIALLY IMMEDIATE,
    ADD CONSTRAINT inventory_device_rack_unit_needs_rack
        CHECK (rack_unit IS NULL OR place_id IS NULL OR place_id ~ '^([A-Z0-9]+-){5}[A-Z0-9]+$');
CREATE INDEX inventory_device_place ON inventory_device (place_id);

-- Nothing waiting in Review is lost: a pending proposal that changes the old place text keeps what it
-- said in evidence.old_place_text, and an update left with no change is superseded.
UPDATE inventory_proposal p SET
    evidence = jsonb_set(
        p.evidence, '{old_place_text}',
        COALESCE(p.evidence -> 'old_place_text', '{}'::jsonb) || (
            SELECT COALESCE(jsonb_object_agg(c.key, c.value -> 'new'), '{}'::jsonb)
            FROM jsonb_each(p.changes) c
            WHERE c.key IN ('site_id', 'building', 'floor', 'room', 'rack')
        )
    ),
    changes = p.changes - ARRAY['site_id', 'building', 'floor', 'room', 'rack']
WHERE p.status = 'pending' AND p.changes ?| ARRAY['site_id', 'building', 'floor', 'room', 'rack'];

UPDATE inventory_proposal SET
    status = 'superseded', decided_by = 'system:migration', decided_at = now(),
    decision_note = 'The place is now picked from the list.'
WHERE status = 'pending' AND kind = 'update' AND changes = '{}'::jsonb AND evidence ? 'old_place_text';
