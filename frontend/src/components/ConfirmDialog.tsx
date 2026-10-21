/**
 * Promise-based confirm and prompt dialogs to replace `window.confirm` /
 * `window.prompt`.
 *
 * The native calls have no focus trap, cannot restate consequences with any
 * emphasis, block the main thread, and look nothing like the app. `useConfirm()`
 * returns async functions that resolve to the user's choice, so a caller reads
 * as `if (await confirm({...})) { ... }` -- the same shape as before, without
 * the drawbacks.
 */

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { Button, TextInput } from "./index";
import { Modal } from "./Dialog";

export interface ConfirmOptions {
  title: string;
  /** Restated consequences. Kept prominent because this is where "are you sure" earns its place. */
  body?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Style the confirm affordance as destructive. */
  danger?: boolean;
}

export interface PromptOptions extends ConfirmOptions {
  label: string;
  placeholder?: string;
  defaultValue?: string;
  inputType?: "text" | "password";
  /** Return an error string to block submission, or null to allow it. */
  validate?: (value: string) => string | null;
}

interface ConfirmApi {
  confirm: (options: ConfirmOptions) => Promise<boolean>;
  prompt: (options: PromptOptions) => Promise<string | null>;
}

const ConfirmContext = createContext<ConfirmApi | null>(null);

type Pending =
  | { kind: "confirm"; options: ConfirmOptions; resolve: (value: boolean) => void }
  | { kind: "prompt"; options: PromptOptions; resolve: (value: string | null) => void };

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<Pending | null>(null);

  const confirm = useCallback(
    (options: ConfirmOptions) =>
      new Promise<boolean>((resolve) => setPending({ kind: "confirm", options, resolve })),
    [],
  );

  const prompt = useCallback(
    (options: PromptOptions) =>
      new Promise<string | null>((resolve) => setPending({ kind: "prompt", options, resolve })),
    [],
  );

  const api = useMemo<ConfirmApi>(() => ({ confirm, prompt }), [confirm, prompt]);

  return (
    <ConfirmContext.Provider value={api}>
      {children}
      {pending && (
        <PendingDialog
          pending={pending}
          onClose={() => {
            if (pending.kind === "confirm") pending.resolve(false);
            else pending.resolve(null);
            setPending(null);
          }}
          onResolve={(value) => {
            if (pending.kind === "confirm") pending.resolve(value as boolean);
            else pending.resolve(value as string | null);
            setPending(null);
          }}
        />
      )}
    </ConfirmContext.Provider>
  );
}

export function useConfirm(): ConfirmApi {
  const context = useContext(ConfirmContext);
  if (!context) throw new Error("useConfirm must be used inside a ConfirmProvider");
  return context;
}

function PendingDialog({
  pending,
  onClose,
  onResolve,
}: {
  pending: Pending;
  onClose: () => void;
  onResolve: (value: boolean | string | null) => void;
}) {
  const { options } = pending;
  const [value, setValue] = useState(pending.kind === "prompt" ? pending.options.defaultValue ?? "" : "");
  const [error, setError] = useState<string | null>(null);

  const submit = () => {
    if (pending.kind === "prompt") {
      const message = pending.options.validate?.(value) ?? null;
      if (message) {
        setError(message);
        return;
      }
      onResolve(value);
    } else {
      onResolve(true);
    }
  };

  return (
    <Modal
      title={options.title}
      danger={options.danger ?? false}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>{options.cancelLabel ?? "Cancel"}</Button>
          <Button variant={options.danger ? "danger" : "primary"} onClick={submit}>
            {options.confirmLabel ?? (pending.kind === "prompt" ? "Save" : "Confirm")}
          </Button>
        </>
      }
    >
      {options.body && <div>{options.body}</div>}
      {pending.kind === "prompt" && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          <label style={{ display: "grid", gap: "var(--space-1)" }}>
            <span style={{ fontSize: "var(--text-sm)", color: "var(--ink-2)" }}>
              {pending.options.label}
            </span>
            <TextInput
              autoFocus
              type={pending.options.inputType ?? "text"}
              placeholder={pending.options.placeholder}
              value={value}
              onChange={(event) => {
                setValue(event.target.value);
                if (error) setError(null);
              }}
            />
          </label>
          {error && (
            <p style={{ color: "var(--crit)", fontSize: "var(--text-sm)", margin: "var(--space-1) 0 0" }}>
              {error}
            </p>
          )}
        </form>
      )}
    </Modal>
  );
}
