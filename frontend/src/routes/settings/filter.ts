/**
 * The index's two derived lists: what the filter matches, and what needs
 * attention.
 *
 * Apart from the screen so they can be tested without rendering one. They are
 * where the index is most likely to be quietly wrong -- a filter that misses
 * the section someone is looking for, or an attention list that cries wolf --
 * and both are pure functions of the registry and the loaded payloads.
 */

import type { SettingsContext, SettingsSection } from "./types";

/**
 * Case-insensitive substring match over the words a person would actually
 * type. Keywords carry the vocabulary the title does not: someone looking for
 * "smtp" or "syslog" is looking for Email and SIEM forwarding, and neither
 * word appears in either title.
 */
export function matchesQuery(section: SettingsSection, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return [section.title, section.group, section.summary, ...(section.keywords ?? [])]
    .join(" ")
    .toLowerCase()
    .includes(needle);
}

export interface AttentionEntry {
  section: SettingsSection;
  text: string;
}

/** Sections reporting a state an administrator would act on, in registry order. */
export function collectAttention(
  sections: SettingsSection[],
  ctx: SettingsContext,
): AttentionEntry[] {
  return sections
    .map((section) => ({ section, text: section.attention?.(ctx) ?? null }))
    .filter((entry): entry is AttentionEntry => entry.text !== null);
}

/** Sections this account may see, with any that hide themselves removed. */
export function visibleSections(
  sections: SettingsSection[],
  ctx: SettingsContext,
  held: Set<string>,
  /** While the payloads are still loading, `visible` cannot be judged yet. */
  loading = false,
): SettingsSection[] {
  return sections.filter(
    (section) =>
      (!section.permission || held.has(section.permission)) &&
      (!section.visible || loading || section.visible(ctx)),
  );
}
