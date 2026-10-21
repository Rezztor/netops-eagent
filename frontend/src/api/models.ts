/**
 * Model configuration and provider credentials.
 *
 * This is the surface behind "administrators configure everything through the
 * interface": which model answers, which provider it comes from, and whether
 * that provider has a usable key.
 */

import { apiRequest } from "./client";

export interface ModelSpec {
  provider: string;
  name: string;
  api_base?: string;
  ctx_length?: number;
  ctx_length_manual?: boolean;
  ctx_history?: number;
  vision?: boolean;
  [key: string]: unknown;
}

export interface ProviderOption {
  value: string;
  label: string;
}

export interface ModelPreset {
  name: string;
  chat?: ModelSpec;
  utility?: ModelSpec;
  embedding?: ModelSpec;
  [key: string]: unknown;
}

export interface ModelConfig {
  config: {
    chat_model?: ModelSpec;
    utility_model?: ModelSpec;
    embedding_model?: ModelSpec;
    model_preset?: string;
    allow_chat_override?: boolean;
  };
  /** provider -> whether a key resolves. Presence only; never the value. */
  api_key_status: Record<string, boolean>;
  chat_providers: ProviderOption[];
  embedding_providers: ProviderOption[];
  presets: ModelPreset[];
  selected_preset: string;
  configured_preset: string;
  model_configured: boolean;
  model_configured_label: string;
}

export function getModelConfig() {
  return apiRequest<ModelConfig>("plugins/_model_config/model_config_get", { body: {} });
}

/**
 * Write the model configuration.
 *
 * The endpoint has existed and been reachable since the fork; nothing in the
 * interface called it, which is why models could be viewed and swapped between
 * presets but never edited. Anything not expressible as a preset -- a different
 * embedding provider, a model the presets do not list -- had to be changed by
 * editing usr/settings.json and restarting.
 *
 * An `api_key` on a section is moved into usr/.env as `API_KEY_<PROVIDER>` by the
 * server and stripped before the config is saved, so a key never lands in
 * settings.json. Sending the placeholder leaves the stored key untouched, which
 * is how a form can round-trip without knowing the secret it is displaying.
 */
export function setModelConfig(
  config: ModelConfig["config"],
  options: { presetName?: string; projectName?: string; agentProfile?: string } = {},
) {
  return apiRequest<{ ok?: boolean; error?: string }>("plugins/_model_config/model_config_set", {
    body: {
      config,
      ...(options.presetName !== undefined ? { preset_name: options.presetName } : {}),
      ...(options.projectName !== undefined ? { project_name: options.projectName } : {}),
      ...(options.agentProfile !== undefined ? { agent_profile: options.agentProfile } : {}),
    },
  });
}

/**
 * A catalogue entry as the provider (or LiteLLM fallback) reports it.
 *
 * `ctx_length` is present only when the backend could read a context window
 * from the provider payload or the LiteLLM registry. Unknown windows are
 * omitted rather than guessed, so the editor can show "unknown" instead of a
 * silent 128k.
 */
export interface ModelSearchResult {
  name: string;
  ctx_length?: number;
}

/**
 * Ask a provider what models it offers.
 *
 * Typing a model name from memory is how you end up with "Invalid model ID" and
 * no idea whether the provider, the prefix or the name is wrong -- so the form
 * offers what the provider actually reports. Providers that cannot be queried
 * return nothing and the field stays free text rather than blocking the save.
 *
 * Returns the reason as well as the models, because this endpoint can answer
 * with both: `{models: [...], error: "[Errno -2] Name or service not known"}` is
 * a registry fallback plus the reason the provider itself could not be reached.
 * This used to return a bare array and drop the message on the floor, so
 * pointing the form at a provider whose server was not running looked exactly
 * like pointing it at one that was -- a list of models appeared, none of which
 * the deployment could actually use.
 */
export interface ModelSearchOutcome {
  models: ModelSearchResult[];
  /** Why the provider could not be asked, when it could not be. */
  error: string | null;
}

export async function searchModels(
  provider: string,
  options: { query?: string; apiBase?: string; modelType?: string } = {},
): Promise<ModelSearchOutcome> {
  try {
    const result = await apiRequest<{ models?: unknown[]; error?: string }>(
      "plugins/_model_config/model_search",
      {
        body: {
          provider,
          query: options.query ?? "",
          api_base: options.apiBase ?? "",
          model_type: options.modelType ?? "chat",
        },
      },
    );
    const models = (result.models ?? []).flatMap((entry) => {
      const parsed = parseSearchResult(entry);
      return parsed ? [parsed] : [];
    });
    const stated = typeof result.error === "string" ? result.error.trim() : "";
    return { models, error: stated || null };
  } catch (caught) {
    // A provider without a catalogue endpoint is not an error that should block
    // anything -- the field stays free text. It is still reported, because
    // "no suggestions" and "could not ask" look identical otherwise.
    return {
      models: [],
      error: caught instanceof Error ? caught.message : String(caught),
    };
  }
}

function parseSearchResult(entry: unknown): ModelSearchResult | null {
  if (typeof entry === "string") {
    const name = entry.trim();
    return name ? { name } : null;
  }
  if (!entry || typeof entry !== "object") return null;
  const record = entry as { name?: unknown; ctx_length?: unknown };
  const name = typeof record.name === "string" ? record.name.trim() : "";
  if (!name) return null;
  const ctx = Number(record.ctx_length);
  return Number.isFinite(ctx) && ctx > 0 ? { name, ctx_length: ctx } : { name };
}

/** Apply a named preset as the instance default. */
export function selectPreset(name: string) {
  return apiRequest<{ ok?: boolean; error?: string }>("plugins/_model_config/model_presets", {
    body: { action: "select", name },
  });
}

/**
 * Where a provider's key comes from.
 *
 * `file` means usr/.env, which is written by this interface and **overrides**
 * the process environment — `load_dotenv` is called with `override=True`. So a
 * key set here silently wins over one the deployment injects, and an
 * instance can keep using a stale key after the Secret has been rotated. Shown
 * rather than inferred, because presence alone cannot distinguish the two.
 */
export type KeyOrigin = "file" | "environment" | "unset";

/** Placeholder the backend substitutes for a stored key; never the real value. */
export const API_KEY_PLACEHOLDER = "************";

export interface ProviderKeys {
  keys: Record<string, string>;
  origins: Record<string, KeyOrigin>;
  chatProviders: ProviderOption[];
  embeddingProviders: ProviderOption[];
}

export async function getProviderKeys(): Promise<ProviderKeys> {
  const result = await apiRequest<{
    settings: { api_keys?: Record<string, string> };
    additional: {
      api_key_origins?: Record<string, KeyOrigin>;
      chat_providers?: ProviderOption[];
      embedding_providers?: ProviderOption[];
    };
  }>("settings_get");
  return {
    keys: result.settings.api_keys ?? {},
    origins: result.additional.api_key_origins ?? {},
    chatProviders: result.additional.chat_providers ?? [],
    embeddingProviders: result.additional.embedding_providers ?? [],
  };
}

/**
 * Set one provider's key.
 *
 * Only the provider being changed is sent. `_write_sensitive_settings` skips any
 * value equal to the placeholder, so sending the masked form back is a no-op —
 * but sending an empty string is not, and would clear the key.
 */
export function setProviderKey(provider: string, key: string) {
  return apiRequest<unknown>("settings_set", {
    body: { settings: { api_keys: { [provider]: key } } },
  });
}
