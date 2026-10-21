/**
 * Pieces every section shares.
 *
 * The save bar is the one worth explaining. Each section writes to its own
 * endpoint -- core settings, the SolarWinds config, the SIEM config -- so there
 * is no single save to fan out across sections, and only one section is on
 * screen at a time anyway. What changed is where the control lives: a Save
 * button in a card header scrolls out of sight on a ten-row section, which is
 * exactly when it is wanted. This sticks to the bottom of the pane and names
 * what is unsaved, because nothing on this screen saves on its own.
 */

import { useEffect, type ReactNode } from "react";

import { Button, Card, Tag } from "@/components";
import { GROUP_PERMISSION, type SectionGroup } from "./types";
import styles from "./settings.module.css";

export function SaveBar({
  dirty,
  saving,
  saved,
  error,
  onSave,
  onDiscard,
}: {
  /** How many fields differ from what the server has. */
  dirty: number;
  saving: boolean;
  saved: boolean;
  error: string | null;
  onSave: () => void;
  onDiscard?: () => void;
}) {
  // Nothing here saves on its own, so a reload or tab close with unsaved edits
  // loses them silently. The native prompt is the only guard that covers those
  // routes (an in-app navigation still shows the bar in the next section).
  useEffect(() => {
    if (dirty <= 0) return;
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);

  if (!dirty && !error && !saved) return null;

  return (
    <div className={`${styles.saveBar} ${error ? styles.saveBarError : ""}`} role="status">
      <div className={styles.saveBarText}>
        {error ? (
          <span style={{ color: "var(--crit)" }}>{error}</span>
        ) : dirty ? (
          <>
            <b>
              {dirty} unsaved {dirty === 1 ? "change" : "changes"}.
            </b>{" "}
            Nothing here saves on its own.
          </>
        ) : (
          <Tag tone="ok">saved</Tag>
        )}
      </div>
      {(dirty > 0 || error) && (
        <>
          {onDiscard && (
            <Button onClick={onDiscard} disabled={saving}>
              Discard
            </Button>
          )}
          <Button variant="primary" onClick={onSave} disabled={saving}>
            {saving ? "Saving…" : error ? "Try again" : "Save changes"}
          </Button>
        </>
      )}
    </div>
  );
}

/**
 * Says that the *endpoints* reject the request, not merely that the controls
 * are hidden -- matching how `helpers/api_policy.py` actually enforces it.
 */
export function ScopeBanner({ group }: { group: SectionGroup }) {
  const permission = GROUP_PERMISSION[group];
  if (!permission) return null;
  // "Administrator only" is wrong for the Integrations group: those endpoints
  // gate on plugins.manage, which is a distinct grant an account can hold
  // without the settings.manage the rest of the screen means by "administrator".
  const heading = permission === "plugins.manage" ? "Plugin managers only." : "Administrator only.";
  return (
    <div
      style={{
        display: "flex",
        gap: 9,
        alignItems: "flex-start",
        border: "1px solid var(--line-2)",
        background: "var(--surface-alt)",
        borderRadius: "var(--radius-sm)",
        padding: "9px 11px",
        fontSize: "var(--text-sm)",
        color: "var(--ink-2)",
      }}
    >
      <div>
        <strong style={{ color: "var(--ink)", fontWeight: 620 }}>{heading}</strong>{" "}
        It changes the instance for everyone, and the server refuses it from anyone
        without that role.
      </div>
    </div>
  );
}

export function SectionLoading({ title }: { title: string }) {
  return (
    <Card title={title}>
      <p style={{ padding: "var(--space-3) 0", color: "var(--ink-3)" }}>Loading…</p>
    </Card>
  );
}

export function SectionError({
  title,
  message,
  onRetry,
}: {
  title: string;
  message: string;
  onRetry: () => void;
}) {
  return (
    <Card title={title} danger>
      <p style={{ padding: "var(--space-3) 0" }}>{message}</p>
      <Button onClick={onRetry}>Retry</Button>
    </Card>
  );
}

export function Muted({ children }: { children: ReactNode }) {
  return (
    <p
      style={{
        padding: "var(--space-2) 0",
        margin: 0,
        color: "var(--ink-3)",
        fontSize: "var(--text-sm)",
      }}
    >
      {children}
    </p>
  );
}
