-- Community SONiC (plain SSH, `show` CLI) is its own platform; `sonic` stays Dell Enterprise SONiC.
-- Widens the platform CHECK from 0009_inventory.sql; no row changes.

ALTER TABLE inventory_device DROP CONSTRAINT inventory_device_platform_check;
ALTER TABLE inventory_device ADD CONSTRAINT inventory_device_platform_check
    CHECK (platform IN ('ios','iosxe','nxos','eos','junos','fortios','panos','sonic','sonic_community','other'));
