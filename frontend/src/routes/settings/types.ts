/**
 * What a settings section is.
 *
 * Kept apart from `registry.tsx` on purpose: the registry imports every
 * section component, and the sections import these types. Putting both in one
 * file makes that a cycle.
 *
 * A section declares itself and the screen renders whatever is declared. The
 * point is that adding one -- a plugin's configuration, most often -- is a new
 * file plus one entry, not an edit to a shared screen that every other section
 * also lives in.
 *
 * This is not a plugin UI extension mechanism and does not reopen ADR 0014.
 * Nothing is loaded at runtime and nothing third-party contributes interface: a
 * section is ordinary code in `frontend/src`, reviewed in this repository like
 * any other screen. All that changed is that the render order is declared
 * rather than hard-coded.
 */

import type { ComponentType } from "react";

import type { SettingsResponse } from "@/api/admin";
import type { getAzureConfig } from "@/api/azure";
import type { NetworkState } from "@/api/networkAutomation";
import type { SiemForwardState } from "@/api/siemForward";
import type { SolarWindsConfig } from "@/api/solarwinds";
import type { AuthenticatedBootstrap, Permission } from "@/api/types";
import type { TagTone } from "@/components";

/**
 * Ordered as they appear in the index.
 *
 * The top-level split is who may change a setting, which is the question the
 * previous screen already answered correctly. Sub-grouping within it is what
 * is new: unnecessary at three sections, unavoidable at eleven.
 *
 * `Integrations` is the group that absorbs plugins. It is the only one
 * expected to grow, which is why it is a group rather than a handful of
 * sections filed under Administration.
 */
export type SectionGroup = "Personal" | "Administration" | "Integrations" | "Product";

export const SECTION_GROUPS: readonly SectionGroup[] = [
  "Personal",
  "Administration",
  "Integrations",
  "Product",
];

/** What each group's endpoints require, for the scope note on every section. */
export const GROUP_PERMISSION: Record<SectionGroup, string | null> = {
  Personal: null,
  Administration: "settings.manage",
  Integrations: "plugins.manage",
  Product: "settings.manage",
};

export type SolarWindsState = { config: SolarWindsConfig; passwordSet: boolean };

export type AzureState = Awaited<ReturnType<typeof getAzureConfig>>;

/**
 * Everything the index needs to draw every chip, loaded once by the screen.
 *
 * A field is null when it is still loading, when the request failed, or when
 * the user may not read it -- and a chip that cannot be computed is not shown
 * at all. That distinction matters more than it looks: a stale or guessed
 * "connected" on the index is worse than no chip, because the whole reason the
 * index carries state is so it can be believed without opening the section.
 */
export interface SettingsContext {
  session: AuthenticatedBootstrap;
  settings: SettingsResponse | null;
  solarwinds: SolarWindsState | null;
  siem: SiemForwardState | null;
  network: NetworkState | null;
  azure: AzureState | null;
  /** Re-fetches the shared payloads after a section writes. */
  reload: () => void;
}

export interface SectionChip {
  tone: TagTone;
  text: string;
}

export interface SettingsSection {
  /** Route segment: `/settings/<id>`. Stable -- documentation links it. */
  id: string;
  title: string;
  group: SectionGroup;
  /** One line, shown on the overview card. */
  summary: string;
  /** Hidden entirely unless the user holds this. */
  permission?: Permission;
  /** Extra filter terms beyond the title, summary and field labels. */
  keywords?: string[];
  Component: ComponentType<{ ctx: SettingsContext }>;
  /** The index chip. Return null when the state is not known yet. */
  chip?: (ctx: SettingsContext) => SectionChip | null;
  /**
   * A sentence for the "needs attention" list, or null when nothing is wrong.
   * Reserve it for a state an administrator would want to act on -- a warning
   * that is always present is noise, and noise is how a real one gets missed.
   */
  attention?: (ctx: SettingsContext) => string | null;
  /** Hides the section outright, e.g. a plugin switched off in Plugins. */
  visible?: (ctx: SettingsContext) => boolean;
}
