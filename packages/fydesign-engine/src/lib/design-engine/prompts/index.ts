// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  PROMPT ROUTER — Detects task type → picks the right brain               ║
// ║  core.ts (~150w) + brain (~170w) = ~320 words total per call              ║
// ║  vs. the old monolithic prompt at 2300 words. 7x smaller.                ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { CORE_IDENTITY } from './core';
import { MOCKUP_BRAIN } from './mockup';
import { CAROUSEL_BRAIN } from './carousel';
import { AD_BRAIN } from './ad';
import { POST_BRAIN } from './post';
import { LANDING_BRAIN } from './landing';
import { EMAIL_BRAIN } from './email';
import { getProjectRules } from './brand-rules';
import { CLAUDE_DESIGN_ARTIFACT_CONTRACT } from './artifact-contract';

export type DesignMode = 'mockup' | 'carousel' | 'ad' | 'post' | 'landing' | 'email' | 'deck' | 'general';

/**
 * Detect the design mode from the user's prompt.
 * Returns the specialized brain name.
 */
export function detectMode(prompt: string): DesignMode {
  const p = prompt.toLowerCase();

  // Mockup / App Store
  if (/app\s*store|play\s*store|screenshot|mockup|phone\s*frame/i.test(p)) return 'mockup';

  // Carousel / Slides
  if (/carousel|carrusel|slide|slides|swipe/i.test(p)) return 'carousel';

  // Ad creative
  if (/\bad[s]?\b|anuncio|meta\s*ad|facebook\s*ad|instagram\s*ad|tiktok|ad\s*creative|performance/i.test(p)) return 'ad';

  // Social post
  if (/post|story|stories|instagram|facebook\s*post|social|reel/i.test(p)) return 'post';

  // Landing page
  if (/landing|landing\s*page|web|hero|sección|section|página/i.test(p)) return 'landing';

  // Email
  if (/email|correo|newsletter|drip|lifecycle|onboarding\s*email/i.test(p)) return 'email';

  // General fallback — uses mockup brain as default (most common use case)
  return 'general';
}

const BRAIN_MAP: Record<DesignMode, string> = {
  mockup: MOCKUP_BRAIN,
  carousel: CAROUSEL_BRAIN,
  ad: AD_BRAIN,
  post: POST_BRAIN,
  landing: LANDING_BRAIN,
  email: EMAIL_BRAIN,
  general: MOCKUP_BRAIN, // default to mockup — most common
  deck: MOCKUP_BRAIN, // deck reuses mockup brain with slide-count guidance
};

/**
 * Build the optimized system prompt.
 * CORE (~150w) + BRAIN (~170w) + BRAND RULES (~50w) + TECHNICAL (~100w) = ~470 words
 * That's ~700 tokens instead of ~4000 tokens. 5-6x faster.
 */
export function buildSystemPrompt(
  width: number,
  height: number,
  brandTokens: string,
  mode: DesignMode,
  brandName?: string,
): string {
  const brain = BRAIN_MAP[mode];
  const projectRules = getProjectRules(brandName);

  return `${CORE_IDENTITY}

${brain}

${projectRules ? `${projectRules}\n` : ''}
${brandTokens ? `BRAND CONTEXT:\n${brandTokens}\n` : ''}

${CLAUDE_DESIGN_ARTIFACT_CONTRACT}

TECHNICAL OUTPUT:
- Complete <!DOCTYPE html> document. All CSS in <style>. Import Google Fonts via @import.
- Body: <body style="margin:0;padding:0;overflow:hidden;width:${width}px;height:${height}px;">
    <div style="width:${width}px;height:${height}px;position:relative;overflow:hidden;">content</div>
  </body>
- Fill entire ${width}×${height}px canvas. No tiny elements in empty space.
- If LOGO URL or IMAGE URLs are in brand context, use <img src="THE_URL">.
- All visuals: pure HTML/CSS/SVG. No external images except brand URLs.
- Output ONLY HTML. No markdown, no explanations.`;
}

/**
 * Build the user message for HTML generation.
 */
export function buildUserMessage(
  designBrief: string,
  originalPrompt: string,
  label: string,
  width: number,
  height: number,
): string {
  return `DESIGN BRIEF:
${designBrief}

Original request: "${originalPrompt}"
Canvas: ${width}×${height}px
Label (DO NOT render as text): "${label}"

Produce the HTML now. Premium quality. Brand-consistent. Full canvas.`;
}

export { CORE_IDENTITY, getProjectRules };
