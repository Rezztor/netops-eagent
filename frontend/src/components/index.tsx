/**
 * The component library.
 *
 * Small, unopinionated primitives that match the approved reference pages in
 * docs/internal/reference-ui/. Everything visual comes from the token file --
 * a component never names a colour.
 */

import type { ButtonHTMLAttributes, ReactNode } from "react";

import { NO_ACCESS_MESSAGE } from "@/api/client";
import styles from "./primitives.module.css";

function cx(...values: Array<string | false | null | undefined>): string {
  return values.filter(Boolean).join(" ");
}

/* ---------- Button ---------- */

export type ButtonVariant = "default" | "primary" | "danger" | "ghost";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
}

export function Button({ variant = "default", className, ...rest }: ButtonProps) {
  const variantClass =
    variant === "primary"
      ? styles.primary
      : variant === "danger"
        ? styles.danger
        : variant === "ghost"
          ? styles.ghost
          : undefined;
  // `type` defaults to "submit" inside a form, which submits it by accident.
  return <button type="button" className={cx(styles.button, variantClass, className)} {...rest} />;
}

/* ---------- Card ---------- */

export function Card({
  title,
  description,
  actions,
  danger = false,
  children,
}: {
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  danger?: boolean;
  children?: ReactNode;
}) {
  return (
    <section className={cx(styles.card, danger && styles.cardDanger)}>
      {(title || description || actions) && (
        <header className={styles.cardHead}>
          <div className={styles.cardHeadText}>
            {title && <h2 className={styles.cardTitle}>{title}</h2>}
            {description && <p className={styles.cardDescription}>{description}</p>}
          </div>
          {actions}
        </header>
      )}
      {children && <div className={styles.cardBody}>{children}</div>}
    </section>
  );
}

/* ---------- Row ---------- */

export function Row({
  label,
  help,
  children,
}: {
  label: ReactNode;
  help?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className={styles.row}>
      <div>
        <div className={styles.rowLabel}>{label}</div>
        {help && <div className={styles.rowHelp}>{help}</div>}
      </div>
      <div className={styles.rowControl}>{children}</div>
    </div>
  );
}

/* ---------- Tag ---------- */

export type TagTone = "ok" | "warn" | "crit" | "accent" | "muted";

export function Tag({ tone = "muted", children }: { tone?: TagTone; children: ReactNode }) {
  const toneClass = {
    ok: styles.tagOk,
    warn: styles.tagWarn,
    crit: styles.tagCrit,
    accent: styles.tagAccent,
    muted: styles.tagMuted,
  }[tone];
  return <span className={cx(styles.tag, toneClass)}>{children}</span>;
}

/* ---------- Toggle ---------- */

export function Toggle({
  checked,
  onChange,
  label,
  disabled = false,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      className={styles.toggle}
      onClick={() => onChange(!checked)}
    />
  );
}

/* ---------- ScopeNote ---------- */

/**
 * States who a section affects and who may change it.
 *
 * Used at the top of every administration surface. The wording matters: the
 * backend rejects the request, not just the control, and saying so stops the
 * banner reading as a soft suggestion.
 */
export function ScopeNote({
  tone = "accent",
  children,
}: {
  tone?: "accent" | "warn" | "crit";
  children: ReactNode;
}) {
  const toneClass =
    tone === "warn" ? styles.scopeNoteWarn : tone === "crit" ? styles.scopeNoteCrit : undefined;
  return (
    <div className={cx(styles.scopeNote, toneClass)} role="note">
      {children}
    </div>
  );
}

/* ---------- ErrorNote ---------- */

/**
 * A failure, said once and announced.
 *
 * One sentence (the error's message), closable, and `role="alert"` so a screen
 * reader hears it. What the server actually said -- `detail` on an `ApiError`
 * -- folds under "Technical details", and only when `details` is set: callers
 * pass it for administrators, because that text can name paths and hosts.
 */
export function ErrorNote({
  error,
  onDismiss,
  details = false,
}: {
  error: unknown;
  onDismiss?: () => void;
  details?: boolean;
}) {
  const message = error instanceof Error ? error.message : String(error);
  const raw = error && typeof error === "object" && "detail" in error ? (error as { detail?: unknown }).detail : "";
  const detail = details && typeof raw === "string" ? raw : "";
  return (
    <div className={cx(styles.scopeNote, styles.scopeNoteCrit)} role="alert">
      <div className={styles.errorNoteBody}>
        <div>{message}</div>
        {detail && (
          <details className={styles.errorDetails}>
            <summary>Technical details</summary>
            <pre>{detail}</pre>
          </details>
        )}
      </div>
      {onDismiss && (
        <button type="button" className={styles.errorDismiss} aria-label="Dismiss" onClick={onDismiss}>
          ×
        </button>
      )}
    </div>
  );
}

/* ---------- Table ---------- */

export function Table({ children }: { children: ReactNode }) {
  // Wide tables scroll inside their own container so the page body never does.
  return (
    <div className="scroll-x">
      <table className={styles.table}>{children}</table>
    </div>
  );
}

/* ---------- Field ---------- */

export function TextInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={cx(styles.input, props.className)} />;
}

export function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={cx(styles.select, props.className)} />;
}

export function Mono({ children }: { children: ReactNode }) {
  return <span className={styles.mono}>{children}</span>;
}

/* ---------- Status ---------- */

export function CenteredMessage({
  title,
  detail,
  action,
}: {
  title: ReactNode;
  detail?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className={styles.centered}>
      <div>
        <h1 className={styles.cardTitle}>{title}</h1>
        {detail && <p className={styles.muted}>{detail}</p>}
        {action && <div style={{ marginTop: "var(--space-4)" }}>{action}</div>}
      </div>
    </div>
  );
}

/**
 * A screen whose data did not load.
 *
 * A permission refusal is not a fault and retrying it changes nothing, so it
 * says so plainly and offers no Retry. Anything else keeps the Retry.
 */
export function LoadFailed({ what, error, onRetry }: { what: string; error: unknown; onRetry: () => void }) {
  const status = error && typeof error === "object" ? (error as { status?: unknown }).status : undefined;
  const message = error instanceof Error ? error.message : String(error);
  if (status === 403 && message === NO_ACCESS_MESSAGE) {
    return <CenteredMessage title="You don't have access to this page" detail="Ask an administrator if you need it." />;
  }
  return (
    <CenteredMessage
      title={`Could not load ${what}`}
      detail={message}
      action={<Button variant="primary" onClick={onRetry}>Retry</Button>}
    />
  );
}

/* ---------- Shared foundations ---------- */
// Re-exported here so every screen imports UX primitives from one place
// (`@/components`), matching how the base primitives above are consumed.
export { AsyncBoundary } from "./AsyncBoundary";
export { ConfirmProvider, useConfirm } from "./ConfirmDialog";
export type { ConfirmOptions, PromptOptions } from "./ConfirmDialog";
export { Modal, Drawer } from "./Dialog";
export { DateTime } from "./DateTime";
export type { DateTimeMode } from "./DateTime";
export { Tabs, TabPanel } from "./Tabs";
export type { TabItem } from "./Tabs";
export { SearchInput, SortHeader, Pagination, EmptyRow } from "./TableControls";
export { ToastProvider, useToast } from "./Toaster";
export type { ToastTone } from "./Toaster";
