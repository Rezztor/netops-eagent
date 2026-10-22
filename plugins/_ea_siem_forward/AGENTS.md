# _ea_siem_forward DOX

## Purpose

- Own the SIEM forwarding plugin: audit events, formatted as CEF, sent to an external Syslog server or SIEM collector over UDP.
- Keep the boundary explicit: this plugin **observes** the audit trail and can never change it.

## Ownership

- `helpers/config.py` — resolved settings. No credential: plain UDP syslog has no authentication, so unlike `_ea_mail` and `_ea_solarwinds` there is no `usr/.env` indirection here.
- `helpers/cef.py` — one audit record to one CEF line, and the syslog envelope around it.
- `helpers/sender.py` — the bounded queue, the background sender thread, the socket, and the counters.
- `extensions/python/startup_migration/` — registers the sink at boot.
- `api/siem_forward_config.py` — read/write the destination. `api/siem_forward_test.py` — send one synthetic event.

## Local Contracts

- **The sink is additive only.** It is registered through `plugins._audit.helpers.audit.register_sink`, which runs it *after* the record is masked, written to stdout and buffered, with its own shallow copy. It must never be reworked into an `@extension.extensible` hook on `emit()`: that decorator's `start` point would hand it the fields before masking and the stdout write, which is a mutation point on the audit trail (ADR 0028).
- **This is not the audit record.** ADR 0005 makes the stdout stream the retained trail precisely because the application cannot reach it afterwards. This plugin is a convenience channel for a deployment with no stdout log shipper; the Settings card says so to the operator, and nothing here may claim otherwise.
- **Forwarding never breaks the audited operation.** The sink formats and enqueues; it opens no socket, resolves no name, and blocks on nothing. Every layer swallows its own exceptions, inheriting `emit()`'s own promise.
- **A full queue drops the oldest and counts it.** Bounded memory and visible loss, never unbounded growth and never a blocked audit write. The counters are surfaced in Settings.
- **Two unrelated severities.** The syslog priority byte (`facility * 8 + 6`) is fixed; CEF's own 0–10 `Severity` header field carries the signal and is derived from the event *and its fields* — `outcome=failure` outranks the event's own category.
- **Header and extension escape different characters.** Header: `|` and `\`, never `=`. Extension: `=` and `\`, never `|`; newlines become literal `\n`/`\r`. Getting this backwards produces lines a collector silently mis-parses.
- Both API handlers check `config.is_enabled()`: dispatch resolves a handler by directory and keeps serving it after the plugin is disabled.

- Saving the destination and resetting the counters each emit an audit event (`siem_forward.config.update`, `siem_forward.stats.reset`) before they take effect.

## Work Guidance

- Add a new destination transport (TCP, TLS/RFC 5425) as a new branch in `sender.py` with its own config value, not by changing what UDP does.
- Nested values are deliberately dropped from the extension rather than stringified — CEF's extension is flat, and the audit record already carries a digest and a capped preview for anything large.
- `device_version` is read live from `helpers/git.py`, never stored, so the CEF header cannot claim a version this instance is not running.

## Verification

- `pytest plugins/_ea_siem_forward` — formatting, escaping, severity, config coercion, queue overflow, and the integration test that this plugin cannot alter the audit trail.
- `pytest tests/test_audit_logging.py` — the sink mechanism itself in `_audit`.
- Live: `nc -ul 514` (or `tcpdump -A -i any udp port 514`) on the collector side, then press **Send test event** in Settings.

## Child DOX Index

No child DOX files.
