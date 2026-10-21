---
name: netops-inventory-import
description: "Build the device inventory from a file the engineer attached (CSV, Excel, PDF, Word, config dump) or pasted text. Use when asked to import, load, add or update devices from a file or list."
triggers:
  - "import devices"
  - "import this spreadsheet"
  - "add these devices to the inventory"
  - "load the inventory from"
---

# Import devices into the inventory

You stage; the requesting engineer approves in Inventory -> Review and is accountable.

1. **Read in pages** with `inventory_read_file` (`offset`, `limit`). You only ever see a redacted view; never repeat `[REDACTED:...]` content or ask for secrets.
2. **Map columns** to fields. Leave out what the file does not say. Never invent a value. `ask_user` when the mapping is ambiguous.
   Existing: name, role, vendor, platform, model, serial, asset_tag, status, mgmt_ip, software_version, ha_group, tags.
   Also: device_type, part_number, base_mac, rack_unit, u_height, department, custodian, support_contact, support_contract, purchase_date, warranty_expiry, support_expiry, eol_date. These names are accepted by `inventory_stage`, plus the place (below). The template (Inventory -> Download template) is a header row of the same names, then `site, location, building, floor, room, rack` as the name alternative to `place_id`, and no `custom` column.
   - `device_type` is one of router, switch, firewall, wireless_controller, access_point, load_balancer, other. Map a plain word (Switch, Access Point). If it does not map cleanly, ask. Do not use `other` as a fallback.
   - `status` may also be spare or faulty. Role stays free text.
   - Dates (`purchase_date`, `warranty_expiry`, `support_expiry`, `eol_date`) must be `YYYY-MM-DD`. Convert an unambiguous date (`2 Oct 2026` -> `2026-10-02`). `ask_user` when it is ambiguous (`01/02/2026` may be 1 Feb or 2 Jan). A date you cannot read: leave the field out and say so.
   - `R07 U22` is two fields: the rack `R07` (a place, below) and `rack_unit` `22`. Do not put the whole string in `rack`. Set `u_height` only when the file states a height. `rack_unit` is 1..60, `u_height` is 1..20.
   - Pass `base_mac` as written (colon, dash, dotted or 12 hex). Anything else: ask, do not invent.
   - **The place** is one of six levels: site > location > building > floor > room > rack. When the file has a place ID (`DIFC-GA-GV4-L1-R02`) pass it as `place_id`. Otherwise pass the names or codes it has as `site`, `location`, `building`, `floor`, `room`, `rack`; `location` sits between `site` and `building`. Leave out a level the file does not say: it is filled only when the level above has exactly one choice, otherwise the record is staged without a place. Only a place that already exists is used. **Never invent a place** or a level, and never guess which location a building is in; `ask_user` when a file's building could be in more than one.
3. **Stage** with `inventory_stage` in batches of at most 50. Each record needs a verbatim `quote` from the file and its `location`; add `confidence` when unsure.
4. **Report** each batch: created, updates, unchanged, every rejected record with its reason, and warnings. List `missing_places` (the path, the first missing level, how many records): the engineer adds those under Inventory -> Places, then the devices are placed from Review or a re-import.
5. **Hand over:** tell the engineer to review in Inventory -> Review. Never say a device was added before approval; you cannot approve.
