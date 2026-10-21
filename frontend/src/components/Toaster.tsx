/**
 * Transient success/error notifications.
 *
 * Many flows here mutated something and then said nothing -- the row updated if
 * you were watching, and errors were swallowed or dumped into an inline banner
 * that scrolled away. `useToast()` gives every screen a one-line way to confirm
 * an action succeeded or report why it failed, without threading notice/error
 * state through each component. Inline `ScopeNote` banners remain for standing,
 * section-level messages; toasts are for the result of an action.
 */

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

import styles from "./foundations.module.css";

export type ToastTone = "info" | "ok" | "error";

interface Toast {
  id: number;
  tone: ToastTone;
  message: string;
}

interface ToastApi {
  toast: (message: string, tone?: ToastTone) => void;
  success: (message: string) => void;
  error: (message: string) => void;
  dismiss: (id: number) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

const DEFAULT_TTL_MS = 5000;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const toast = useCallback(
    (message: string, tone: ToastTone = "info") => {
      const id = nextId.current++;
      setToasts((prev) => [...prev, { id, tone, message }]);
      // Errors linger longer than confirmations, since they may need reading.
      const ttl = tone === "error" ? DEFAULT_TTL_MS * 1.6 : DEFAULT_TTL_MS;
      window.setTimeout(() => dismiss(id), ttl);
    },
    [dismiss],
  );

  const api = useMemo<ToastApi>(
    () => ({
      toast,
      success: (message: string) => toast(message, "ok"),
      error: (message: string) => toast(message, "error"),
      dismiss,
    }),
    [toast, dismiss],
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      <Toaster toasts={toasts} onDismiss={dismiss} />
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const context = useContext(ToastContext);
  if (!context) throw new Error("useToast must be used inside a ToastProvider");
  return context;
}

function Toaster({ toasts, onDismiss }: { toasts: Toast[]; onDismiss: (id: number) => void }) {
  if (typeof document === "undefined") return null;
  return createPortal(
    <div className={styles.toaster} aria-live="polite" aria-atomic="false">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`${styles.toast} ${
            t.tone === "ok" ? styles.toastOk : t.tone === "error" ? styles.toastErr : ""
          }`}
          role={t.tone === "error" ? "alert" : "status"}
        >
          <span className={styles.toastText}>{t.message}</span>
          <button
            type="button"
            className={styles.toastClose}
            aria-label="Dismiss"
            onClick={() => onDismiss(t.id)}
          >
            ×
          </button>
        </div>
      ))}
    </div>,
    document.body,
  );
}
