/**
 * Application shell.
 *
 * Everything hangs off one question, answered by GET /api/bootstrap: is there a
 * session? Until that returns, nothing is rendered -- not a guessed layout, not
 * a login form. Rendering the app optimistically and correcting afterwards would
 * flash an interface the user may not be entitled to.
 */

import { CenteredMessage, Button, ConfirmProvider, ToastProvider } from "@/components";
import { redirectToLogin } from "@/api/client";
import { SessionProvider, useReloadSession, useSessionState } from "@/lib/session";
import { AppShell, displayVersion } from "@/routes/AppShell";
import { ChangePasswordScreen } from "@/routes/ChangePasswordScreen";

export function App() {
  // Toasts and confirm dialogs are cross-cutting: mounted once here so any
  // screen can raise one without threading provider plumbing through the tree.
  return (
    <SessionProvider>
      <ToastProvider>
        <ConfirmProvider>
          <Root />
        </ConfirmProvider>
      </ToastProvider>
    </SessionProvider>
  );
}

function Root() {
  const state = useSessionState();
  const reload = useReloadSession();

  if (state.status === "loading") {
    // Intentionally bare: a skeleton of an interface the user may not be
    // allowed to see is worse than a moment of nothing.
    return <CenteredMessage title="Loading…" />;
  }

  if (state.status === "error") {
    return (
      <CenteredMessage
        title="Cannot reach Rezztor EAgent"
        detail={state.error.message}
        action={<Button variant="primary" onClick={reload}>Retry</Button>}
      />
    );
  }

  if (!state.session.authenticated) {
    return (
      <CenteredMessage
        title="Sign in required"
        detail={`Rezztor EAgent ${displayVersion(state.session.version.version)}`}
        action={
          <Button variant="primary" onClick={redirectToLogin}>
            Go to sign in
          </Button>
        }
      />
    );
  }

  // Nothing else is reachable until this is cleared, so nothing else renders.
  if (state.session.user.must_change_password) {
    return <ChangePasswordScreen />;
  }

  return <AppShell />;
}
