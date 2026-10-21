// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/client";
import { ErrorNote } from "../index";

afterEach(() => cleanup());

const failure = () => new ApiError("message_async", 500, "Something went wrong on the server (HTTP 500).", "boom at /srv/app/x.py");

describe("ErrorNote", () => {
  it("is an alert that can be closed", () => {
    const close = vi.fn();
    render(<ErrorNote error={failure()} onDismiss={close} />);
    expect(screen.getByRole("alert").textContent).toContain("Something went wrong");
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(close).toHaveBeenCalled();
  });

  it("keeps the server's text from anyone but an administrator", () => {
    render(<ErrorNote error={failure()} />);
    expect(screen.queryByText("Technical details")).toBeNull();
    expect(document.body.textContent).not.toContain("/srv/app");
    cleanup();
    render(<ErrorNote error={failure()} details />);
    expect(screen.getByText("Technical details")).toBeTruthy();
    expect(document.body.textContent).toContain("/srv/app");
  });
});
