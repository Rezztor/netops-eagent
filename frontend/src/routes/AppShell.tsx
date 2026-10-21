/**
 * The signed-in shell: rail, topbar, routed content.
 */

import { createContext, lazy, Suspense, useContext, useEffect, useRef, useState } from "react";
import { Link, Navigate, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";

import { signOut } from "@/api/client";
import { getInventorySummary } from "@/api/inventorySummary";
import { PERMISSIONS } from "@/api/types";
import { Button, CenteredMessage, ScopeNote } from "@/components";
import { isFocused, withFocus } from "@/lib/focus";
import { useSession } from "@/lib/session";
import { RealtimeProvider, useRealtime } from "@/lib/realtime";
import { useSidebar } from "@/lib/useSidebar";
import { Notifications } from "./Notifications";
import { NAV_GROUPS, NAV_ITEMS } from "./nav";
import { OverviewScreen } from "./OverviewScreen";
import { ChatScreen } from "./ChatScreen";
import { ThemeToggle } from "./ThemeToggle";
import styles from "./shell.module.css";

/**
 * The version as a person reads it. The backend prefixes the branch's initial
 * ("U v2026.10.17+14" on uat, "M ..." on main), which is for whoever built it.
 */
export function displayVersion(version: string): string {
  return version.replace(/^[A-Z] (?=\S)/, "");
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).slice(0, 2);
  return parts.map((p) => p[0]?.toUpperCase() ?? "").join("") || "?";
}

const PinIcon = (
  <svg
    width="14"
    height="14"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.8"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <path d="M12 17v5" />
    <path d="M9 2h6l-1.5 7 4 4H6.5l4-4L9 2z" />
  </svg>
);

const ChevronIcon = (
  <svg
    width="14"
    height="14"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.8"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <path d="M9 6l6 6-6 6" />
  </svg>
);

// Loaded when first opened. Home and Chat are where everyone lands, so they
// stay in the first bundle; the administration and inventory screens, which
// most sessions never open, used to be in it too (one 799 KB script).
const UsersScreen = lazy(() => import("./UsersScreen").then((m) => ({ default: m.UsersScreen })));
const PluginsScreen = lazy(() => import("./PluginsScreen").then((m) => ({ default: m.PluginsScreen })));
const ModelsScreen = lazy(() => import("./ModelsScreen").then((m) => ({ default: m.ModelsScreen })));
const SettingsScreen = lazy(() => import("./settings/SettingsScreen").then((m) => ({ default: m.SettingsScreen })));
const ActivityScreen = lazy(() => import("./ActivityScreen").then((m) => ({ default: m.ActivityScreen })));
const NetworkScreen = lazy(() => import("./NetworkScreen").then((m) => ({ default: m.NetworkScreen })));
const InventoryScreen = lazy(() => import("./InventoryScreen").then((m) => ({ default: m.InventoryScreen })));
const OperationsScreen = lazy(() => import("./OperationsScreen").then((m) => ({ default: m.OperationsScreen })));
const SkillsScreen = lazy(() => import("./SkillsScreen").then((m) => ({ default: m.SkillsScreen })));
const SchedulesScreen = lazy(() => import("./SchedulesScreen").then((m) => ({ default: m.SchedulesScreen })));
const ApprovalsScreen = lazy(() => import("./ApprovalsScreen").then((m) => ({ default: m.ApprovalsScreen })));
const DriftScreen = lazy(() => import("./DriftScreen").then((m) => ({ default: m.DriftScreen })));
const FilesScreen = lazy(() => import("./FilesScreen").then((m) => ({ default: m.FilesScreen })));

/** At or below this width the rail is a drawer behind a menu button (shell.module.css says the same). */
const NARROW = "(max-width: 1080px)";

function useNarrow(): boolean {
  const supported = typeof window !== "undefined" && typeof window.matchMedia === "function";
  const [narrow, setNarrow] = useState(() => supported && window.matchMedia(NARROW).matches);
  useEffect(() => {
    if (!supported) return;
    const query = window.matchMedia(NARROW);
    const update = () => setNarrow(query.matches);
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, [supported]);
  return narrow;
}

/** The menu button lives in each page's top bar; the drawer it opens lives in the shell. */
const MenuContext = createContext<{ open: boolean; toggle: () => void }>({ open: false, toggle: () => {} });

export function AppShell() {
  return (
    <RealtimeProvider>
      <Shell />
    </RealtimeProvider>
  );
}

function Shell() {
  const session = useSession();
  const location = useLocation();
  const navigate = useNavigate();
  const { connection } = useRealtime();
  const { pinned: pinnedSetting, setPinned, peeking, setPeeking } = useSidebar();
  const narrow = useNarrow();
  const [menuOpen, setMenuOpen] = useState(false);
  // In the drawer the rail is always shown whole: the icon-only rail is a
  // desktop space saving, and peeking is a hover behaviour a phone has not got.
  const pinned = pinnedSetting || narrow;
  const held = new Set(session.permissions);
  const visible = NAV_ITEMS.filter((item) => !item.permission || held.has(item.permission));
  const railRef = useRef<HTMLElement>(null);

  // Proposals and conflicts waiting for a person. Read again when the user enters or leaves the screen.
  const [toReview, setToReview] = useState(0);
  const inInventory = location.pathname.startsWith("/inventory");
  const canSeeInventory = held.has(PERMISSIONS.inventoryView);
  useEffect(() => {
    if (!canSeeInventory) return;
    let live = true;
    getInventorySummary()
      .then((r) => live && setToReview(r.ok ? r.pending + r.conflicts : 0))
      .catch(() => live && setToReview(0));
    return () => {
      live = false;
    };
  }, [canSeeInventory, inInventory]);
  const peekOpenTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const peekCloseTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Focus mode gives the whole window to one conversation, so the rail goes
  // with everything else. Escape is the way back, because a mode that hides the
  // navigation has to have an exit that does not need it.
  const focused = isFocused(location.search) && location.pathname.startsWith("/chat");

  useEffect(() => {
    if (!focused) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") navigate({ search: withFocus(location.search, false) });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [focused, location.search, navigate]);

  // Peek and the drawer close when the route changes.
  useEffect(() => {
    setPeeking(false);
    setMenuOpen(false);
  }, [location.pathname, setPeeking]);

  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  useEffect(() => {
    if (!peeking) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setPeeking(false);
    };
    const onPointer = (event: PointerEvent) => {
      if (railRef.current && !railRef.current.contains(event.target as Node)) {
        setPeeking(false);
      }
    };
    window.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointer);
    return () => {
      window.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointer);
    };
  }, [peeking, setPeeking]);

  useEffect(
    () => () => {
      if (peekOpenTimer.current) clearTimeout(peekOpenTimer.current);
      if (peekCloseTimer.current) clearTimeout(peekCloseTimer.current);
    },
    [],
  );

  const clearPeekTimers = () => {
    if (peekOpenTimer.current) {
      clearTimeout(peekOpenTimer.current);
      peekOpenTimer.current = null;
    }
    if (peekCloseTimer.current) {
      clearTimeout(peekCloseTimer.current);
      peekCloseTimer.current = null;
    }
  };

  const onRailPointerEnter = () => {
    if (pinned) return;
    clearPeekTimers();
    peekOpenTimer.current = setTimeout(() => setPeeking(true), 150);
  };

  const onRailPointerLeave = () => {
    if (pinned) return;
    clearPeekTimers();
    peekCloseTimer.current = setTimeout(() => setPeeking(false), 120);
  };

  const onRailFocusIn = () => {
    if (pinned) return;
    clearPeekTimers();
    setPeeking(true);
  };

  const onRailFocusOut = (event: React.FocusEvent) => {
    if (pinned) return;
    const next = event.relatedTarget as Node | null;
    if (next && railRef.current?.contains(next)) return;
    clearPeekTimers();
    setPeeking(false);
  };

  // Only what this page can measure: whether its live connection to the server
  // is up. "All systems operational" used to be printed whenever the instance
  // was not in development mode, a claim nothing checked.
  const statusLabel = session.runtime.is_development
    ? "Development mode"
    : connection === "live"
      ? "Connected"
      : connection === "connecting"
        ? "Connecting…"
        : "Live updates unavailable";
  const statusDetail = `${session.runtime.is_dockerized ? "Containerised" : "Local runtime"} · ${session.preferences.timezone}`;

  const shellClass = `${styles.shell} ${focused ? styles.shellFocus ?? "" : ""}`;
  const sidebarMode = narrow ? "drawer" : pinned ? "pinned" : "rail";

  return (
    <div
      className={shellClass}
      data-sidebar={focused ? undefined : sidebarMode}
      data-peeking={!pinned && peeking ? "true" : undefined}
      data-menu={narrow && menuOpen ? "open" : undefined}
    >
      <a href="#content" className="skip-link">Skip to content</a>
      {narrow && menuOpen && (
        <div className={styles.backdrop} aria-hidden="true" onClick={() => setMenuOpen(false)} />
      )}
      {!focused && (
        <div className={styles.railSlot}>
          <aside
            ref={railRef}
            className={`${styles.rail} ${!pinned && peeking ? styles.railPeeking : ""}`}
            onPointerEnter={onRailPointerEnter}
            onPointerLeave={onRailPointerLeave}
            onFocusCapture={onRailFocusIn}
            onBlurCapture={onRailFocusOut}
          >
            <div className={styles.brand}>
              <div className={styles.brandMark}>R</div>
              <div className={styles.brandText}>
                <div className={styles.brandName}>Rezztor EAgent</div>
                <div className={styles.brandSub}>{displayVersion(session.version.version)}</div>
              </div>
              {narrow ? (
                <button
                  type="button"
                  className={styles.closeMenu}
                  aria-label="Close menu"
                  onClick={() => setMenuOpen(false)}
                >
                  ×
                </button>
              ) : null}
              <button
                type="button"
                className={styles.pinBtn}
                aria-pressed={pinned}
                aria-label={pinned ? "Unpin sidebar" : "Pin sidebar"}
                onClick={() => setPinned(!pinned)}
              >
                {PinIcon}
              </button>
            </div>

            <nav className={styles.nav} aria-label="Main">
              {NAV_GROUPS.map((group) => {
                const items = visible.filter((item) => item.group === group);
                if (!items.length) return null;
                return (
                  <div key={group}>
                    <div className={styles.navGroup}>{group}</div>
                    {items.map((item) => {
                      const isAdmin = item.group === "Administration";
                      return (
                        <NavLink
                          key={item.to}
                          to={item.to}
                          end={item.to === "/"}
                          aria-label={item.label}
                          data-tip={item.label}
                          title={!pinned && !peeking ? item.label : undefined}
                          className={({ isActive }) =>
                            isActive
                              ? `${styles.navItem} ${styles.navItemActive}`
                              : styles.navItem
                          }
                        >
                          {item.icon}
                          <span className={styles.navLabel}>{item.label}</span>
                          {item.to === "/inventory" && toReview > 0 ? (
                            <span className={styles.navBadge} title={`${toReview} waiting for review`} data-testid="inventory-review-count">
                              {toReview}
                            </span>
                          ) : null}
                          {isAdmin ? (
                            <>
                              <span className={styles.navLock}>admin</span>
                              <span className={styles.adminDot} aria-hidden="true" />
                            </>
                          ) : null}
                        </NavLink>
                      );
                    })}
                  </div>
                );
              })}
            </nav>

            <div className={styles.railFoot}>
              {!pinned ? (
                <button
                  type="button"
                  className={styles.peekBtn}
                  aria-expanded={peeking}
                  aria-label="Show full sidebar"
                  onClick={() => setPeeking(true)}
                >
                  {ChevronIcon}
                </button>
              ) : null}
              <div className={styles.health} title={`${statusLabel}. ${statusDetail}`}>
                <div className={styles.healthRow}>
                  <span
                    className={
                      session.runtime.is_development || connection !== "live"
                        ? `${styles.dot} ${styles.dotWarn}`
                        : styles.dot
                    }
                  />
                  <span className={styles.healthText}>{statusLabel}</span>
                </div>
                <div className={styles.healthNote}>{statusDetail}</div>
              </div>
            </div>
          </aside>
        </div>
      )}

      <MenuContext.Provider value={{ open: menuOpen, toggle: () => setMenuOpen((v) => !v) }}>
      <div className={styles.main}>
        <LicenceBanner />
        <UpdateBanner />
        <Routes>
          <Route path="/" element={<Framed title="Overview" sub="What needs doing, and what you can do here"><OverviewScreen /></Framed>} />
          <Route path="/chat" element={<Framed title="Chat" sub="Conversations with the agent" full><ChatScreen /></Framed>} />
          <Route path="/files" element={<Framed title="Files" sub="Your working directory"><FilesScreen /></Framed>} />
          <Route path="/skills" element={<Framed title="Skills" sub="What the agent can be told to do"><SkillsScreen /></Framed>} />
          <Route path="/schedules" element={<Framed title="My schedules" sub="Your reminders and unattended work, at a glance"><SchedulesScreen /></Framed>} />
          <Route path="/users" element={<Framed title="Users" sub="Accounts and role assignment"><UsersScreen /></Framed>} />
          <Route path="/plugins" element={<Framed title="Plugins" sub="Capabilities available to every agent"><PluginsScreen /></Framed>} />
          <Route path="/models" element={<Framed title="Models" sub="Which model answers, and with whose credentials"><ModelsScreen /></Framed>} />
          {/* Sections are addresses -- /settings/licence, /settings/email -- so
              a guide can link one and a test can assert it. `full` because the
              screen owns its own scrolling: the index and the pane each scroll,
              the page itself does not. */}
          <Route path="/settings/*" element={<Framed title="Settings" sub="Platform configuration and personal preferences" full><SettingsScreen /></Framed>} />
          <Route path="/system" element={<Framed title="System" sub="Everyone's scheduled tasks, MCP servers, agent profiles, projects and memory"><OperationsScreen /></Framed>} />
          {/* The screen was called Operations; old bookmarks still land. */}
          <Route path="/operations" element={<Navigate to="/system" replace />} />
          <Route path="/approvals" element={<Framed title="Approvals" sub="Changes waiting for your signature"><ApprovalsScreen /></Framed>} />
          <Route path="/network" element={<Framed title="Network" sub="What ran, what is waiting, and what the executor holds"><NetworkScreen /></Framed>} />
          <Route path="/inventory" element={<Framed title="Inventory" sub="Devices, sites, proposed changes and cloud resources"><InventoryScreen /></Framed>} />
          <Route path="/drift" element={<Framed title="Drift" sub="Configuration that changed, and whether an approved change explains it"><DriftScreen /></Framed>} />
          <Route path="/activity" element={<Framed title="Activity" sub="Recent platform events"><ActivityScreen /></Framed>} />
          <Route
            path="*"
            element={<CenteredMessage title="Page not found" detail="That address does not exist." />}
          />
        </Routes>
      </div>
      </MenuContext.Provider>
    </div>
  );
}

/**
 * The app-wide licence banner.
 *
 * Grace 1 is shown to administrators only: everything works and they are the
 * ones who can act. Grace 2 and Restricted are shown to everybody, with what
 * still works, so nobody is left guessing why a run did not start. A licensed
 * installation is quiet unless its licence was revoked with notice. A Community
 * build is quiet: there is nothing to activate.
 *
 * The data comes from the session's bootstrap payload, so the banner needs no
 * extra request and loads in the restricted state too.
 */
function LicenceBanner() {
  const session = useSession();
  const licence = session.licence;
  const administrator = session.permissions.includes(PERMISSIONS.settingsManage);
  if (!licence || licence.state === "community") return null;

  if (licence.state === "restricted") {
    return (
      <div className={styles.banner}>
        <ScopeNote tone="crit">
          This installation is restricted. {licence.reason} New agent runs, device and cloud changes and
          scheduled tasks do not start; sign-in, reading, export and settings keep working. An
          administrator can fix this on the licence page (Settings, Licence).
        </ScopeNote>
      </div>
    );
  }

  const days = licence.grace_days_remaining;
  if (days !== null && (licence.state === "grace2" || administrator)) {
    return (
      <div className={styles.banner}>
        <ScopeNote tone={days <= 3 ? "crit" : "warn"}>
          The licence could not be renewed. Everything keeps working for {days}{" "}
          {days === 1 ? "day" : "days"} more; after that nothing new starts. See the licence page
          (Settings, Licence).
        </ScopeNote>
      </div>
    );
  }

  if (licence.reason && administrator) {
    return (
      <div className={styles.banner}>
        <ScopeNote tone="crit">{licence.reason}</ScopeNote>
      </div>
    );
  }

  return null;
}

/**
 * Staged update waiting for a restart, or else how many updates Rezztor's
 * update service lists. Administrators only — they are the ones who can act.
 * Data comes from the bootstrap payload, same as the licence banner; the
 * Updates page owns the actions.
 */
function UpdateBanner() {
  const session = useSession();
  const update = session.update;
  if (!session.permissions.includes(PERMISSIONS.settingsManage)) return null;

  if (update && update.phase === "staged") {
    return (
      <div className={styles.banner}>
        <ScopeNote tone="warn">
          Update {update.version} is installed and waits for a restart.{" "}
          <Link to="/settings/updates">Open Updates</Link>
        </ScopeNote>
      </div>
    );
  }

  // What Rezztor's update service listed when the host last asked (ADR 0091).
  const count = session.updatesAvailable ?? 0;
  if (count <= 0 || (update && update.phase !== "idle")) return null;
  return (
    <div className={styles.banner}>
      <ScopeNote>
        {count === 1 ? "1 update available." : `${count} updates available.`}{" "}
        <Link to="/settings/updates">Open Updates</Link>
      </ScopeNote>
    </div>
  );
}

function Framed({
  title,
  sub,
  full = false,
  children,
}: {
  title: string;
  sub: string;
  full?: boolean;
  children: React.ReactNode;
}) {
  const session = useSession();
  const menu = useContext(MenuContext);
  // A route change has to be told somewhere: the tab, the history list and a
  // screen reader all read the document title, which never used to change.
  useEffect(() => {
    document.title = `${title} · Rezztor EAgent`;
  }, [title]);
  return (
    <>
      <header className={styles.topbar}>
        <button
          type="button"
          className={styles.menuBtn}
          aria-label="Menu"
          aria-expanded={menu.open}
          onClick={menu.toggle}
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
            <path d="M4 6h16M4 12h16M4 18h16" />
          </svg>
        </button>
        <div className={styles.titles}>
          <h1 className={styles.pageTitle}>{title}</h1>
          <div className={styles.pageSub}>{sub}</div>
        </div>
        <div className={styles.spacer} />
        <NotificationBell />
        <ThemeToggle />
        <div className={styles.who}>
          <div className={styles.avatar}>{initials(session.user.display_name)}</div>
          <div className={styles.whoText}>
            <div className={styles.whoName}>{session.user.display_name}</div>
            <div className={styles.whoRole}>{session.user.role_title}</div>
          </div>
          <SignOutButton />
        </div>
      </header>
      <main id="content" tabIndex={-1} className={full ? styles.contentFull : styles.content}>
        {/* Not a CenteredMessage: that is an h1, and the page already has one. */}
        <Suspense fallback={<p role="status" className={styles.loading}>Loading…</p>}>{children}</Suspense>
      </main>
    </>
  );
}


/**
 * Sign out.
 *
 * A POST to `/api/logout` with the CSRF token, then the sign-in page. `GET
 * /logout` no longer signs out: a GET is something any other site can make the
 * browser send. The navigation happens whether or not the call succeeded -- a
 * session that is already gone has nothing left to end.
 */
function SignOutButton() {
  return (
    <Button
      variant="ghost"
      aria-label="Sign out"
      title="Sign out"
      onClick={signOut}
    >
      <svg
        width="16"
        height="16"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
        <path d="M16 17l5-5-5-5" />
        <path d="M21 12H9" />
      </svg>
    </Button>
  );
}


function NotificationBell() {
  const { state, setContext, contextId } = useRealtime();
  // Re-pointing the connection at the same conversation is how the shell asks
  // for a refresh without owning the socket.
  return (
    <Notifications
      notifications={state?.notifications ?? []}
      onChanged={() => setContext(contextId)}
    />
  );
}
