# Kokoro TTS Plugin DOX

## Purpose

- Own built-in Kokoro text-to-speech integration and browser TTS fallback coordination.

## Ownership

- `api/` owns synthesize and status endpoints.
- `helpers/` owns runtime and migration behavior.
- `hooks.py` owns provider registration/lifecycle behavior.
- `webui/` owns settings and speech UI integration.
- `default_config.yaml`, `plugin.yaml`, and `README.md` own defaults, metadata, and behavior notes.

## Local Contracts

- Keep Kokoro dependencies on Docker/bootstrap paths, not opportunistic runtime installs.
- The model is loaded from the Hugging Face cache and never downloaded unless the deployment set `HF_HUB_OFFLINE=0` (`helpers/runtime._refuse_to_download`): `KPipeline` fetches a missing model through the hub. `tests/test_nothing_is_fetched_at_runtime.py` covers this.
- Preserve browser-native fallback when the plugin is disabled or unavailable.
- Do not expose generated speech artifacts outside intended response paths.
- Preserve `voice` and `speed` configuration compatibility; weighted blends stay in memory and use positive finite weights.

## Work Guidance

- Coordinate status endpoint behavior with frontend fallback decisions.

## Verification

- Smoke-test status, synthesis, configured voice/speed, and disabled fallback behavior after changes.

## Child DOX Index

No child DOX files.
