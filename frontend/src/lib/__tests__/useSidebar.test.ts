// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  isTypingTarget,
  SIDEBAR_PINNED_KEY,
  useSidebar,
} from "../useSidebar";

function installMemoryStorage() {
  const store = new Map<string, string>();
  const memory: Storage = {
    get length() {
      return store.size;
    },
    clear: () => store.clear(),
    getItem: (key) => store.get(key) ?? null,
    setItem: (key, value) => {
      store.set(key, String(value));
    },
    removeItem: (key) => {
      store.delete(key);
    },
    key: (index) => [...store.keys()][index] ?? null,
  };
  Object.defineProperty(globalThis, "localStorage", {
    value: memory,
    configurable: true,
    writable: true,
  });
}

beforeEach(() => {
  installMemoryStorage();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("isTypingTarget", () => {
  it("detects input, textarea, select and contenteditable", () => {
    const input = document.createElement("input");
    const textarea = document.createElement("textarea");
    const select = document.createElement("select");
    const editable = document.createElement("div");
    editable.setAttribute("contenteditable", "true");
    const div = document.createElement("div");
    expect(isTypingTarget(input)).toBe(true);
    expect(isTypingTarget(textarea)).toBe(true);
    expect(isTypingTarget(select)).toBe(true);
    expect(isTypingTarget(editable)).toBe(true);
    expect(isTypingTarget(div)).toBe(false);
    expect(isTypingTarget(null)).toBe(false);
  });
});

describe("useSidebar", () => {
  it("defaults to pinned", () => {
    const { result } = renderHook(() => useSidebar());
    expect(result.current.pinned).toBe(true);
    expect(result.current.peeking).toBe(false);
  });

  it("persists pin toggle to localStorage", () => {
    const { result } = renderHook(() => useSidebar());
    act(() => result.current.setPinned(false));
    expect(localStorage.getItem(SIDEBAR_PINNED_KEY)).toBe("0");
    expect(result.current.pinned).toBe(false);

    act(() => result.current.toggle());
    expect(localStorage.getItem(SIDEBAR_PINNED_KEY)).toBe("1");
    expect(result.current.pinned).toBe(true);
  });

  it("reads unpinned from localStorage", () => {
    localStorage.setItem(SIDEBAR_PINNED_KEY, "0");
    const { result } = renderHook(() => useSidebar());
    expect(result.current.pinned).toBe(false);
  });

  it("survives localStorage throwing on read and write", () => {
    Object.defineProperty(globalThis, "localStorage", {
      value: {
        getItem: () => {
          throw new Error("denied");
        },
        setItem: () => {
          throw new Error("denied");
        },
        removeItem: () => {
          throw new Error("denied");
        },
        clear: () => {
          throw new Error("denied");
        },
        key: () => null,
        length: 0,
      },
      configurable: true,
    });
    const { result } = renderHook(() => useSidebar());
    expect(result.current.pinned).toBe(true);
    act(() => result.current.setPinned(false));
    expect(result.current.pinned).toBe(false);
  });

  it("toggles with [ and Ctrl/Cmd+B, ignored inside inputs", () => {
    const { result } = renderHook(() => useSidebar());
    expect(result.current.pinned).toBe(true);

    const input = document.createElement("input");
    document.body.appendChild(input);

    act(() => {
      const event = new KeyboardEvent("keydown", { key: "[", bubbles: true });
      Object.defineProperty(event, "target", { value: input });
      document.dispatchEvent(event);
    });
    expect(result.current.pinned).toBe(true);

    act(() => {
      const event = new KeyboardEvent("keydown", { key: "[", bubbles: true });
      Object.defineProperty(event, "target", { value: document.body });
      document.dispatchEvent(event);
    });
    expect(result.current.pinned).toBe(false);

    act(() => {
      const event = new KeyboardEvent("keydown", {
        key: "b",
        ctrlKey: true,
        bubbles: true,
      });
      Object.defineProperty(event, "target", { value: document.body });
      document.dispatchEvent(event);
    });
    expect(result.current.pinned).toBe(true);

    input.remove();
  });

  it("clears peeking when pinning", () => {
    const { result } = renderHook(() => useSidebar());
    act(() => {
      result.current.setPinned(false);
      result.current.setPeeking(true);
    });
    expect(result.current.peeking).toBe(true);
    act(() => result.current.setPinned(true));
    expect(result.current.pinned).toBe(true);
    expect(result.current.peeking).toBe(false);
  });
});
