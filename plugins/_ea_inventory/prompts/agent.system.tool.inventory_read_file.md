### inventory_read_file

Read a file the user attached (CSV, Excel, PDF, Word, or a text/config file) as a **redacted** view, to map it to inventory records. Passwords, community strings, keys and hashes are already replaced by `[REDACTED:<type>]`; never ask for or repeat them. Only the user's own files can be read. Scanned PDFs without a text layer cannot be read.

Args: `path` (as given in the attachment), `sheet` (Excel only, default the first with data), `offset` (default 0), `limit` (default 100, max 500). Tables come back as a TSV block: a header line, then rows numbered from 1 (`offset` and `limit` count rows). Text, config and PDF come back as lines, with `[page N]` markers for PDFs (`offset` and `limit` count lines). The result ends with JSON: `source_id`, `kind`, `total_rows` or `total_lines`, `pages`, `sheets`, `redactions`, `next_offset` (null when done). Keep calling with `next_offset` to read on. Reading changes nothing; use `inventory_stage` to propose records.

~~~json
{
    "thoughts": ["The user attached switches.xlsx; I need to see its columns."],
    "headline": "Reading the attached spreadsheet",
    "tool_name": "inventory_read_file",
    "tool_args": {
        "path": "switches.xlsx",
        "offset": 0,
        "limit": 100
    }
}
~~~
