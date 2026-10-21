// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

let admin = false;
vi.mock("@/lib/session", () => ({ useCan: () => admin }));

import { ApiError, NO_ACCESS_MESSAGE } from "@/api/client";
import { LoadFailed } from "@/components";
import { ExecutorRefused } from "../ExecutorState";

afterEach(() => cleanup());

const NO_TOKEN = "Network automation has no executor token on this instance. An administrator must set one.";

describe("ExecutorRefused", () => {
  it("is a setup step, not a fault: a link for an administrator and no Retry", () => {
    admin = true;
    render(<MemoryRouter><ExecutorRefused error={NO_TOKEN} onRetry={() => {}} /></MemoryRouter>);
    expect(screen.getByText("Network automation is not set up yet")).toBeTruthy();
    expect(screen.getByRole("link").getAttribute("href")).toBe("/settings/network-automation");
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  it("sends everyone else to their administrator", () => {
    admin = false;
    render(<MemoryRouter><ExecutorRefused error={NO_TOKEN} onRetry={() => {}} /></MemoryRouter>);
    expect(screen.getByText("Ask your administrator to set it up.")).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("keeps Retry for a real refusal", () => {
    render(<MemoryRouter><ExecutorRefused error="executor returned 502" onRetry={() => {}} /></MemoryRouter>);
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });
});

describe("LoadFailed", () => {
  it("says no access, without Retry, on a permission refusal", () => {
    render(<LoadFailed what="users" error={new ApiError("users_list", 403, NO_ACCESS_MESSAGE, null)} onRetry={() => {}} />);
    expect(screen.getByText("You don't have access to this page")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  it("keeps Retry for anything else", () => {
    render(<LoadFailed what="users" error={new ApiError("users_list", 500, "boom", null)} onRetry={() => {}} />);
    expect(screen.getByText("Could not load users")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });
});
