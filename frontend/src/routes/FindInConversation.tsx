/**
 * Find something in the conversation on screen.
 *
 * Not a reimplementation of browser find, which already works on what is
 * rendered. This exists for what the transcript hides: steps folded into a
 * closed group, and answers clamped at their character limit. It searches the
 * entries themselves and opens whatever needs opening to show you the match.
 *
 * It says how many matches are in folded content, because "3 matches" when you
 * can see one is confusing, and the explanation is the useful part.
 */

import { useEffect, useRef } from "react";

import type { Match } from "@/lib/transcript";
import styles from "./chat.module.css";

export function FindInConversation({
  term,
  onTerm,
  matches,
  active,
  onActive,
  onClose,
}: {
  term: string;
  onTerm: (value: string) => void;
  matches: Match[];
  /** Index into `matches`, or -1 when there is nothing to point at. */
  active: number;
  onActive: (index: number) => void;
  onClose: () => void;
}) {
  const input = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    input.current?.focus();
  }, []);

  const total = matches.length;
  const hidden = matches.filter((match) => match.folded).length;

  function step(by: number) {
    if (total === 0) return;
    onActive((active + by + total) % total);
  }

  // No search landmark: this sits inside the conversation's log region.
  return (
    <div className={styles.findBar}>
      <input
        ref={input}
        type="search"
        className={styles.findInput}
        placeholder="Find in this conversation…"
        aria-label="Find in this conversation"
        value={term}
        onChange={(event) => onTerm(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            step(event.shiftKey ? -1 : 1);
          }
          if (event.key === "Escape") onClose();
        }}
      />

      <span className={styles.findCount}>
        {term.trim().length < 2
          ? "type at least two characters"
          : total === 0
            ? "no matches"
            : `${active + 1} of ${total}`}
        {hidden > 0 && total > 0 && (
          <span className={styles.findHidden}>
            {" "}
            · {hidden} in collapsed steps
          </span>
        )}
      </span>

      <button
        type="button"
        className={styles.entryAction}
        onClick={() => step(-1)}
        disabled={total === 0}
        aria-label="Previous match"
      >
        ↑
      </button>
      <button
        type="button"
        className={styles.entryAction}
        onClick={() => step(1)}
        disabled={total === 0}
        aria-label="Next match"
      >
        ↓
      </button>
      <button type="button" className={styles.entryAction} onClick={onClose} aria-label="Close find">
        ✕
      </button>
    </div>
  );
}
