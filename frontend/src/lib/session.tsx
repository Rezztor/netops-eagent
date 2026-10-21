/**
 * Session state and permission gating.
 *
 * `useCan` exists so a component can ask "may this user do X" without knowing
 * about roles. Permissions are the contract; roles are just a bundle of them,
 * and helpers/rbac.py can add a role without this code changing.
 *
 * Gating the interface is a usability measure, not a security one. The backend
 * enforces the same permissions in helpers/api_policy.py and rejects the
 * request regardless of what the frontend chose to render -- so hiding a control
 * is about not offering people things they cannot do, and nothing here is load
 * bearing for access control.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { ApiError, loadSession } from "@/api/client";
import type { Permission, Session } from "@/api/types";

type SessionState =
  | { status: "loading" }
  | { status: "ready"; session: Session }
  | { status: "error"; error: Error };

interface SessionContextValue {
  state: SessionState;
  reload: () => void;
}

const SessionContext = createContext<SessionContextValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<SessionState>({ status: "loading" });
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setState({ status: "loading" });

    loadSession(controller.signal)
      .then((session) => setState({ status: "ready", session }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setState({
          status: "error",
          error: error instanceof Error ? error : new Error(String(error)),
        });
      });

    return () => controller.abort();
  }, [nonce]);

  const reload = useCallback(() => setNonce((value) => value + 1), []);
  const value = useMemo<SessionContextValue>(() => ({ state, reload }), [state, reload]);

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

function useSessionContext(): SessionContextValue {
  const context = useContext(SessionContext);
  if (!context) {
    throw new Error("useSession must be used inside a SessionProvider");
  }
  return context;
}

export function useSessionState(): SessionState {
  return useSessionContext().state;
}

export function useReloadSession(): () => void {
  return useSessionContext().reload;
}

/**
 * The authenticated session.
 *
 * Throws when called outside an authenticated tree. That is deliberate: the
 * router only mounts the application once `authenticated` is true, so a
 * component reaching this while signed out is a routing bug, and returning a
 * null session would spread that bug across every caller instead.
 */
export function useSession() {
  const { state } = useSessionContext();
  if (state.status !== "ready" || !state.session.authenticated) {
    throw new Error("useSession requires an authenticated session");
  }
  return state.session;
}

/** True when the signed-in user holds every permission given. */
export function useCan(...required: Permission[]): boolean {
  const session = useSession();
  const held = useMemo(() => new Set(session.permissions), [session.permissions]);
  return required.every((permission) => held.has(permission));
}

/** Render children only when the user holds the permission. */
export function Can({
  permission,
  children,
  fallback = null,
}: {
  permission: Permission;
  children: ReactNode;
  fallback?: ReactNode;
}) {
  return useCan(permission) ? <>{children}</> : <>{fallback}</>;
}

/** True when an error means the session ended and the user should sign in again. */
export function isSessionExpired(error: unknown): boolean {
  return error instanceof ApiError && error.isUnauthenticated;
}
