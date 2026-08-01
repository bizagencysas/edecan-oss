// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  fydesign Visual Critic Loop                                               ║
// ║  "Opus thinks. DeepSeek builds." — Opus critiques, DeepSeek refines.       ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { callAI, callAIJSON } from './deepseek-client';
import { callDeepSeekBuilder } from './deepseek-builder';
import { extractCode, validateHTML } from '@/lib/design-engine/code-extractor';
import { CLAUDE_DESIGN_ARTIFACT_CONTRACT } from '@/lib/design-engine/prompts/artifact-contract';

export interface VisualCritique {
  score: number;
  worth_fixing: boolean;
  issues: string[];
  improvements: string[];
}

/**
 * DeepSeek critiques the HTML code directly (no screenshot needed).
 * Evaluates brand fidelity, composition, typography, and polish.
 */
export async function critiqueDesign(
  html: string,
  brief: string,
  width: number,
  height: number,
  originalPrompt?: string,
  brandTokens?: string,
): Promise<VisualCritique | null> {
  const brandContext = brandTokens
    ? `\nBRAND IDENTITY (the design MUST reflect this):\n${brandTokens}\n`
    : '';
  const userContext = originalPrompt
    ? `\nORIGINAL USER REQUEST: "${originalPrompt}"\n`
    : '';

  const prompt = `You are a senior visual design critic at a top agency. Review this ${width}×${height}px HTML design.

${userContext}
DESIGN BRIEF:
${brief}
${brandContext}

HTML CODE TO REVIEW:
\`\`\`html
${html.slice(0, 15000)}
\`\`\`

Score 0-10 and identify CONCRETE, ACTIONABLE problems.

CRITICAL EVALUATION CRITERIA:
1. BRAND FIDELITY: Are the brand's colors used? Does it feel like the brand?
2. COMPOSITION: Is the layout balanced? Good whitespace?
3. LAYOUT: Text overlap with mockups? Elements properly aligned?
4. PHONE MOCKUP: If present — realistic frame, Dynamic Island, status bar, real UI inside?
5. TYPOGRAPHY: Clear hierarchy? Headlines massive and bold? Good contrast?
6. PROFESSIONAL QUALITY: Would this pass as fydesign-level output: premium, specific, memorable, and shippable?
7. CANVAS: Does content fill the full ${width}×${height}px?
8. CREATIVE FORCE: Does it have a real point of view, or is it just a polished template?
9. ARTIFACT BEHAVIOR: If it claims to be a prototype, chatbot, voice UI, video player, 3D/shader, chart, or interactive tool, does it actually work as HTML/CSS/JS?
10. CLAUDE DESIGN FIT: Does it feel like an editable Claude Design canvas artifact rather than a static image or generic screenshot?

${CLAUDE_DESIGN_ARTIFACT_CONTRACT}

Score rubric:
- 10: top-tier agency design, perfect brand
- 7-9: solid, ship-ready, minor polish needed
- 4-6: amateurish — significant issues
- 0-3: broken or ignores the brand

Return ONLY valid JSON:
{
  "score": <0-10>,
  "worth_fixing": <true if score < 8>,
  "issues": ["concrete issue 1", "..."],
  "improvements": ["specific CSS/HTML change 1", "..."]
}`;

  try {
    // Opus 4.7 critiques — it's the taste/critique layer
    const result = await callAIJSON<VisualCritique>(prompt, {
      model: 'claude-opus-4-7',
      temperature: 0.2,
      maxTokens: 4000,
    });
    return result;
  } catch (e) {
    console.warn('[VisualCritic] Opus critique failed:', e instanceof Error ? e.message : e);
    // Fallback to DeepSeek if Opus unavailable
    try {
      return await callAIJSON<VisualCritique>(prompt, { temperature: 0.2, maxTokens: 4000 });
    } catch {
      return null;
    }
  }
}

/**
 * Refine HTML using a critique.
 */
export async function refineDesign(
  currentHTML: string,
  critique: VisualCritique,
  brief: string,
  width: number,
  height: number,
  systemPrompt: string,
): Promise<string> {
  const refinePrompt = `The design scored ${critique.score}/10. A senior designer flagged:

ISSUES:
${critique.issues.map(i => `- ${i}`).join('\n')}

REQUIRED IMPROVEMENTS:
${critique.improvements.map(i => `- ${i}`).join('\n')}

ORIGINAL BRIEF:
${brief}

CURRENT HTML:
\`\`\`html
${currentHTML.slice(0, 15000)}
\`\`\`

Apply ALL improvements. Keep brand colors and composition. Canvas: ${width}×${height}px.
Return ONLY the complete revised HTML — no markdown fences, no explanation.`;

  // Builder model refines — follows Opus critique instructions
  const raw = await callDeepSeekBuilder(refinePrompt, {
    system: systemPrompt,
    temperature: 0.4,
    maxTokens: 16000,
  });
  const refined = extractCode(raw);
  if (refined.length < currentHTML.length * 0.5) {
    console.warn('[VisualCritic] Refined HTML too short — keeping original');
    return currentHTML;
  }
  return refined;
}

/**
 * Full critique → refine loop for HQ mode.
 */
export async function visuallyImprove(
  html: string,
  brief: string,
  width: number,
  height: number,
  systemPrompt: string,
  maxIterations = 2,
  originalPrompt?: string,
  brandTokens?: string,
): Promise<{ html: string; finalScore: number | null; iterations: number }> {
  let current = html;
  let iterations = 0;
  let lastScore: number | null = null;

  for (let i = 0; i < maxIterations; i++) {
    iterations++;

    const critique = await critiqueDesign(current, brief, width, height, originalPrompt, brandTokens);
    if (!critique) break;
    lastScore = critique.score;
    console.log(`[VisualCritic] Iteration ${iterations} score: ${critique.score}/10 (worth_fixing=${critique.worth_fixing})`);
    console.log(`[VisualCritic] Issues: ${critique.issues.join(' | ')}`);

    if (!critique.worth_fixing || critique.score >= 9) break;
    if (critique.improvements.length === 0) break;

    try {
      current = await refineDesign(current, critique, brief, width, height, systemPrompt);
      const issues = validateHTML(current);
      if (issues.length > 0) {
        console.warn('[VisualCritic] Refined HTML has issues, stopping:', issues.join(', '));
        break;
      }
    } catch (e) {
      console.warn('[VisualCritic] Refine failed:', e instanceof Error ? e.message : e);
      break;
    }
  }

  return { html: current, finalScore: lastScore, iterations };
}

// Re-export the vision-based critic for easy access from this module.
// The vision version (visual-critic-vision.ts) uses Gemini Pro Vision with
// real screenshots for pixel-accurate critique in production.
export { visuallyImproveVision } from './visual-critic-vision';
