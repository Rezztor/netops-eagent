# SIEM Forwarding

Sends a copy of this instance's audit trail to an external Syslog server or SIEM
collector, formatted as CEF (Common Event Format) and sent over UDP — port 514
by default.

## What is forwarded

Every event the audit system emits: sign-in and sign-out, password changes and
resets, account create/update/delete, settings changes, plugin toggles, tool
execution, mail sent. This is the same stream the Activity screen shows.

General application log output (info/warning/error lines) is **not** forwarded.
CEF is a structured-event format and unstructured log text has no faithful
representation in it. To collect everything, point a log shipper at this
container's stdout — every audit event is written there too, prefixed
`REZZTOR_AUDIT`.

## Telling a real record from a forged one

Anything the model or a tool prints to stdout can be shaped like a `REZZTOR_AUDIT`
line. Every record therefore carries `boot` (random per process start), `seq`
(rises by one per record within a boot) and `mac` (HMAC-SHA256 over the record's
canonical JSON, keys sorted, no spaces, without `mac`). The key is
`HMAC-SHA256(FLASK_SECRET_KEY, "eagent audit record")`. A collector that holds
it rejects a line with a missing or wrong `mac` and flags a gap in `seq` within
one `boot`; a new `boot` means the process restarted. To check a captured log:
`audit.verify(lines)` in `plugins/_audit/helpers/audit.py` returns the problems found.

The Activity tail and this forwarder read records from memory, not from stdout,
so text printed to stdout cannot reach either of them. Only the stdout stream
needs the check above.

## Setting it up

Settings → SIEM forwarding (requires `plugins.manage`; the card is not shown at
all while the plugin is disabled).

Set the collector host and port, turn **Forward audit events** on, save, then
use **Send test event** to confirm the packet leaves this host.

## What it does and does not promise

UDP has no delivery acknowledgement. A successful send means the packet left
this machine — not that the collector received, parsed, or accepted it.

More importantly: this is a *convenience* channel. The retained audit record is
the stdout stream (ADR 0005), which is trustworthy
precisely because the application cannot reach it after it is written. Events
forwarded from inside the application can only ever be as trustworthy as the
application. A deployment that needs a record it can rely on during an incident
should ship stdout to the SIEM, with or without this plugin.
