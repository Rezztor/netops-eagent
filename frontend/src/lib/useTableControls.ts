/**
 * Search, filter, sort and paginate a list -- once, for every list screen.
 *
 * List-heavy tabs (Users, Skills, Schedules, Activity, Plugins, Network jobs)
 * each reinvented some subset of this and usually skipped most of it. The pure
 * `selectRows` does the work and is unit-tested without a DOM; `useTableControls`
 * wraps it with the small amount of React state a screen needs and keeps the
 * page index honest when the underlying list shrinks.
 */

import { useMemo, useState } from "react";

export type SortDirection = "asc" | "desc";

export interface SortState {
  key: string;
  direction: SortDirection;
}

export interface TableControlsOptions<T> {
  /** Fields to match the free-text query against, per item. */
  search?: (item: T) => Array<string | number | null | undefined>;
  /** Named comparators, selected by `sort.key`. Ascending order; the hook flips them for "desc". */
  comparators?: Record<string, (a: T, b: T) => number>;
  /** An extra predicate applied before search (e.g. a status dropdown). */
  filter?: (item: T) => boolean;
  initialSort?: SortState | null;
  initialQuery?: string;
  pageSize?: number;
}

function tokenize(query: string): string[] {
  return query.trim().toLowerCase().split(/\s+/).filter(Boolean);
}

/** True when every whitespace-separated token appears in at least one field. */
export function matchesQuery(fields: Array<string | number | null | undefined>, query: string): boolean {
  const tokens = tokenize(query);
  if (!tokens.length) return true;
  const haystack = fields
    .filter((f) => f !== null && f !== undefined)
    .map((f) => String(f).toLowerCase());
  return tokens.every((token) => haystack.some((field) => field.includes(token)));
}

export interface SelectOptions<T> {
  query?: string | undefined;
  filter?: ((item: T) => boolean) | undefined;
  search?: ((item: T) => Array<string | number | null | undefined>) | undefined;
  comparators?: Record<string, (a: T, b: T) => number> | undefined;
  sort?: SortState | null | undefined;
}

/** The filtered + sorted list, with no paging applied. Pure and testable. */
export function selectRows<T>(items: readonly T[], options: SelectOptions<T>): T[] {
  const { query = "", filter, search, comparators, sort } = options;
  let rows = items.slice();
  if (filter) rows = rows.filter(filter);
  if (query && search) rows = rows.filter((item) => matchesQuery(search(item), query));
  if (sort && comparators && comparators[sort.key]) {
    const compare = comparators[sort.key];
    rows.sort(compare);
    if (sort.direction === "desc") rows.reverse();
  }
  return rows;
}

export interface TableControls<T> {
  query: string;
  setQuery: (value: string) => void;
  sort: SortState | null;
  setSort: (value: SortState | null) => void;
  /** Toggle a column: none -> asc -> desc -> asc, always resetting other columns. */
  toggleSort: (key: string, initialDirection?: SortDirection) => void;
  page: number;
  setPage: (value: number) => void;
  pageCount: number;
  /** Rows after search/filter/sort but before paging. */
  filtered: T[];
  /** The rows to render for the current page. */
  rows: T[];
  total: number;
  matched: number;
}

export function useTableControls<T>(
  items: readonly T[],
  options: TableControlsOptions<T> = {},
): TableControls<T> {
  const { search, comparators, filter, initialSort = null, initialQuery = "", pageSize = 0 } = options;
  const [query, setQueryRaw] = useState(initialQuery);
  const [sort, setSort] = useState<SortState | null>(initialSort);
  const [page, setPageRaw] = useState(0);

  const filtered = useMemo(
    () => selectRows(items, { query, filter, search, comparators, sort }),
    [items, query, filter, search, comparators, sort],
  );

  const pageCount = pageSize > 0 ? Math.max(1, Math.ceil(filtered.length / pageSize)) : 1;
  // A shrinking list must not strand the view on an empty trailing page.
  const safePage = Math.min(page, pageCount - 1);
  const rows = pageSize > 0 ? filtered.slice(safePage * pageSize, safePage * pageSize + pageSize) : filtered;

  const setQuery = (value: string) => {
    setQueryRaw(value);
    setPageRaw(0);
  };

  const toggleSort = (key: string, initialDirection: SortDirection = "asc") => {
    setSort((prev) => {
      if (!prev || prev.key !== key) return { key, direction: initialDirection };
      if (prev.direction === initialDirection) {
        return { key, direction: initialDirection === "asc" ? "desc" : "asc" };
      }
      return { key, direction: initialDirection };
    });
    setPageRaw(0);
  };

  return {
    query,
    setQuery,
    sort,
    setSort,
    toggleSort,
    page: safePage,
    setPage: setPageRaw,
    pageCount,
    filtered,
    rows,
    total: items.length,
    matched: filtered.length,
  };
}
