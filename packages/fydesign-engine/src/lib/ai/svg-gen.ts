// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  SVG generator — vector design through the configured text provider          ║
// ║                                                                            ║
// ║  Opus writes a complete, self-contained <svg> document on-brand (logos,    ║
// ║  icons, badges, simple infographics). No external refs, no <image>, no     ║
// ║  scripts. Output is sanitized to the first <svg…> … last </svg>.           ║
// ║                                                                            ║
// ║  RELATIVE import only (tsx does not resolve "@/").                          ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { callAI } from './deepseek-client';

const SYSTEM_PROMPT =
  'You are a master vector/logo designer. Output ONLY a single valid <svg>…</svg> document — no markdown, no explanation. Include width, height, and viewBox. No external refs, no <image>, no scripts.';

/**
 * Generate ONE complete, valid, self-contained SVG that is on-brand.
 *
 * @param brief  What to draw (e.g. "a minimal credit-card icon").
 * @param ctx    Brand context — name, palette (used in the artwork) and optional fonts.
 * @returns      A sanitized `<svg>…</svg>` string, or `null` if Opus produced no valid SVG.
 */
export async function generateBrandSVG(
  brief: string,
  ctx: { name: string; colors: string[]; fonts?: string },
): Promise<string | null> {
  const palette = (ctx.colors || []).filter(Boolean);
  const userPrompt = [
    `Brand: ${ctx.name}`,
    palette.length ? `Brand colors (use these): ${palette.join(', ')}` : '',
    ctx.fonts ? `Brand fonts: ${ctx.fonts}` : '',
    '',
    `Design brief: ${brief}`,
    '',
    'Requirements:',
    '- Produce a single, complete, self-contained SVG (logo / icon / badge / simple infographic).',
    '- Use the brand colors above. Clean, balanced, modern geometry; crisp paths; no clutter.',
    '- Must include width, height and a viewBox attribute.',
    '- No external references, no <image>, no <script>, no foreignObject, no remote fonts.',
    '- Any text must use a generic system font stack (e.g. sans-serif) so it renders anywhere.',
    '- Output ONLY the raw <svg>…</svg> markup. No markdown fences, no commentary.',
  ]
    .filter(Boolean)
    .join('\n');

  let raw: string;
  try {
    raw = await callAI(userPrompt, { system: SYSTEM_PROMPT, maxTokens: 4000 });
  } catch (e) {
    console.warn('[svg-gen] callAI failed:', e instanceof Error ? e.message : e);
    return null;
  }

  return sanitizeSvg(raw);
}

/** Strip markdown fences and isolate the first `<svg`…last `</svg>`; null if invalid. */
function sanitizeSvg(raw: string): string | null {
  if (!raw) return null;
  let s = raw.trim();

  // Drop a leading ```svg / ```xml / ``` fence and any trailing fence.
  const fence = s.match(/```(?:svg|xml|html)?\s*([\s\S]*?)```/i);
  if (fence) s = fence[1].trim();

  const start = s.indexOf('<svg');
  const endTag = s.lastIndexOf('</svg>');
  if (start === -1 || endTag === -1 || endTag < start) return null;

  const svg = s.slice(start, endTag + '</svg>'.length).trim();
  if (!svg.includes('<svg') || !svg.includes('</svg>')) return null;
  return svg;
}
