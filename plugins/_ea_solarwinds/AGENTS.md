# SolarWinds Plugin DOX

## Purpose

- Own reading live SolarWinds data into the agent's answers. Read-only.

## Ownership

- `helpers/client.py` owns the one HTTP call this plugin can make (`/Query`)
  and the only exception hierarchy that call raises.
- `helpers/capabilities.py` owns every SWQL template and their bound
  parameters. The only module that constructs SWQL text.
- `helpers/bulk.py` owns the inventory's all-or-nothing node fetch
  (`fetch_nodes`), built only on the internal capabilities below.
- `helpers/policy.py` owns the per-conversation rate cap.
- `helpers/config.py` owns settings resolution, coercion, and whether the
  plugin is configured.
- `tools/solarwinds_lookup.py` owns the one tool surface, and translating
  every exception the helpers raise into a message the agent can act on.
- `api/solarwinds_config.py` owns the Settings card's get/save contract.
- `skills/solarwinds-troubleshooting/SKILL.md` owns domain knowledge: which
  capability answers which kind of question, not a restatement of arguments.
- The interface lives in `frontend/src/routes/SettingsScreen.tsx`'s
  `SolarWindsCard`, not here.

## Local Contracts

- `helpers/client.py` never follows a redirect (`allow_redirects=False`): a 30x would re-send the credentials, and
  the POST, to wherever it points. A 3xx answer is a `ConnectionFailed`.
- `solarwinds_lookup` needs `tools.network` (`helpers/tool_permissions.py`), like the other network tools: the standard user role has no network device access.
- A `solarwinds_lookup` result is scrubbed by `extensions/python/tool_execute_after/_20_redact_network_output.py`
  like a device read: `device_config` returns archived configurations, secrets and all.
- `device_config` returns at most `MAX_CONFIGS` (5) archived configurations per call, each cut to `CONFIG_LINES` with the count of lines left out; `before` (a `DownloadTime`) pages to older ones. Still redacted by the extension above.
- `helpers/client.py` has no method that can reach
  `/Invoke/{entity}/{verb}`. Never add one. If a write capability is ever
  wanted, it is a new, separately reviewed module — not a branch added here.
  `tests/test_solarwinds_client.py` asserts the absence directly.
- No function in `helpers/capabilities.py` builds SWQL by formatting an
  argument's *value* into the query string. Every value crosses into SWIS
  through its bound-parameter `parameters` object. An argument's *presence*
  may choose between two fixed literal templates; its *value* never touches
  the template text. `tests/test_solarwinds_capabilities.py` asserts this for
  every capability with an injection-shaped argument.
- Internal capabilities (`nodes_page`, `node_serials_page`,
  `node_serials_ncm_page`) are hidden from `names()`/`describe()` and refused by
  `build()` unless `internal=True`, which only `helpers/bulk.py` passes, so the
  agent cannot request bulk reads. `nodes_page`'s `omit` may only name columns
  from `NODE_OPTIONAL_COLUMNS`; no caller value reaches the text.
  `tests/test_bulk.py` runs `fetch_nodes` against `tests/fake_swis.py` and
  asserts every received query is a known template.
- The password is never stored in this plugin's own config file. It goes
  through `helpers/dotenv.py`'s `KEY_SOLARWINDS_PASSWORD`, the same
  indirection `KEY_SMTP_PASSWORD` uses, and the API handler never returns it
  to the client — only whether it is set.
- A query against an entity SWIS doesn't recognise raises
  `CapabilityUnavailable`, not a generic failure. The tool turns that into
  "isn't enabled on this SolarWinds instance," never the raw SWIS error text.
  This plugin serves multiple customer organisations with different
  SolarWinds modules licensed — a missing module is an expected outcome, not
  a bug, and the capability list is not narrowed to avoid it.
- This plugin's own endpoint is gated on `plugins.manage` in
  `helpers/api_policy.py`, keyed by full dispatch path
  (`plugins/_ea_solarwinds/solarwinds_config`).
- Runs in the application process, never on the code-execution runner. The
  runner's egress is deliberately tight; a SolarWinds service account has no
  reason to be reachable from agent-written code.

## Work Guidance

- Adding a capability means adding one entry to `_CAPABILITIES` in
  `helpers/capabilities.py`, checked against a real SWQL entity first — not
  inferred from what the module is supposed to expose.
- The exact shape of a SWIS "entity not found" error is provisional
  (`_ENTITY_NOT_FOUND` in `helpers/client.py`) — sourced from a real forum
  thread, not yet exercised against a live instance from this codebase. If a
  live instance phrases it differently, extend the pattern rather than
  replace the design.

## Verification

- `pytest plugins/_ea_solarwinds -q`
- Live, once a real (or disposable test) instance and a read-only service
  account are available: Settings → SolarWinds, enter host/username/password,
  save; ask the agent a status question; confirm `solarwinds_lookup` is
  called with a capability name, never a raw query; ask about a module the
  test instance doesn't have and confirm the plain "not enabled" message,
  never a raw SWIS error, reaches the transcript.

## Child DOX Index

No child DOX files.
