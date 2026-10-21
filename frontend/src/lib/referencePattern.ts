/**
 * The CR or SR number's pattern as the interface reads it: a full, case-insensitive match, 40 characters at most.
 *
 * Display only. The approve endpoint, the gate and the executor each check the number again with their own copy of the
 * rule; this only decides when a field looks right. A pattern this engine cannot read matches nothing, so a control
 * that depends on it stays off rather than on.
 */

export const REFERENCE_MAX = 40;
export const PATTERN_MAX = 100;

export function matchesReference(pattern: string, value: string): boolean {
  const text = value.trim();
  if (!text || text.length > REFERENCE_MAX) return false;
  try {
    return new RegExp(`^(?:${pattern})$`, "i").test(text);
  } catch {
    return false;
  }
}

/** Why a pattern may not be saved, or null. The same three refusals the server makes (build contract, azure_change_config). */
export function patternProblem(pattern: string): string | null {
  if (!pattern.trim()) return "Enter a pattern.";
  if (pattern.length > PATTERN_MAX) return `A pattern may be at most ${PATTERN_MAX} characters.`;
  try {
    const re = new RegExp(`^(?:${pattern})$`, "i");
    if (re.test("")) return "That pattern matches an empty number, so every change would pass.";
  } catch {
    return "That is not a valid pattern.";
  }
  return null;
}
