/**
 * Accessible overlay primitives: a centred modal and a right-hand drawer.
 *
 * Both trap focus, close on Escape, restore focus to whatever was focused when
 * they opened, and label themselves for assistive technology. `window.confirm`
 * and `window.prompt` did none of this and blocked the main thread; edit panels
 * were previously inline cards with no focus handling. These are the shared
 * shells the confirm dialog and the per-tab edit drawers build on.
 */

import { useCallback, useEffect, useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

import styles from "./foundations.module.css";

const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Trap Tab focus inside `ref`, close on Escape, and restore focus on unmount.
 * Shared by both shells so their behaviour cannot drift apart.
 */
function useDialogBehaviour(
  ref: React.RefObject<HTMLElement | null>,
  onClose: () => void,
) {
  const previouslyFocused = useRef<HTMLElement | null>(null);
  // Callers pass an inline `onClose`. Depending on it re-ran the effect on every
  // parent render, which sent focus back to the opener and then to the first
  // control -- off the field being typed in -- each time the screen refreshed.
  const closeRef = useRef(onClose);
  useEffect(() => {
    closeRef.current = onClose;
  });

  useEffect(() => {
    previouslyFocused.current = document.activeElement as HTMLElement | null;
    const node = ref.current;
    if (node) {
      const first = node.querySelector<HTMLElement>(FOCUSABLE);
      (first ?? node).focus();
    }

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        closeRef.current();
        return;
      }
      if (event.key !== "Tab" || !ref.current) return;
      const focusable = Array.from(ref.current.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (el) => el.offsetParent !== null || el === document.activeElement,
      );
      if (!focusable.length) {
        event.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!first || !last) return;
      const active = document.activeElement;
      if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      previouslyFocused.current?.focus?.();
    };
  }, [ref]);
}

export function Modal({
  title,
  children,
  footer,
  onClose,
  danger = false,
  wide = false,
}: {
  title: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  onClose: () => void;
  danger?: boolean;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useDialogBehaviour(ref, onClose);

  const onOverlayClick = useCallback(
    (event: React.MouseEvent) => {
      if (event.target === event.currentTarget) onClose();
    },
    [onClose],
  );

  if (typeof document === "undefined") return null;
  return createPortal(
    <div className={styles.overlay} onMouseDown={onOverlayClick}>
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={`${styles.dialog} ${danger ? styles.dialogDanger : ""} ${wide ? styles.dialogWide : ""}`}
      >
        <div className={styles.dialogHead}>
          <h2 id={titleId} className={styles.dialogTitle}>
            {title}
          </h2>
        </div>
        <div className={styles.dialogBody}>{children}</div>
        {footer && <div className={styles.dialogFoot}>{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}

export function Drawer({
  title,
  children,
  onClose,
}: {
  title: ReactNode;
  children: ReactNode;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useDialogBehaviour(ref, onClose);

  const onOverlayClick = useCallback(
    (event: React.MouseEvent) => {
      if (event.target === event.currentTarget) onClose();
    },
    [onClose],
  );

  if (typeof document === "undefined") return null;
  return createPortal(
    <div className={styles.drawerOverlay} onMouseDown={onOverlayClick}>
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={styles.drawer}
      >
        <div className={styles.dialogHead}>
          <h2 id={titleId} className={styles.dialogTitle}>
            {title}
          </h2>
        </div>
        <div className={styles.dialogBody} style={{ flex: 1 }}>
          {children}
        </div>
      </div>
    </div>,
    document.body,
  );
}
