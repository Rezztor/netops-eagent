// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "@/api/models";
import type { ModelConfig, ModelSearchOutcome, ProviderKeys } from "@/api/models";
import { ConfirmProvider, ToastProvider } from "@/components";
import { ModelsScreen } from "../ModelsScreen";

vi.mock("@/api/models", async (importOriginal) => ({
  ...(await importOriginal<typeof api>()),
  getModelConfig: vi.fn(),
  getProviderKeys: vi.fn(),
  searchModels: vi.fn(),
}));

const providers = [
  { value: "openrouter", label: "OpenRouter" },
  { value: "openai", label: "OpenAI" },
];
// Chat and Utility share ONE provider and base URL: the reported bug.
const config = {
  config: {
    chat_model: { provider: "openrouter", name: "a/one" },
    utility_model: { provider: "openrouter", name: "a/two" },
    embedding_model: { provider: "openai", name: "embed" },
  },
  api_key_status: { openrouter: true, openai: true },
  chat_providers: providers,
  embedding_providers: providers,
  presets: [],
  selected_preset: "",
  configured_preset: "",
  model_configured: true,
  model_configured_label: "a/one",
} as ModelConfig;
const keys: ProviderKeys = {
  keys: {},
  origins: { openrouter: "file", openai: "file" },
  chatProviders: providers,
  embeddingProviders: providers,
};
const outcome = (...names: string[]): ModelSearchOutcome => ({
  models: names.map((name) => ({ name })),
  error: null,
});

const searchModels = vi.mocked(api.searchModels);

// Let the loaders resolve (first call) and the 300 ms debounce fire.
const settle = async () => {
  await act(() => vi.advanceTimersByTimeAsync(0));
  await act(() => vi.advanceTimersByTimeAsync(300));
};
const modelSelect = () => screen.queryByLabelText("Model") as HTMLSelectElement | null;
const optionNames = () => Array.from(modelSelect()?.options ?? []).map((o) => o.value);
// Slot is the first select in the editor, Provider the second (no presets in the fixture).
const pick = (index: number, value: string) =>
  fireEvent.change(screen.getAllByRole("combobox")[index]!, { target: { value } });

beforeEach(() => {
  vi.useFakeTimers();
  vi.mocked(api.getModelConfig).mockResolvedValue(config);
  vi.mocked(api.getProviderKeys).mockResolvedValue(keys);
  searchModels.mockImplementation(async (provider) =>
    outcome(`${provider}/m1`, `${provider}/m2`),
  );
  render(
    <ToastProvider>
      <ConfirmProvider>
        <ModelsScreen />
      </ConfirmProvider>
    </ToastProvider>,
  );
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("ModelsScreen model list", () => {
  it("lists the provider's models for Chat", async () => {
    await settle();
    expect(optionNames()).toEqual(expect.arrayContaining(["openrouter/m1", "openrouter/m2"]));
  });

  it("refetches and keeps the list after switching Chat to Utility on the same provider", async () => {
    await settle();
    const before = searchModels.mock.calls.length;
    pick(0, "utility_model");
    await settle();
    expect(searchModels.mock.calls.length).toBeGreaterThan(before);
    expect(optionNames()).toEqual(expect.arrayContaining(["openrouter/m1", "openrouter/m2"]));
  });

  it("keeps a list when the provider is switched and switched back", async () => {
    await settle();
    pick(1, "openai");
    await settle();
    expect(optionNames()).toContain("openai/m1");
    pick(1, "openrouter");
    await settle();
    expect(optionNames()).toContain("openrouter/m1");
  });

  it("does not repopulate from a stale response after the provider is cleared", async () => {
    let resolve!: (value: ModelSearchOutcome) => void;
    await settle();
    searchModels.mockReturnValueOnce(new Promise((r) => (resolve = r)));
    pick(1, "openai"); // hangs: this response is still in flight when the provider clears
    await settle();
    pick(1, "");
    await settle();
    await act(async () => resolve(outcome("stale/m1")));
    expect(modelSelect()).toBeNull();
  });
});

describe("ModelsScreen context length", () => {
  it("warns when a manual context length is more than the provider reports", async () => {
    searchModels.mockImplementation(async () => ({ models: [{ name: "a/one", ctx_length: 131072 }], error: null }));
    await settle();
    fireEvent.click(screen.getByRole("switch", { name: "Set context length manually" }));
    fireEvent.change(screen.getByLabelText("Context length"), { target: { value: "200000" } });
    expect(screen.getByRole("alert").textContent).toContain("More than the 131,072 tokens");
    fireEvent.change(screen.getByLabelText("Context length"), { target: { value: "100000" } });
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
