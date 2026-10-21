/**
 * Forced password change.
 *
 * Blocking, and deliberately so: while `must_change_password` is set the backend
 * refuses every endpoint except this one, `whoami`, `logout`, `csrf_token`,
 * `health` and `bootstrap`. Rendering the application around a user in that
 * state would show them an interface where nothing works and nothing explains
 * why.
 *
 * This is the screen bootstrap's `must_change_password` flag exists to trigger.
 */

import { useState } from "react";

import { apiAction, signOut } from "@/api/client";
import { Button, Card, Row, ScopeNote, TextInput } from "@/components";
import { useReloadSession, useSession } from "@/lib/session";

// Stated up front, not only once it is broken. The server enforces its own
// minimum; this one matches the wording used everywhere a password is set.
const MIN_LENGTH = 12;

export function ChangePasswordScreen() {
  const session = useSession();
  const reloadSession = useReloadSession();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const mismatch = confirm.length > 0 && next !== confirm;
  const tooShort = next.length > 0 && next.length < MIN_LENGTH;

  async function submit() {
    // The rules HTML cannot express. `required` and `minLength` are on the
    // fields and the browser reports those itself; "the two entries match" is
    // not a constraint a single input can carry, so it is checked here and
    // said out loud. It used to disable the submit button instead, which left
    // someone who mistyped the confirmation with a button that did nothing.
    if (next !== confirm) {
      setError("The two new-password entries do not match.");
      return;
    }
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await apiAction("change_password", {
        body: { current_password: current, new_password: next },
      });
      // Reload rather than route: the session's must_change_password flag is
      // what gates the whole application, and it lives in the bootstrap payload.
      reloadSession();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
      setBusy(false);
    }
  }

  return (
    <div
      style={{
        display: "grid",
        placeItems: "center",
        minHeight: "100vh",
        padding: "var(--space-5)",
      }}
    >
      <div style={{ width: "min(460px, 100%)", display: "grid", gap: "var(--space-4)" }}>
        <div>
          <p style={{ fontSize: "var(--text-sm)", fontWeight: 700, color: "var(--accent)", margin: 0 }}>
            Rezztor EAgent
          </p>
          <h1 style={{ fontSize: "var(--text-xl)", fontWeight: 700, letterSpacing: "-0.02em" }}>
            Choose a new password
          </h1>
          <p style={{ fontSize: "var(--text-base)", color: "var(--ink-2)", marginTop: 5 }}>
            Signed in as {session.user.display_name}. Set your own password to continue;
            the one you were given works only for this step.{" "}
            <Button variant="ghost" onClick={signOut}>Sign out</Button>
          </p>
        </div>

        {error && (
          <ScopeNote tone="crit">
            <div>{error}</div>
          </ScopeNote>
        )}

        <Card>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void submit();
            }}
          >
            <Row label="Current password">
              <TextInput
                type="password"
                autoComplete="current-password"
                value={current}
                required
                disabled={busy}
                onChange={(e) => setCurrent(e.target.value)}
                aria-label="Current password"
              />
            </Row>
            <Row
              label="New password"
              help={
                <span style={tooShort ? { color: "var(--warn)" } : undefined}>
                  At least {MIN_LENGTH} characters.
                </span>
              }
            >
              <TextInput
                type="password"
                autoComplete="new-password"
                value={next}
                required
                minLength={MIN_LENGTH}
                disabled={busy}
                onChange={(e) => setNext(e.target.value)}
                aria-label="New password"
              />
            </Row>
            <Row label="Confirm" help={mismatch ? "The two entries do not match." : undefined}>
              <TextInput
                type="password"
                autoComplete="new-password"
                value={confirm}
                required
                disabled={busy}
                onChange={(e) => setConfirm(e.target.value)}
                aria-label="Confirm new password"
              />
            </Row>
            <div style={{ padding: "var(--space-3) 0", display: "flex", justifyContent: "flex-end" }}>
              {/* Disabled only while the request is in flight. Disabling it on
                  validity is what made an incomplete form silent: the browser
                  never runs constraint validation on a submit that cannot
                  fire, so nothing named the offending field. */}
              <Button type="submit" variant="primary" disabled={busy}>
                {busy ? "Saving…" : "Set password"}
              </Button>
            </div>
          </form>
        </Card>
      </div>
    </div>
  );
}
