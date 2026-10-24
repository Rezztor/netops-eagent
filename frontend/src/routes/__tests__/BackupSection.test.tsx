// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as operations from "@/api/operations";
import { ConfirmProvider } from "@/components";
import { BackupSection } from "../BackupSection";

vi.mock("@/api/operations", async (importOriginal) => ({
  ...(await importOriginal<typeof operations>()),
  createBackup: vi.fn(),
  inspectBackup: vi.fn(),
  previewRestore: vi.fn(),
  restoreBackup: vi.fn(),
}));

const m = {
  create: vi.mocked(operations.createBackup),
  inspect: vi.mocked(operations.inspectBackup),
  preview: vi.mocked(operations.previewRestore),
};

const defaults: operations.BackupDefaults = {
  default_patterns: { include_patterns: ["usr/**"], exclude_patterns: [] },
  metadata: { backup_name: "nightly", include_hidden: true },
};

const onError = vi.fn();
const onNotice = vi.fn();

function renderSection() {
  render(
    <ConfirmProvider>
      <BackupSection defaults={defaults} onError={onError} onNotice={onNotice} />
    </ConfirmProvider>,
  );
}

const create = () => screen.getByText("Create and download");
const type = (label: string, value: string, index = 0) =>
  fireEvent.change(screen.getAllByLabelText(label)[index]!, { target: { value } });

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("creating a backup", () => {
  it("says plainly what the file holds and that the passphrase cannot be recovered", () => {
    renderSection();
    const note = screen.getByRole("note");
    expect(note.textContent).toMatch(/keys/);
    expect(note.textContent).toMatch(/password hash/);
    expect(note.textContent).toMatch(/every\s+user's data/);
    expect(note.textContent).toMatch(/cannot be\s+recovered/);
    expect(note.textContent).toMatch(/without it the backup cannot be restored/);
  });

  it("refuses a short passphrase without calling the server", async () => {
    renderSection();
    type("Backup passphrase", "short");
    type("Repeat backup passphrase", "short");
    fireEvent.click(create());
    await waitFor(() => expect(onError).toHaveBeenCalledWith(expect.stringMatching(/at least 12/)));
    expect(m.create).not.toHaveBeenCalled();
  });

  it("refuses two passphrases that differ", async () => {
    renderSection();
    type("Backup passphrase", "a long enough passphrase");
    type("Repeat backup passphrase", "a long enough passphrasE");
    fireEvent.click(create());
    await waitFor(() => expect(onError).toHaveBeenCalledWith(expect.stringMatching(/not the same/)));
    expect(m.create).not.toHaveBeenCalled();
  });

  it("sends the passphrase once, then blanks both fields", async () => {
    m.create.mockResolvedValue(undefined);
    renderSection();
    type("Backup passphrase", "a long enough passphrase");
    type("Repeat backup passphrase", "a long enough passphrase");
    fireEvent.click(create());
    await waitFor(() => expect(m.create).toHaveBeenCalled());
    expect(m.create.mock.calls[0]![4]).toBe("a long enough passphrase");
    await waitFor(() => expect((screen.getByLabelText("Backup passphrase") as HTMLInputElement).value).toBe(""));
    expect((screen.getByLabelText("Repeat backup passphrase") as HTMLInputElement).value).toBe("");
  });

  it("hides the passphrase while it is typed", () => {
    renderSection();
    expect((screen.getByLabelText("Backup passphrase") as HTMLInputElement).type).toBe("password");
    expect((screen.getByLabelText("Repeat backup passphrase") as HTMLInputElement).type).toBe("password");
  });
});

describe("restoring a backup", () => {
  function choose() {
    const file = new File(["x"], "nightly.rzbak");
    fireEvent.change(screen.getByLabelText("Backup archive"), { target: { files: [file] } });
    return file;
  }

  it("sends the passphrase with the inspection", async () => {
    m.inspect.mockResolvedValue({ success: true, encrypted: true, metadata: {}, total_files: 1 });
    renderSection();
    const file = choose();
    // Create form has the same label; the restore field is the last one.
    const fields = screen.getAllByLabelText("Backup passphrase");
    fireEvent.change(fields[fields.length - 1]!, { target: { value: "the right passphrase" } });
    fireEvent.click(screen.getByText("Inspect"));
    await waitFor(() => expect(m.inspect).toHaveBeenCalledWith(file, "the right passphrase"));
    expect(screen.queryByText(/was not protected/)).toBeNull();
  });

  it("shows the server's refusal when the passphrase is wrong", async () => {
    m.inspect.mockRejectedValue(new Error("The backup could not be decrypted: the passphrase is wrong or the file is damaged."));
    renderSection();
    choose();
    fireEvent.click(screen.getByText("Inspect"));
    await waitFor(() => expect(onError).toHaveBeenCalledWith(expect.stringMatching(/passphrase is wrong/)));
  });

  it("tells the administrator an old plain backup was not protected", async () => {
    m.inspect.mockResolvedValue({ success: true, encrypted: false, metadata: {}, total_files: 1 });
    renderSection();
    choose();
    fireEvent.click(screen.getByText("Inspect"));
    expect(await screen.findByText(/This backup was not protected/)).toBeTruthy();
  });
});
