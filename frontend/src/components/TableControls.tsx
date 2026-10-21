/**
 * The visible widgets that pair with `lib/useTableControls`: a search box, a
 * sortable column header, a pagination bar and a standard empty row.
 *
 * Kept as small, composable pieces rather than one monolithic `<DataTable>` so
 * a screen keeps ownership of its own `<Table>` markup and column layout while
 * still getting consistent controls and a consistent empty state.
 */

import type { ReactNode } from "react";

import { Button } from "./index";
import type { SortState } from "@/lib/useTableControls";
import styles from "./foundations.module.css";

export function SearchInput({
  value,
  onChange,
  placeholder = "Search…",
  label = "Search",
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  label?: string;
}) {
  return (
    <div className={styles.search}>
      <svg
        className={styles.searchIcon}
        width="15"
        height="15"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <circle cx="11" cy="11" r="7" />
        <path d="M21 21l-4.3-4.3" />
      </svg>
      <input
        type="search"
        className={`${styles.searchInput}`}
        style={{
          width: "100%",
          border: "1px solid var(--line-2)",
          borderRadius: "var(--radius-sm)",
          padding: "6px 9px 6px 30px",
          background: "var(--surface)",
          color: "var(--ink)",
          fontSize: "var(--text-sm)",
          outline: "none",
        }}
        placeholder={placeholder}
        aria-label={label}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </div>
  );
}

/** A `<th>` whose contents toggle sorting on click and announce direction. */
export function SortHeader({
  columnKey,
  sort,
  onToggle,
  align = "left",
  children,
}: {
  columnKey: string;
  sort: SortState | null;
  onToggle: (key: string) => void;
  align?: "left" | "right";
  children: ReactNode;
}) {
  const active = sort?.key === columnKey;
  const direction = active ? sort?.direction : undefined;
  return (
    <th
      style={{ textAlign: align }}
      aria-sort={active ? (direction === "asc" ? "ascending" : "descending") : "none"}
    >
      <button type="button" className={styles.sortHeader} onClick={() => onToggle(columnKey)}>
        {children}
        <span className={styles.sortArrow} aria-hidden="true">
          {active ? (direction === "asc" ? "▲" : "▼") : "↕"}
        </span>
      </button>
    </th>
  );
}

export function Pagination({
  page,
  pageCount,
  onPage,
  total,
  matched,
  noun = "items",
}: {
  page: number;
  pageCount: number;
  onPage: (page: number) => void;
  total: number;
  matched: number;
  noun?: string;
}) {
  return (
    <div className={styles.pagination}>
      <span>
        {matched === total
          ? `${total} ${noun}`
          : `${matched} of ${total} ${noun}`}
      </span>
      <span className={styles.paginationSpacer} />
      {pageCount > 1 && (
        <>
          <Button disabled={page <= 0} onClick={() => onPage(page - 1)} aria-label="Previous page">
            Previous
          </Button>
          <span>
            Page {page + 1} of {pageCount}
          </span>
          <Button
            disabled={page >= pageCount - 1}
            onClick={() => onPage(page + 1)}
            aria-label="Next page"
          >
            Next
          </Button>
        </>
      )}
    </div>
  );
}

/** A full-width empty state for the body of a `<Table>`. */
export function EmptyRow({ colSpan, children }: { colSpan: number; children: ReactNode }) {
  return (
    <tr>
      <td colSpan={colSpan} className={styles.emptyRow}>
        {children}
      </td>
    </tr>
  );
}
