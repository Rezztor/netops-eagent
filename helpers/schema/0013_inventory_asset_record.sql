-- The asset record: type, hardware, location, ownership and lifecycle dates on a device.
-- Text defaults to '' and the rest to NULL, so existing rows stay valid and unchanged.
-- Also adds the `spare` and `faulty` statuses. Ports, parts and config backup are later phases.

ALTER TABLE inventory_device
    ADD COLUMN device_type      TEXT NOT NULL DEFAULT 'other',
    ADD COLUMN part_number      TEXT NOT NULL DEFAULT '',
    ADD COLUMN base_mac         TEXT NOT NULL DEFAULT '',
    ADD COLUMN building         TEXT NOT NULL DEFAULT '',
    ADD COLUMN floor            TEXT NOT NULL DEFAULT '',
    ADD COLUMN room             TEXT NOT NULL DEFAULT '',
    ADD COLUMN rack             TEXT NOT NULL DEFAULT '',
    ADD COLUMN rack_unit        INTEGER CHECK (rack_unit BETWEEN 1 AND 60),
    ADD COLUMN u_height         INTEGER CHECK (u_height BETWEEN 1 AND 20),
    ADD COLUMN department       TEXT NOT NULL DEFAULT '',
    ADD COLUMN custodian        TEXT NOT NULL DEFAULT '',
    ADD COLUMN support_contact  TEXT NOT NULL DEFAULT '',
    ADD COLUMN support_contract TEXT NOT NULL DEFAULT '',
    ADD COLUMN purchase_date    DATE,
    ADD COLUMN warranty_expiry  DATE,
    ADD COLUMN support_expiry   DATE,
    ADD COLUMN eol_date         DATE;

ALTER TABLE inventory_device ADD CONSTRAINT inventory_device_type_check
    CHECK (device_type IN ('router','switch','firewall','wireless_controller','access_point','load_balancer','other'));

ALTER TABLE inventory_device DROP CONSTRAINT inventory_device_status_check;
ALTER TABLE inventory_device ADD CONSTRAINT inventory_device_status_check
    CHECK (status IN ('active','planned','staged','offline','spare','faulty','decommissioned'));
