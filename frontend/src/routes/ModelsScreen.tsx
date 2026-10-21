/**
 * Models and provider credentials.
 *
 * The surface behind "administrators configure everything through the
 * interface". Two things it is careful about:
 *
 * Key values are never displayed. The backend masks them and this screen shows
 * only whether one is configured and where it came from.
 *
 * Where it came from is shown because the two sources silently disagree. A key
 * set here is written to usr/.env, which lives on the persistent volume and
 * **overrides** the process environment — `load_dotenv` runs with
 * `override=True`. So setting a key here beats a variable the deployment injects, and an
 * instance can go on using a stale key after the Secret has been rotated with
 * nothing on screen to say so.
 */

import { useEffect, useRef, useState } from "react";

import {
  API_KEY_PLACEHOLDER,
  getModelConfig,
  getProviderKeys,
  searchModels,
  selectPreset,
  setModelConfig,
  setProviderKey,
  type KeyOrigin,
  type ModelConfig,
  type ModelSearchResult,
  type ModelSpec,
  type ProviderKeys,
} from "@/api/models";
import {
  Button,
  Card,
  CenteredMessage,
  LoadFailed,
  Mono,
  Row,
  ScopeNote,
  Select,
  Table,
  Tag,
  TextInput,
  Toggle,
  useConfirm,
  useToast,
} from "@/components";
import { useAsync } from "@/lib/useAsync";
import shell from "./shell.module.css";

/**
 * Reject a "successful" HTTP call that carries a business error.
 *
 * `model_config_set` and `model_presets` answer 200 with `{ok: false, error}`
 * when they refuse a change -- an unknown provider, a preset that does not
 * exist. Awaiting the request alone treated those as success and showed a
 * confirmation while nothing had changed; this turns them back into errors the
 * toast can surface.
 */
function unwrap(result: unknown): void {
  if (result && typeof result === "object") {
    const record = result as { ok?: boolean; error?: string };
    if (record.error) throw new Error(record.error);
    if (record.ok === false) throw new Error("The server rejected the change.");
  }
}

export function ModelsScreen() {
  const config = useAsync<ModelConfig>(() => getModelConfig(), []);
  const keys = useAsync<ProviderKeys>(() => getProviderKeys(), []);
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  if (config.state.status === "loading" || keys.state.status === "loading") {
    return <CenteredMessage title="Loading model configuration…" />;
  }
  if (config.state.status === "error") {
    return (
      <LoadFailed what="model configuration" error={config.state.error} onRetry={config.reload} />
    );
  }
  if (keys.state.status === "error") {
    return (
      <LoadFailed what="provider credentials" error={keys.state.error} onRetry={keys.reload} />
    );
  }

  const data = config.state.data;
  const credentials = keys.state.data;

  async function run(action: () => Promise<unknown>, success: string) {
    setBusy(true);
    try {
      unwrap(await action());
      toast.success(success);
      config.reload();
      keys.reload();
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  const environmentKeys = Object.entries(credentials.origins).filter(
    ([, origin]) => origin === "environment",
  );

  return (
    <div className={shell.stack}>
      <ScopeNote>
        <div>
          <strong>Administrator only.</strong> These settings decide which model
          answers for every user on this instance.
        </div>
      </ScopeNote>

      <Card
        title="Active models"
        description={
          data.model_configured
            ? `Currently ${data.model_configured_label}`
            : "No chat model is configured; the agent cannot answer."
        }
        danger={!data.model_configured}
      >
        <ModelRow label="Chat" help="Answers the user." spec={data.config.chat_model} keys={credentials} />
        <ModelRow label="Utility" help="Internal work: naming, summarising, routing." spec={data.config.utility_model} keys={credentials} />
        <ModelRow label="Embedding" help="Memory and knowledge search." spec={data.config.embedding_model} keys={credentials} />
      </Card>

      <ModelEditor
        data={data}
        keys={credentials}
        busy={busy}
        onSave={(next) => void run(() => setModelConfig(next), "Model updated.")}
      />

      <PresetsCard
        data={data}
        busy={busy}
        onSelect={(name) => void run(() => selectPreset(name), `Preset "${name}" applied.`)}
      />

      {environmentKeys.length > 0 && (
        <ScopeNote tone="warn">
          <div>
            <strong>
              {environmentKeys.length} key{environmentKeys.length === 1 ? "" : "s"} come
              {environmentKeys.length === 1 ? "s" : ""} from the environment.
            </strong>{" "}
            Setting one below saves it on this instance, and the saved key wins
            over the environment: after that, changing the injected value has no
            effect until the key here is cleared.
          </div>
        </ScopeNote>
      )}

      <ProviderKeysCard
        credentials={credentials}
        status={data.api_key_status}
        busy={busy}
        onSet={(provider, key) =>
          run(() => setProviderKey(provider, key), `Key for ${provider} saved.`)
        }
        onClear={(provider) =>
          run(() => setProviderKey(provider, ""), `Key for ${provider} cleared.`)
        }
      />
    </div>
  );
}

/* ---------- presets ---------- */

/**
 * Select a preset and show what it actually contains.
 *
 * The old control was a bare dropdown: it named presets but never said which
 * models each one bundled, so choosing between them meant remembering. It also
 * hid the split the backend already reports -- `selected_preset` is what the
 * dropdown was set to, `configured_preset` is what the running config resolves
 * to, and they diverge when a slot was hand-edited afterwards. Now the contents
 * are on screen and reconciling the two is one button.
 */
function PresetsCard({
  data,
  busy,
  onSelect,
}: {
  data: ModelConfig;
  busy: boolean;
  onSelect: (name: string) => void;
}) {
  const [name, setName] = useState(data.selected_preset);
  const preset = data.presets.find((entry) => entry.name === name);
  const diverged = data.selected_preset !== data.configured_preset;

  return (
    <Card title="Presets" description="A named bundle of chat, utility and embedding models.">
      <Row
        label="Preset"
        help={
          diverged
            ? `Selected "${data.selected_preset}" but the running config resolves to "${data.configured_preset}" -- a slot was edited after the preset was applied.`
            : "Applies to every new conversation."
        }
      >
        <Select
          value={name}
          disabled={busy || data.presets.length === 0}
          onChange={(event) => setName(event.target.value)}
          aria-label="Preset"
        >
          {data.presets.map((entry) => (
            <option key={entry.name} value={entry.name}>
              {entry.name}
            </option>
          ))}
        </Select>
      </Row>

      {preset && (
        <div style={{ padding: "0 0 var(--space-2)" }}>
          <Table>
            <thead>
              <tr>
                <th>Slot</th>
                <th>Provider</th>
                <th>Model</th>
              </tr>
            </thead>
            <tbody>
              <PresetSlotRow label="Chat" spec={preset.chat} />
              <PresetSlotRow label="Utility" spec={preset.utility} />
              <PresetSlotRow label="Embedding" spec={preset.embedding} />
            </tbody>
          </Table>
        </div>
      )}

      <Row
        label=""
        help={
          name === data.selected_preset && !diverged
            ? "This preset is already applied."
            : ""
        }
      >
        <Button
          variant="primary"
          disabled={busy || data.presets.length === 0 || (name === data.selected_preset && !diverged)}
          onClick={() => onSelect(name)}
        >
          {busy ? "Applying…" : diverged && name === data.configured_preset ? "Reconcile to preset" : "Apply preset"}
        </Button>
      </Row>
    </Card>
  );
}

function PresetSlotRow({ label, spec }: { label: string; spec: ModelSpec | undefined }) {
  return (
    <tr>
      <td>{label}</td>
      <td style={{ color: "var(--ink-3)" }}>{spec?.provider || "—"}</td>
      <td>{spec?.name ? <Mono>{spec.name}</Mono> : "—"}</td>
    </tr>
  );
}

/* ---------- editing a slot ---------- */

const SLOTS = [
  { key: "chat_model", label: "Chat", help: "Answers the user." },
  { key: "utility_model", label: "Utility", help: "Internal work: naming, summarising, routing." },
  { key: "embedding_model", label: "Embedding", help: "Memory and knowledge search." },
] as const;

type SlotKey = (typeof SLOTS)[number]["key"];

const CUSTOM_MODEL = "__custom__";
const MODEL_FILTER_THRESHOLD = 50;

/**
 * Change which model fills one slot.
 *
 * This exists because `model_config_set` had been reachable since the fork with
 * nothing calling it: models could be viewed, and swapped between presets, but
 * not edited. Anything a preset did not cover -- a different embedding provider,
 * a model the presets do not list -- meant editing usr/settings.json by hand and
 * restarting.
 *
 * Saving one slot sends the whole config back, because the endpoint replaces it
 * rather than merging. Sending a partial config would silently blank the slots
 * left out.
 */
function ModelEditor({
  data,
  keys,
  busy,
  onSave,
}: {
  data: ModelConfig;
  keys: ProviderKeys;
  busy: boolean;
  onSave: (config: ModelConfig["config"]) => void;
}) {
  const toast = useToast();
  const [slot, setSlot] = useState<SlotKey>("chat_model");
  const current = data.config[slot] ?? { provider: "", name: "" };

  const [provider, setProvider] = useState(current.provider ?? "");
  const [name, setName] = useState(current.name ?? "");
  const [ctxLength, setCtxLength] = useState(String(current.ctx_length ?? ""));
  const [ctxManual, setCtxManual] = useState(Boolean(current.ctx_length_manual));
  const [apiBase, setApiBase] = useState(current.api_base ?? "");
  const [vision, setVision] = useState(Boolean(current.vision));
  const [apiKey, setApiKey] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(
    Boolean(current.api_base) || Boolean(current.vision),
  );
  const [suggestions, setSuggestions] = useState<ModelSearchResult[]>([]);
  // Why the provider could not be asked, when it could not be. The endpoint
  // answers with a registry fallback *and* a reason -- models plus
  // "[Errno -2] Name or service not known" -- and this screen showed the list
  // and dropped the reason, so an unreachable provider looked like a reachable
  // one offering models the deployment cannot actually use.
  const [searchError, setSearchError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [customModel, setCustomModel] = useState(false);
  const [searching, setSearching] = useState(false);
  const [testing, setTesting] = useState(false);
  // Bumped to re-run the listing effect when deps otherwise look unchanged
  // (same provider/base/type across slots, or after a manual "Test provider").
  const [refresh, setRefresh] = useState(0);
  // Bumped on every query so a slow response for an old keystroke cannot
  // overwrite the suggestions for a newer one.
  const searchSeq = useRef(0);

  // Embedding models come from a different provider list: not every chat
  // provider can embed, and offering one that cannot is how you get a config
  // that saves cleanly and fails at first recall.
  const providers =
    slot === "embedding_model" ? data.embedding_providers : data.chat_providers;
  const modelType = slot === "embedding_model" ? "embedding" : "chat";
  const hasKey = Boolean(keys.origins[provider]) || Boolean(data.api_key_status[provider]);

  // Full catalogue for the selected provider. Filtering is client-side so
  // typing a current model id cannot hide the rest of the list.
  useEffect(() => {
    const seq = ++searchSeq.current;
    if (!provider) {
      setSuggestions([]);
      setSearchError(null);
      setSearching(false);
      return;
    }
    setSearching(true);
    const timer = setTimeout(() => {
      void searchModels(provider, {
        apiBase: apiBase.trim(),
        modelType,
      }).then((outcome) => {
        if (searchSeq.current === seq) {
          setSuggestions(outcome.models);
          setSearchError(outcome.error);
          setSearching(false);
        }
      });
    }, 300);
    return () => clearTimeout(timer);
  }, [provider, apiBase, modelType, hasKey, refresh]);

  function selectSlot(next: SlotKey) {
    const spec = data.config[next] ?? { provider: "", name: "" };
    setSlot(next);
    setProvider(spec.provider ?? "");
    setName(spec.name ?? "");
    setCtxLength(String(spec.ctx_length ?? ""));
    setCtxManual(Boolean(spec.ctx_length_manual));
    setApiBase(spec.api_base ?? "");
    setVision(Boolean(spec.vision));
    setShowAdvanced(Boolean(spec.api_base) || Boolean(spec.vision));
    setApiKey("");
    setSuggestions([]);
    setSearchError(null);
    setFilter("");
    setCustomModel(false);
    setRefresh((n) => n + 1);
  }

  async function testProvider() {
    if (!provider) return;
    setTesting(true);
    setRefresh((n) => n + 1);
    try {
      const outcome = await searchModels(provider, {
        apiBase: apiBase.trim(),
        modelType,
      });
      if (outcome.error) {
        // The reason, rather than "returned no models" -- which was also what
        // it said when the provider answered perfectly well and simply has no
        // catalogue. Those are different problems and only one is yours to fix.
        toast.error(`${provider} could not be reached: ${outcome.error}`);
      } else if (outcome.models.length > 0) {
        toast.success(`${provider} is reachable — it reported ${outcome.models.length} models.`);
      } else {
        toast.error(
          `${provider} returned no models. Check the key and base URL, or note that this provider does not expose a catalogue.`,
        );
      }
    } finally {
      setTesting(false);
    }
  }

  const selected = suggestions.find((entry) => entry.name === name);
  const detectedCtx = selected?.ctx_length;
  const filteredSuggestions = filter.trim()
    ? suggestions.filter((entry) =>
        entry.name.toLowerCase().includes(filter.trim().toLowerCase()),
      )
    : suggestions;

  function save() {
    const spec: ModelSpec = { ...current, provider: provider.trim(), name: name.trim() };
    if (slot === "embedding_model") {
      delete spec.ctx_length;
      delete spec.ctx_length_manual;
      delete spec.vision;
    } else {
      spec.vision = vision;
      if (ctxManual) {
        spec.ctx_length_manual = true;
        if (ctxLength.trim()) spec.ctx_length = Number(ctxLength);
        else delete spec.ctx_length;
      } else {
        delete spec.ctx_length_manual;
        if (detectedCtx) spec.ctx_length = detectedCtx;
        // else keep current.ctx_length from the spread — empty list must not wipe it
      }
    }
    if (apiBase.trim()) spec.api_base = apiBase.trim();
    else delete spec.api_base;
    // Omitted when blank: the server treats a present key as an instruction to
    // write one, and we do not want to overwrite a stored key with an empty
    // string.
    if (apiKey.trim()) spec.api_key = apiKey.trim();

    onSave({ ...data.config, [slot]: spec });
    setApiKey("");
  }

  const autoCtx = detectedCtx ?? current.ctx_length;
  // A manual value above what the provider reports is accepted by the form and
  // then fails every long request; say so while it can still be changed.
  const ctxTooLong = ctxManual && detectedCtx !== undefined && Number(ctxLength) > detectedCtx;
  const changed =
    provider.trim() !== (current.provider ?? "") ||
    name.trim() !== (current.name ?? "") ||
    ctxManual !== Boolean(current.ctx_length_manual) ||
    (ctxManual
      ? ctxLength.trim() !== String(current.ctx_length ?? "")
      : Number(autoCtx ?? 0) !== Number(current.ctx_length ?? 0)) ||
    apiBase.trim() !== (current.api_base ?? "") ||
    (slot !== "embedding_model" && vision !== Boolean(current.vision)) ||
    apiKey.trim() !== "";

  return (
    <Card
      title="Change a model"
      description="Set the provider and model for one slot. Applies to every user on this instance."
    >
      <Row label="Slot" help="Which job this model does.">
        <Select value={slot} onChange={(event) => selectSlot(event.target.value as SlotKey)}>
          {SLOTS.map((entry) => (
            <option key={entry.key} value={entry.key}>
              {entry.label} — {entry.help}
            </option>
          ))}
        </Select>
      </Row>

      <Row
        label="Provider"
        help={
          slot === "embedding_model"
            ? "Only providers that can produce embeddings are listed."
            : "Where the model is served from."
        }
      >
        <div style={{ display: "flex", gap: "var(--space-2)", alignItems: "center" }}>
          <Select
            value={provider}
            onChange={(event) => {
              setProvider(event.target.value);
              setSuggestions([]);
              setSearchError(null);
              setFilter("");
              setCustomModel(false);
            }}
          >
            <option value="">Select a provider…</option>
            {providers.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </Select>
          <Button
            disabled={!provider || testing}
            onClick={() => void testProvider()}
            title="Ask the provider for its model list to confirm the key and base URL work."
          >
            {testing ? "Testing…" : "Test provider"}
          </Button>
        </div>
      </Row>

      <Row
        label="Model"
        help={
          searching
            ? "Asking the provider what it offers…"
            : searchError
              ? (
                  <span style={{ color: "var(--crit)" }}>
                    Could not ask {provider}: {searchError}
                    {suggestions.length > 0
                      ? " — the names below are from the bundled registry, not from your provider."
                      : ""}
                  </span>
                )
              : suggestions.length > 0
                ? `${suggestions.length} models reported by this provider.`
                : "Exact model id. Prefixes differ by provider — openai/gpt-5.3 for OpenRouter, gpt-5.3 for OpenAI directly."
        }
      >
        {suggestions.length > 0 ? (
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)", alignItems: "stretch" }}>
            {suggestions.length > MODEL_FILTER_THRESHOLD && (
              <TextInput
                value={filter}
                placeholder="Filter models"
                aria-label="Filter models"
                onChange={(event) => setFilter(event.target.value)}
              />
            )}
            <Select
              value={customModel ? CUSTOM_MODEL : name}
              onChange={(event) => {
                const next = event.target.value;
                if (next === CUSTOM_MODEL) {
                  setCustomModel(true);
                  return;
                }
                setCustomModel(false);
                setName(next);
                if (!ctxManual) {
                  const found = suggestions.find((entry) => entry.name === next);
                  if (found?.ctx_length) setCtxLength(String(found.ctx_length));
                }
              }}
              aria-label="Model"
            >
              <option value="">Select a model…</option>
              {name &&
                !customModel &&
                !filteredSuggestions.some((entry) => entry.name === name) && (
                <option value={name}>{name}</option>
              )}
              {filteredSuggestions.map((entry) => (
                <option key={entry.name} value={entry.name}>
                  {entry.name}
                </option>
              ))}
              <option value={CUSTOM_MODEL}>Custom…</option>
            </Select>
            {customModel && (
              <TextInput
                value={name}
                placeholder="model id"
                aria-label="Custom model id"
                onChange={(event) => setName(event.target.value)}
              />
            )}
          </div>
        ) : (
          <TextInput
            value={name}
            placeholder="model id"
            onChange={(event) => setName(event.target.value)}
          />
        )}
      </Row>

      {slot !== "embedding_model" && (
        <Row
          label="Context length"
          help={
            ctxTooLong
              ? (
                <span style={{ color: "var(--warn)" }} role="alert">
                  More than the {detectedCtx.toLocaleString()} tokens {provider} reports for {name}. Requests longer
                  than the model accepts will fail; use {detectedCtx.toLocaleString()} or less.
                </span>
              )
              : ctxManual
              ? "Tokens the model accepts."
              : detectedCtx
                ? `${detectedCtx.toLocaleString()} tokens — reported for ${name}.`
                : current.ctx_length
                  ? `Unknown — using the saved value (${current.ctx_length.toLocaleString()}).`
                  : "Unknown — using 128,000 if nothing is saved."
          }
        >
          <div style={{ display: "flex", gap: "var(--space-2)", alignItems: "center" }}>
            {ctxManual ? (
              <TextInput
                value={ctxLength}
                inputMode="numeric"
                placeholder="200000"
                aria-label="Context length"
                onChange={(event) => setCtxLength(event.target.value)}
              />
            ) : (
              <span style={{ color: "var(--ink-3)", fontSize: "var(--text-xs)" }}>
                {(detectedCtx ?? current.ctx_length)?.toLocaleString() ?? "Unknown"}
              </span>
            )}
            <Toggle
              checked={ctxManual}
              onChange={(next) => {
                setCtxManual(next);
                if (next && !ctxLength.trim()) {
                  setCtxLength(String(detectedCtx ?? current.ctx_length ?? ""));
                }
              }}
              label="Set context length manually"
            />
          </div>
        </Row>
      )}

      <Row
        label="Advanced"
        help="A custom endpoint or, for chat models, whether the model accepts images."
      >
        <Button onClick={() => setShowAdvanced((current) => !current)}>
          {showAdvanced ? "Hide advanced" : "Show advanced"}
        </Button>
      </Row>

      {showAdvanced && (
        <>
          <Row
            label="API base URL"
            help="Override the provider's default endpoint — for a proxy or a self-hosted server. Leave blank for the default."
          >
            <TextInput
              value={apiBase}
              placeholder="https://…"
              onChange={(event) => setApiBase(event.target.value)}
            />
          </Row>
          {slot !== "embedding_model" && (
            <Row label="Vision" help="The model can be sent images as well as text.">
              <Toggle checked={vision} onChange={setVision} label="Model accepts images" />
            </Row>
          )}
        </>
      )}

      <Row
        label="API key"
        help={
          hasKey
            ? "This provider already has a key. Leave blank to keep it."
            : "No key is set for this provider. Without one the model cannot be called."
        }
      >
        <TextInput
          type="password"
          value={apiKey}
          placeholder={hasKey ? "unchanged" : "paste a key"}
          onChange={(event) => setApiKey(event.target.value)}
        />
      </Row>

      <Row label="" help={changed ? "" : "Nothing has changed yet."}>
        <Button
          variant="primary"
          // Only `busy`. Disabling this on the field checks meant that typing a
          // model id while the provider was still on "Select a provider…"
          // flipped `changed` true, removed the one hint on screen ("Nothing has
          // changed yet."), and left a dead button with nothing naming the
          // provider field.
          disabled={busy}
          onClick={() => {
            const missing = !provider.trim()
              ? "Choose a provider first."
              : !name.trim()
                ? "Enter a model id."
                : !changed
                  ? "Nothing has changed yet."
                  : null;
            if (missing) {
              toast.error(missing);
              return;
            }
            void save();
          }}
        >
          {busy ? "Saving…" : "Save model"}
        </Button>
      </Row>
    </Card>
  );
}

function ModelRow({
  label,
  help,
  spec,
  keys,
}: {
  label: string;
  help: string;
  spec: ModelSpec | undefined;
  keys: ProviderKeys;
}) {
  if (!spec) {
    return (
      <Row label={label} help={help}>
        <Tag tone="muted">not set</Tag>
      </Row>
    );
  }
  const origin = keys.origins[spec.provider];
  // A sentence-transformers model under the huggingface provider runs on
  // this machine through LocalSentenceTransformerWrapper (models.py): it
  // downloads once and never calls an API, so it needs no key. Judging it
  // by key presence alone reported the default embedding model as unusable
  // when it works -- which sends an operator hunting for a credential they
  // do not need, or worse, switching to a provider that cannot embed.
  const runsLocally =
    spec.provider === "huggingface" &&
    (spec.name ?? "").startsWith("sentence-transformers/");
  const usable = runsLocally || origin === "file" || origin === "environment";
  return (
    <Row
      label={label}
      help={
        <>
          {help}
          {!usable && (
            <>
              {" "}
              <strong>No key configured for {spec.provider}, so this model cannot be called.</strong>
            </>
          )}
        </>
      }
    >
      <div style={{ textAlign: "right" }}>
        <Mono>{spec.name}</Mono>
        <div style={{ color: "var(--ink-3)", fontSize: "var(--text-xs)" }}>
          {runsLocally ? `${spec.provider} · on this machine` : spec.provider}
          {spec.ctx_length ? ` · ${spec.ctx_length.toLocaleString()} ctx` : ""}
        </div>
      </div>
      <Tag tone={usable ? "ok" : "crit"}>
        {runsLocally ? "local" : usable ? "key set" : "no key"}
      </Tag>
    </Row>
  );
}

function originTag(origin: KeyOrigin | undefined) {
  if (origin === "file") return <Tag tone="ok">set here</Tag>;
  if (origin === "environment") return <Tag tone="accent">environment</Tag>;
  return <Tag tone="muted">not set</Tag>;
}

function ProviderKeysCard({
  credentials,
  status,
  busy,
  onSet,
  onClear,
}: {
  credentials: ProviderKeys;
  status: Record<string, boolean>;
  busy: boolean;
  onSet: (provider: string, key: string) => void;
  onClear: (provider: string) => void;
}) {
  const { confirm } = useConfirm();
  const [editing, setEditing] = useState<string | null>(null);
  const [value, setValue] = useState("");
  const [showAll, setShowAll] = useState(false);

  // Providers, configured first: an instance knows thirty-odd and uses two.
  const providers = [...credentials.chatProviders, ...credentials.embeddingProviders]
    .filter((provider, index, all) => all.findIndex((p) => p.value === provider.value) === index)
    .sort((a, b) => {
      const rank = (id: string) => (credentials.origins[id] === "unset" ? 1 : 0);
      return rank(a.value) - rank(b.value) || a.label.localeCompare(b.label);
    });
  const configured = providers.filter((p) => credentials.origins[p.value] !== "unset");
  const visible = showAll ? providers : configured;

  return (
    <Card
      title="Provider credentials"
      description="Values are never displayed. Only whether a key is set, and where it comes from."
      actions={
        <Button onClick={() => setShowAll((current) => !current)}>
          {showAll ? `Show configured only (${configured.length})` : `Show all ${providers.length}`}
        </Button>
      }
    >
      <Table>
        <thead>
          <tr>
            <th>Provider</th>
            <th>Key</th>
            <th>Reachable</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {visible.length === 0 && (
            <tr>
              <td colSpan={4} style={{ color: "var(--ink-3)" }}>
                No provider has a key configured. Show all providers to set one.
              </td>
            </tr>
          )}
          {visible.map((provider) => {
            const origin = credentials.origins[provider.value];
            const isEditing = editing === provider.value;
            return (
              <tr key={provider.value}>
                <td>
                  <strong>{provider.label}</strong>
                  <div style={{ color: "var(--ink-3)" }}>
                    <Mono>{provider.value}</Mono>
                  </div>
                </td>
                <td>
                  {isEditing ? (
                    <TextInput
                      type="password"
                      autoFocus
                      placeholder="Paste key…"
                      value={value}
                      aria-label={`API key for ${provider.label}`}
                      onChange={(event) => setValue(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Escape") setEditing(null);
                        if (event.key === "Enter" && value) {
                          onSet(provider.value, value);
                          setEditing(null);
                          setValue("");
                        }
                      }}
                    />
                  ) : (
                    originTag(origin)
                  )}
                </td>
                <td>
                  {status[provider.value] ? (
                    <Tag tone="ok">yes</Tag>
                  ) : (
                    <Tag tone="muted">—</Tag>
                  )}
                </td>
                <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                  {isEditing ? (
                    <>
                      <Button
                        variant="primary"
                        disabled={busy || !value}
                        onClick={() => {
                          onSet(provider.value, value);
                          setEditing(null);
                          setValue("");
                        }}
                      >
                        Save
                      </Button>{" "}
                      <Button onClick={() => { setEditing(null); setValue(""); }}>Cancel</Button>
                    </>
                  ) : (
                    <>
                      <Button
                        disabled={busy}
                        onClick={() => { setEditing(provider.value); setValue(""); }}
                      >
                        {origin === "unset" ? "Set key" : "Replace"}
                      </Button>{" "}
                      {origin === "file" && (
                        <Button
                          variant="danger"
                          disabled={busy}
                          onClick={async () => {
                            const ok = await confirm({
                              title: `Clear the ${provider.label} key?`,
                              body: "Models from this provider will stop working until a new key is set.",
                              danger: true,
                              confirmLabel: "Clear key",
                            });
                            if (ok) onClear(provider.value);
                          }}
                        >
                          Clear
                        </Button>
                      )}
                    </>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </Table>
      <p style={{ padding: "var(--space-3) 0 0", fontSize: "var(--text-xs)", color: "var(--ink-3)", margin: 0 }}>
        A stored key is shown to the backend as <Mono>{API_KEY_PLACEHOLDER}</Mono> and
        never sent to this page.
      </p>
    </Card>
  );
}
