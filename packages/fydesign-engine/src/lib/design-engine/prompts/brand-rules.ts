// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  BRAND RULES — Project-specific design rules                              ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

// OSS starts without identities supplied by the maintainer or previous users.
// A person's rules arrive through their private brand registry at runtime.
export const BRAND_RULES: Record<string, string> = {};

/**
 * Get project-specific rules from brand name.
 * Tries to fuzzy-match the brand name to known projects.
 */
export function getProjectRules(brandName?: string): string {
  if (!brandName) return '';
  const lower = brandName.toLowerCase().replace(/\s+/g, '-');
  for (const [key, rules] of Object.entries(BRAND_RULES)) {
    if (lower.includes(key) || key.includes(lower)) {
      return rules;
    }
  }
  return '';
}
