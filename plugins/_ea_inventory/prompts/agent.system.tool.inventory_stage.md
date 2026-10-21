### inventory_stage

Stage device records you mapped from a file read with `inventory_read_file`. Each record becomes a pending request in Inventory -> Pending changes; nothing is added or changed until an engineer approves. You cannot approve.

Args: `file` (the path you gave `inventory_read_file`), `records` (at most 200; send batches of 50), `note` (optional). A record is `{"fields": {...}, "evidence": {"quote": "...", "location": "..."}, "confidence": 0-1}`.
- `fields`: name, role, vendor, platform, model, serial, asset_tag, status, mgmt_ip, software_version, ha_group, tags, custom, device_type, part_number, base_mac, rack_unit, u_height, department, custodian, support_contact, support_contract, purchase_date, warranty_expiry, support_expiry, eol_date, and the place. Dates are `YYYY-MM-DD`. Leave out what the file does not say; never guess. Unknown fields are rejected.
- The place: pass `place_id` (for example `DIFC-GA-GV4-L1-R02`) when the file has one; otherwise pass the names or codes it has as `site`, `location`, `building`, `floor`, `room` and `rack` (`location` is the level between site and building). Only a place that already exists is used: never invent one, never guess a missing level. A level the file leaves out is filled only when the level above has exactly one choice.
- `evidence.quote` must be copied verbatim from the file (one row or line) and `location` says where (`sheet Core row 12`, `page 3`). `name`, `serial` and `mgmt_ip` must appear in the file as written; a serial or IP that does not is dropped, a name that does not rejects the record.
- Existing devices are matched by serial, management IP or name: you get an update with only the differences, or "unchanged".

The result lists `batch_id`, counts, `rejected` (with the reason), `warnings` and `missing_places` (each place the file names that does not exist: `path`, the first missing `level`, and the `records` that named it; those devices are staged without a place). Report `missing_places` to the engineer, who adds them under Inventory -> Places. Report the rest as pending requests, never as devices added.

~~~json
{
    "thoughts": ["Row 12 of the sheet is a switch with a serial and an address."],
    "headline": "Staging devices from the spreadsheet",
    "tool_name": "inventory_stage",
    "tool_args": {
        "file": "/eagent/usr/workdir/switches.xlsx",
        "records": [
            {
                "fields": {"name": "sw-jkt-01", "vendor": "cisco", "platform": "iosxe", "serial": "FOC1234ABCD", "mgmt_ip": "10.1.0.11"},
                "evidence": {"quote": "sw-jkt-01 Cisco C9300 FOC1234ABCD 10.1.0.11", "location": "sheet Core row 12"},
                "confidence": 0.9
            }
        ]
    }
}
~~~
