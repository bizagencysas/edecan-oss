// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Gemini Brand Fidelity Validator (A2)                                      ║
// ║  After each generation, verifies HTML respects the brand identity:          ║
// ║  correct colors, fonts, product names, plan names, and tone.                ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { callGeminiJSON, GEMINI_PRO } from './gemini-client';

export interface BrandValidationIssue {
  kind: 'wrong-color' | 'wrong-font' | 'invented-name' | 'invented-plan' | 'wrong-tone' | 'other';
  detail: string;
  severity: 'critical' | 'minor';
}

export interface BrandValidationResult {
  passed: boolean;
  score: number; // 0-10
  issues: BrandValidationIssue[];
}

/**
 * Ask Gemini 2.5 Pro to audit a generated HTML design for brand fidelity.
 * Returns null on API/parse failure. Callers should treat null as "skip validation".
 */
export async function validateBrandFidelity(input: {
  html: string;
  brand: {
    name?: string;
    blurb?: string;
    colors?: string[];
    fonts?: string[];
    productNames?: string[];
  };
  userPrompt: string;
}): Promise<BrandValidationResult | null> {
  if (!input.html || !input.brand.name) return null;

  const prompt = `You are fydesign's Brand Fidelity Auditor.

BRAND FACTS (authoritative):
- Name: ${input.brand.name}
- Blurb: ${input.brand.blurb?.slice(0, 500) || 'unknown'}
- Brand colors (hex): ${input.brand.colors?.join(', ') || 'any'}
- Brand fonts: ${input.brand.fonts?.join(', ') || 'any'}
- Real product names: ${input.brand.productNames?.join(', ') || 'none'}

USER REQUEST: "${input.userPrompt}"

HTML TO AUDIT (first 8000 chars):
\`\`\`html
${input.html.slice(0, 8000)}
\`\`\`

Look for these specific issues:
1. wrong-color: a non-brand color used as primary/accent when brand colors exist
2. wrong-font: a font that's not the brand font (e.g. Inter when brand is Plex)
3. invented-name: a product name that contradicts the real ones (e.g. "BUILDER" when brand has "Boost Plus")
4. invented-plan: pricing/tiers/features that don't match the brand facts
5. wrong-tone: copy tone wildly different from brand voice

Return ONLY valid JSON:
{ "passed": <true if score >= 8>, "score": <0-10>, "issues": [{"kind": "...", "detail": "concrete <100 char issue", "severity": "critical|minor"}] }

Be lenient on creative tone (don't flag a punchy headline just because brand blurb is technical). Be strict on factual contradictions (wrong plan name, invented price). Empty issues array if everything looks fine.`;

  try {
    console.log(`[Validator] Calling Gemini 2.5 Pro for brand fidelity check...`);
    const result = await callGeminiJSON<BrandValidationResult>(prompt, {
      model: GEMINI_PRO,
      temperature: 0.2,
      maxTokens: 2000,
    });

    if (!result || typeof result.score !== 'number') {
      console.warn('[Validator] Gemini returned invalid result');
      return null;
    }

    // Ensure passed is consistent with score
    result.passed = result.score >= 8;
    result.issues = Array.isArray(result.issues) ? result.issues : [];

    console.log(`[Validator] Brand fidelity score: ${result.score}/10 — ${result.passed ? 'PASSED' : 'FAILED'} (${result.issues.length} issue(s))`);
    return result;
  } catch (e) {
    console.warn('[Validator] Failed:', e instanceof Error ? e.message : e);
    return null;
  }
}
