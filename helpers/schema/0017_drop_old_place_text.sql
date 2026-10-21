-- Drop the old free-text place: `inventory_site` and the device columns `site_id`, `building`, `floor`,
-- `room` and `rack`. A device's place is `place_id` alone (ADR 0054, 0057).
--
-- Safe whether or not every device was adopted first: what a device with no `place_id` still says is
-- copied into `custom.old_place_text` (one line of text) before the columns go, and an old site that was
-- never adopted is kept in `inventory_unadopted_site` and named in a WARNING. Nothing is lost.

-- 1. A device that is waiting in Review to move into a rack carries its rack unit with it. The check
--    below forbids a unit with no rack, so the unit is cleared from the device in step 2 and the
--    pending proposal sets it together with the place.
UPDATE inventory_proposal p SET
    changes = p.changes || jsonb_build_object('rack_unit', jsonb_build_object('old', NULL, 'new', d.rack_unit))
FROM inventory_device d
WHERE p.device_id = d.id AND p.status = 'pending' AND p.kind = 'update'
  AND p.changes ? 'place_id' AND NOT p.changes ? 'rack_unit'
  AND d.place_id IS NULL AND d.rack_unit IS NOT NULL
  AND p.changes -> 'place_id' ->> 'new' ~ '^([A-Z0-9]+-){5}[A-Z0-9]+$';

-- 2. What a device with no place still says, as text a person can read: "Lab / 1 / 1.01 / R01 U22".
UPDATE inventory_device d SET
    custom = d.custom || jsonb_build_object('old_place_text', concat_ws(
        ' / ', (SELECT s.name FROM inventory_site s WHERE s.id = d.site_id),
        NULLIF(d.building, ''), NULLIF(d.floor, ''), NULLIF(d.room, ''), NULLIF(d.rack, '')
    ) || COALESCE(' U' || d.rack_unit, '')),
    rack_unit = NULL
WHERE d.place_id IS NULL
  AND (d.site_id IS NOT NULL OR d.building <> '' OR d.floor <> '' OR d.room <> '' OR d.rack <> ''
       OR d.rack_unit IS NOT NULL);

-- 3. An old site no place carries the name of (a split such as `DIFC-ER1` still waiting for its levels)
--    is reported and kept. Names are compared as the application does: letters and digits, case ignored.
DO $$
DECLARE
    missing integer;
BEGIN
    CREATE TEMP TABLE unadopted ON COMMIT DROP AS
        SELECT s.name, s.location, s.notes, now() AS dropped_at FROM inventory_site s
        WHERE NOT EXISTS (
            SELECT 1 FROM inventory_place p WHERE p.kind = 'site'
              AND regexp_replace(lower(p.name), '[^a-z0-9]', '', 'g') = regexp_replace(lower(s.name), '[^a-z0-9]', '', 'g'));
    SELECT count(*) INTO missing FROM unadopted;
    IF missing > 0 THEN
        CREATE TABLE inventory_unadopted_site AS SELECT * FROM unadopted;
        RAISE WARNING 'inventory: % old site(s) were never adopted as places and are kept in inventory_unadopted_site: %',
            missing, (SELECT string_agg(name, ', ' ORDER BY name) FROM unadopted);
    END IF;
END $$;

-- 4. Drop the old text.
ALTER TABLE inventory_device
    DROP CONSTRAINT inventory_device_rack_unit_needs_rack,
    DROP COLUMN site_id,
    DROP COLUMN building,
    DROP COLUMN floor,
    DROP COLUMN room,
    DROP COLUMN rack;
DROP TABLE inventory_site;

-- 5. A rack unit needs a rack ID. COALESCE: a NULL place would make the check NULL, and a NULL check passes.
ALTER TABLE inventory_device ADD CONSTRAINT inventory_device_rack_unit_needs_rack
    CHECK (rack_unit IS NULL OR COALESCE(place_id ~ '^([A-Z0-9]+-){5}[A-Z0-9]+$', false));
