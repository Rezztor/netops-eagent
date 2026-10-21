/**
 * Notifications.
 *
 * The list arrives on the same `state_push` snapshot as the conversation, so
 * there is nothing to poll — the badge updates as events happen.
 *
 * Kept in the topbar rather than as a screen because a notification is
 * something you are told, not somewhere you go.
 */

import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import {
  clearNotifications,
  markAllNotificationsRead,
  markNotificationsRead,
  type Notification,
} from "@/api/chat";
import { Button, Tag, useToast } from "@/components";
import styles from "./notifications.module.css";

function toneFor(notification: Notification): "ok" | "warn" | "crit" | "muted" {
  const type = String(notification.type ?? "").toLowerCase();
  if (type.includes("error")) return "crit";
  if (type.includes("warning")) return "warn";
  if (type.includes("success")) return "ok";
  return "muted";
}

export function Notifications({
  notifications,
  onChanged,
}: {
  notifications: Notification[];
  onChanged: () => void;
}) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const rootRef = useRef<HTMLDivElement | null>(null);
  const unread = notifications.filter((n) => !n.read).length;

  // Close on an outside click or Escape. A panel that traps the pointer is
  // worse than one that closes when it should not have.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    try {
      await action();
      onChanged();
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  // Opening a notification reveals its detail and marks it read -- the way an
  // item leaves the unread count without a separate "mark read" per row.
  function onOpen(notification: Notification) {
    const key = notification.id || String(notification.no);
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
    if (!notification.read && notification.id) {
      void run(() => markNotificationsRead([notification.id]));
    }
  }

  return (
    <div className={styles.root} ref={rootRef}>
      <Button
        variant="ghost"
        aria-label={unread ? `Notifications, ${unread} unread` : "Notifications"}
        aria-expanded={open}
        title="Notifications"
        onClick={() => setOpen((value) => !value)}
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
          <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
          <path d="M13.7 21a2 2 0 0 1-3.4 0" />
        </svg>
        {unread > 0 && <span className={styles.badge}>{unread > 99 ? "99+" : unread}</span>}
      </Button>

      {open && (
        <div className={styles.panel} role="dialog" aria-label="Notifications">
          <div className={styles.panelHead}>
            <strong style={{ flex: 1, fontSize: "var(--text-sm)" }}>
              Notifications{unread > 0 ? ` · ${unread} unread` : ""}
            </strong>
            <Button
              disabled={busy || unread === 0}
              onClick={() => void run(markAllNotificationsRead)}
            >
              Mark read
            </Button>
            <Button
              disabled={busy || notifications.length === 0}
              onClick={() => void run(clearNotifications)}
            >
              Clear
            </Button>
          </div>

          <div className={styles.list}>
            {notifications.length === 0 ? (
              <p className={styles.empty}>Nothing to report.</p>
            ) : (
              notifications
                .slice()
                .sort((a, b) => b.no - a.no)
                .map((notification) => {
                  const key = notification.id || String(notification.no);
                  const isOpen = expanded.has(key);
                  return (
                    <div
                      key={key}
                      className={notification.read ? styles.item : `${styles.item} ${styles.unread}`}
                      role="button"
                      tabIndex={0}
                      aria-expanded={notification.detail ? isOpen : undefined}
                      style={{ cursor: "pointer" }}
                      onClick={() => onOpen(notification)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          onOpen(notification);
                        }
                      }}
                    >
                      <div className={styles.itemHead}>
                        <Tag tone={toneFor(notification)}>{notification.type ?? "info"}</Tag>
                        <span className={styles.itemTitle}>{notification.title || "Notification"}</span>
                        <span className={styles.itemTime}>{notification.display_time ?? ""}</span>
                      </div>
                      {notification.message && (
                        <div className={styles.itemMessage}>{notification.message}</div>
                      )}
                      {/* In-app paths only: a link is the server's, but nothing here opens another site. */}
                      {notification.link?.startsWith("/") && !notification.link.startsWith("//") && (
                        <Link
                          to={notification.link}
                          className={styles.itemMessage}
                          onClick={(event) => {
                            event.stopPropagation();
                            if (!notification.read && notification.id) void run(() => markNotificationsRead([notification.id]));
                            setOpen(false);
                          }}
                        >
                          Open
                        </Link>
                      )}
                      {notification.detail && isOpen && (
                        <div
                          className={styles.itemMessage}
                          style={{ marginTop: "var(--space-2)", color: "var(--ink-3)", whiteSpace: "pre-wrap" }}
                        >
                          {notification.detail}
                        </div>
                      )}
                    </div>
                  );
                })
            )}
          </div>
        </div>
      )}
    </div>
  );
}
