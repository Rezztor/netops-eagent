// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Modal } from "../Dialog";

afterEach(() => cleanup());

describe("Modal focus", () => {
  it("keeps focus on the field being typed in when the parent re-renders", () => {
    const view = (close: () => void) => (
      <Modal title="Rename" onClose={close}>
        <button type="button">first</button>
        <input aria-label="name" />
      </Modal>
    );
    const { rerender } = render(view(() => {}));
    const input = screen.getByLabelText("name");
    input.focus();

    // A parent with an inline onClose hands over a new function each render.
    rerender(view(() => {}));
    expect(document.activeElement).toBe(input);
  });

  it("closes through the latest onClose on Escape", () => {
    const first = vi.fn();
    const second = vi.fn();
    const { rerender } = render(<Modal title="t" onClose={first}><button type="button">x</button></Modal>);
    rerender(<Modal title="t" onClose={second}><button type="button">x</button></Modal>);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledOnce();
  });
});
