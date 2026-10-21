// @vitest-environment jsdom
/**
 * A failed mark-read must surface as a toast, not an unhandled rejection.
 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@/components";
import type { Notification } from "@/api/chat";

vi.mock("@/api/chat", () => ({
  markNotificationsRead: vi.fn(async () => {
    throw new Error("mark-read failed");
  }),
  markAllNotificationsRead: vi.fn(),
  clearNotifications: vi.fn(),
}));

import { Notifications } from "../Notifications";
import { markNotificationsRead } from "@/api/chat";

const note: Notification = {
  no: 1,
  id: "n1",
  title: "Something happened",
  type: "info",
  read: false,
  detail: "details",
};

afterEach(() => {
  cleanup();
  vi.mocked(markNotificationsRead).mockClear();
});

describe("Notifications.run", () => {
  it("shows a toast when marking a notification read fails", async () => {
    const rejection = vi.fn();
    window.addEventListener("unhandledrejection", rejection);

    render(
      <ToastProvider>
        <Notifications notifications={[note]} onChanged={() => {}} />
      </ToastProvider>,
    );

    fireEvent.click(screen.getByLabelText(/Notifications/));
    fireEvent.click(screen.getByText("Something happened"));

    await waitFor(() => expect(markNotificationsRead).toHaveBeenCalled());
    expect((await screen.findByRole("alert")).textContent).toContain("mark-read failed");
    expect(rejection).not.toHaveBeenCalled();

    window.removeEventListener("unhandledrejection", rejection);
  });
});

describe("Notifications link", () => {
  it("opens an in-app path and never another site", () => {
    render(
      <MemoryRouter>
        <ToastProvider>
          <Notifications
            notifications={[
              { ...note, id: "n2", no: 2, title: "A change waits for approval", link: "/approvals?change=chg1" },
              { ...note, id: "n3", no: 3, title: "Elsewhere", link: "//evil.example/x" },
            ]}
            onChanged={() => {}}
          />
        </ToastProvider>
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByLabelText(/Notifications/));
    const links = screen.getAllByRole("link", { name: "Open" });
    expect(links.map((a) => a.getAttribute("href"))).toEqual(["/approvals?change=chg1"]);
  });
});
