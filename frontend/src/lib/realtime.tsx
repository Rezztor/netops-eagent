/**
 * One live connection for the whole application.
 *
 * The pushed snapshot carries the conversation *and* the notification list, so
 * more than one screen needs it. A connection per screen would mean two sockets,
 * two projections on the server, and a notification badge that only updates
 * while the chat screen happens to be mounted.
 *
 * The provider owns the socket; screens read state and set the conversation.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { RealtimeState, type ConnectionState } from "@/api/realtime";
import type { PollResponse } from "@/api/chat";
import { useSession } from "./session";

interface RealtimeContextValue {
  state: PollResponse | null;
  connection: ConnectionState;
  /** Point the connection at a conversation. "" means none selected. */
  setContext: (contextId: string) => void;
  contextId: string;
  /**
   * The connection itself, for surfaces with their own handler activated on it.
   *
   * Exposed rather than wrapped because those surfaces have their own protocols
   * -- the browser viewer alone has seven events -- and proxying each one
   * through here would put a plugin's vocabulary in the shared layer.
   */
  realtime: RealtimeState | null;
}

const RealtimeContext = createContext<RealtimeContextValue | null>(null);

export function RealtimeProvider({ children }: { children: ReactNode }) {
  const session = useSession();
  const [state, setState] = useState<PollResponse | null>(null);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [contextId, setContextIdState] = useState("");
  // Held in state rather than a ref: consumers read it through the context, and
  // a ref assignment does not re-render, so they would see null forever.
  const [realtime, setRealtime] = useState<RealtimeState | null>(null);
  const realtimeRef = useRef<RealtimeState | null>(null);
  // A screen's effects run before this provider's, so a conversation named on
  // first render (a reload, a shared link) arrives before the connection
  // exists. Kept here so the connection opens on it instead of on nothing.
  const contextIdRef = useRef("");

  useEffect(() => {
    const connection = new RealtimeState();
    realtimeRef.current = connection;
    setRealtime(connection);
    connection.connect(
      { context: contextIdRef.current, timezone: session.preferences.timezone },
      { onState: setState, onConnectionChange: setConnection },
    );
    return () => {
      connection.disconnect();
      realtimeRef.current = null;
      setRealtime(null);
    };
  }, [session.preferences.timezone]);

  const setContext = useCallback((next: string) => {
    contextIdRef.current = next;
    setContextIdState(next);
    realtimeRef.current?.setContext(next);
  }, []);

  const value = useMemo<RealtimeContextValue>(
    () => ({ state, connection, setContext, contextId, realtime }),
    [state, connection, setContext, contextId, realtime],
  );

  return <RealtimeContext.Provider value={value}>{children}</RealtimeContext.Provider>;
}

export function useRealtime(): RealtimeContextValue {
  const context = useContext(RealtimeContext);
  if (!context) {
    throw new Error("useRealtime must be used inside a RealtimeProvider");
  }
  return context;
}
