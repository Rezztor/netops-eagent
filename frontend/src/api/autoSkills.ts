/**
 * Auto Skills: the queue between a skill the agent induced from one person's
 * work and the shared library every account's agent reads.
 *
 * Both endpoints require `plugins.manage`, enforced server-side in
 * `helpers/api_policy.py`. The screen hides the controls without it so it does
 * not offer an action that would fail.
 */

import { apiRequest } from "./client";

export type PromotionStatus = "pending" | "promoted" | "rejected";

export interface Promotion {
  id: string;
  status: PromotionStatus;
  name: string;
  description?: string;
  rationale?: string;
  action?: string;
  owner_user_id?: string;
  source_context?: string;
  tool_calls?: number;
  evidence_digest?: string;
  applied_personally?: boolean;
  created?: string;
  resolved?: string;
  reason?: string;
  /** Only present from `getPromotion`, not from the listing. */
  skill_md?: string;
  /** How often the owner has loaded it. Empty until the skill has been used. */
  usage?: { loads?: number; loads_completed?: number; last_loaded?: string };
}

export interface AutoSkillsConfig {
  mode: string;
  min_tool_calls: number;
  max_personal_skills: number;
  max_pending_promotions: number;
  [key: string]: unknown;
}

const PROMOTIONS = "plugins/_ea_auto_skills/auto_skills_promotions";
const CONFIG = "plugins/_ea_auto_skills/auto_skills_config";

export async function listPromotions(): Promise<Promotion[]> {
  const result = await apiRequest<{ ok: boolean; promotions?: Promotion[]; error?: string }>(
    PROMOTIONS,
    { body: { action: "list" } },
  );
  if (!result.ok) throw new Error(result.error || "Could not list skill promotions.");
  return result.promotions ?? [];
}

export async function getPromotion(promotionId: string): Promise<Promotion> {
  const result = await apiRequest<{ ok: boolean; promotion?: Promotion; error?: string }>(
    PROMOTIONS,
    { body: { action: "get", promotion_id: promotionId } },
  );
  if (!result.ok || !result.promotion) {
    throw new Error(result.error || "Could not load the proposal.");
  }
  return result.promotion;
}

/** Copies the skill into `usr/skills`, where every account's agent will read it. */
export async function promoteSkill(promotionId: string): Promise<string> {
  const result = await apiRequest<{ ok: boolean; message?: string; error?: string }>(
    PROMOTIONS,
    { body: { action: "promote", promotion_id: promotionId } },
  );
  if (!result.ok) throw new Error(result.error || "Could not promote the skill.");
  return result.message ?? "Promoted.";
}

/** Declines the proposal. The originating user keeps their own copy. */
export async function rejectSkill(promotionId: string, reason: string): Promise<string> {
  const result = await apiRequest<{ ok: boolean; message?: string; error?: string }>(
    PROMOTIONS,
    { body: { action: "reject", promotion_id: promotionId, reason } },
  );
  if (!result.ok) throw new Error(result.error || "Could not reject the proposal.");
  return result.message ?? "Rejected.";
}

/**
 * Removes a skill the agent wrote for the signed-in user.
 *
 * Unlike `deleteSkill`, this needs no `plugins.manage`: it reaches only the
 * caller's own induced skills, and the server takes the owner from the session
 * rather than from anything sent here. It also withdraws any proposal still
 * waiting for an administrator.
 */
export async function forgetSkill(name: string): Promise<string> {
  const result = await apiRequest<{ ok: boolean; message?: string; error?: string }>(
    "plugins/_ea_auto_skills/auto_skills_forget",
    { body: { action: "forget", name } },
  );
  if (!result.ok) throw new Error(result.error || "Could not remove the skill.");
  return result.message ?? "Removed.";
}

export async function getAutoSkillsConfig(): Promise<{
  enabled: boolean;
  editable: string[];
  config: AutoSkillsConfig;
}> {
  const result = await apiRequest<{
    ok: boolean;
    enabled?: boolean;
    editable?: string[];
    config?: AutoSkillsConfig;
    error?: string;
  }>(CONFIG, { body: { action: "get" } });
  if (!result.ok || !result.config) {
    throw new Error(result.error || "Could not load the Auto Skills settings.");
  }
  return {
    enabled: result.enabled ?? false,
    editable: result.editable ?? [],
    config: result.config,
  };
}

export async function saveAutoSkillsConfig(
  config: Partial<AutoSkillsConfig>,
): Promise<AutoSkillsConfig> {
  const result = await apiRequest<{ ok: boolean; config?: AutoSkillsConfig; error?: string }>(
    CONFIG,
    { body: { action: "save", config } },
  );
  if (!result.ok || !result.config) {
    throw new Error(result.error || "Could not save the Auto Skills settings.");
  }
  return result.config;
}
