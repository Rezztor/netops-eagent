// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as filesApi from "@/api/files";
import { ConfirmProvider, ToastProvider } from "@/components";
import { FilesScreen } from "../FilesScreen";

vi.mock("@/api/files", async (importOriginal) => ({
  ...(await importOriginal<typeof filesApi>()),
  listDirectory: vi.fn(),
  readFile: vi.fn(),
  writeFile: vi.fn(),
}));

vi.mock("@/lib/session", () => ({
  useSession: () => ({ preferences: { timezone: "UTC", time_format: "24h" } }),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("file editor save", () => {
  it("keeps what was typed while the write was in flight, and stays unsaved", async () => {
    vi.mocked(filesApi.listDirectory).mockResolvedValue({
      current_path: "/wd",
      parent_path: "/",
      entries: [{ name: "notes.txt", path: "wd/notes.txt", modified: "2026-01-01T00:00:00Z", type: "txt", size: 3, is_dir: false }],
    });
    vi.mocked(filesApi.readFile).mockResolvedValue({ data: { content: "abc" } });
    let finish: () => void = () => {};
    vi.mocked(filesApi.writeFile).mockReturnValue(new Promise((resolve) => { finish = () => resolve({}); }));

    render(
      <MemoryRouter>
        <ToastProvider>
          <ConfirmProvider>
            <FilesScreen />
          </ConfirmProvider>
        </ToastProvider>
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByText("notes.txt"));
    const editor = (await screen.findByLabelText("Contents of notes.txt")) as HTMLTextAreaElement;
    fireEvent.change(editor, { target: { value: "abcd" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    // More typing while the request is out.
    fireEvent.change(await screen.findByLabelText("Contents of notes.txt"), { target: { value: "abcdef" } });
    await act(async () => finish());

    await waitFor(() => expect(filesApi.writeFile).toHaveBeenCalledWith("wd/notes.txt", "abcd"));
    expect((screen.getByLabelText("Contents of notes.txt") as HTMLTextAreaElement).value).toBe("abcdef");
    expect(screen.getByText("unsaved")).toBeTruthy();
  });
});
