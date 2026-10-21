/**
 * Render the three states of a `useAsync` load -- loading, error (with retry),
 * ready -- in one place.
 *
 * Generalises the `SectionLoading`/`SectionError` pair from the settings screen
 * so every tab shows the same loading text and the same retryable error card
 * instead of each inventing its own (or, worse, swallowing the error). The
 * `centered` variant matches the full-screen `CenteredMessage` used at the top
 * of standalone screens.
 */

import type { ReactNode } from "react";

import { Button, Card, CenteredMessage } from "./index";
import type { AsyncState } from "@/lib/useAsync";

export function AsyncBoundary<T>({
  state,
  reload,
  title = "content",
  variant = "section",
  children,
}: {
  state: AsyncState<T>;
  reload: () => void;
  /** Named in the loading/error copy, e.g. "users" -> "Loading users…". */
  title?: string;
  variant?: "section" | "centered";
  children: (data: T) => ReactNode;
}) {
  if (state.status === "loading") {
    if (variant === "centered") return <CenteredMessage title={`Loading ${title}…`} />;
    return (
      <Card title={`Loading ${title}…`}>
        <p style={{ padding: "var(--space-3) 0", color: "var(--ink-3)" }}>One moment.</p>
      </Card>
    );
  }

  if (state.status === "error") {
    if (variant === "centered") {
      return (
        <CenteredMessage
          title={`Could not load ${title}`}
          detail={state.error.message}
          action={
            <Button variant="primary" onClick={reload}>
              Retry
            </Button>
          }
        />
      );
    }
    return (
      <Card title={`Could not load ${title}`} danger>
        <p style={{ padding: "var(--space-3) 0" }}>{state.error.message}</p>
        <Button onClick={reload}>Retry</Button>
      </Card>
    );
  }

  return <>{children(state.data)}</>;
}
