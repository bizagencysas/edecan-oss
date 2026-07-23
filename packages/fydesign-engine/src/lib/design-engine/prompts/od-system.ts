// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  SYSTEM PROMPT — Minimal. Opus already knows everything from Clarify.     ║
// ║                                                                            ║
// ║  Philosophy: Opus 4.7 is the best designer in the world.                   ║
// ║  Give it brand tokens + user's enriched prompt + technical contract.       ║
// ║  Get out of the way.                                                       ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { buildFydesignContext, type CreativeMode } from './fydesign';
import { CLAUDE_DESIGN_ARTIFACT_CONTRACT } from './artifact-contract';

// ─────────────────────────────────────────────────────────────────────────────
// Visual Direction Library (fallback when no brand tokens exist)
// ─────────────────────────────────────────────────────────────────────────────

export interface DesignDirection {
  id: string;
  label: string;
  mood: string;
  references: string[];
  displayFont: string;
  bodyFont: string;
  monoFont?: string;
  palette: {
    bg: string;
    surface: string;
    fg: string;
    muted: string;
    border: string;
    accent: string;
  };
  posture: string[];
}

export const DESIGN_DIRECTIONS: DesignDirection[] = [
  {
    id: 'editorial-monocle',
    label: 'Editorial — Monocle / FT magazine',
    mood: 'Print-magazine feel. Generous whitespace, large serif headlines, restrained palette.',
    references: ['Monocle', 'The Financial Times Weekend', 'NYT Magazine'],
    displayFont: "'Playfair Display', Georgia, serif",
    bodyFont: "'Inter', system-ui, sans-serif",
    palette: {
      bg:      'oklch(98% 0.004 95)',
      surface: 'oklch(100% 0.002 95)',
      fg:      'oklch(20% 0.018 70)',
      muted:   'oklch(48% 0.012 70)',
      border:  'oklch(90% 0.006 95)',
      accent:  'oklch(52% 0.10 28)',
    },
    posture: ['serif display, sans body', 'no shadows, borders + whitespace', 'one decisive image'],
  },
  {
    id: 'modern-minimal',
    label: 'Modern minimal — Linear / Vercel',
    mood: 'Quiet, precise, software-native.',
    references: ['Linear', 'Vercel', 'Notion', 'Stripe'],
    displayFont: "'SF Pro Display', system-ui, sans-serif",
    bodyFont: "'Inter', system-ui, sans-serif",
    palette: {
      bg:      'oklch(99% 0.002 240)',
      surface: 'oklch(100% 0 0)',
      fg:      'oklch(18% 0.012 250)',
      muted:   'oklch(54% 0.012 250)',
      border:  'oklch(92% 0.005 250)',
      accent:  'oklch(58% 0.18 255)',
    },
    posture: ['tight letter-spacing', 'hairline borders', 'frosted glass nav'],
  },
  {
    id: 'human-approachable',
    label: 'Human / approachable — Airbnb / Duolingo',
    mood: 'Friendly and tactile. Clean background, product-led color, generous radii.',
    references: ['Airbnb', 'Duolingo', 'Mercury'],
    displayFont: "'DM Sans', system-ui, sans-serif",
    bodyFont: "'Inter', system-ui, sans-serif",
    palette: {
      bg:      'oklch(98% 0.004 240)',
      surface: 'oklch(100% 0 0)',
      fg:      'oklch(20% 0.02 240)',
      muted:   'oklch(50% 0.018 240)',
      border:  'oklch(90% 0.006 240)',
      accent:  'oklch(56% 0.12 170)',
    },
    posture: ['strong weight contrast', 'comfortable radii (12-18px)', 'subtle elevation on cards'],
  },
  {
    id: 'dark-premium',
    label: 'Dark premium — Apple / Tesla',
    mood: 'Deep dark canvases with rich contrast. Premium, luxury, fintech.',
    references: ['Apple Pro', 'Tesla', 'xAI'],
    displayFont: "'SF Pro Display', system-ui, sans-serif",
    bodyFont: "'Inter', system-ui, sans-serif",
    palette: {
      bg:      'oklch(13% 0.02 270)',
      surface: 'oklch(18% 0.015 270)',
      fg:      'oklch(95% 0.005 250)',
      muted:   'oklch(60% 0.01 250)',
      border:  'oklch(28% 0.015 270)',
      accent:  'oklch(72% 0.20 280)',
    },
    posture: ['dark backgrounds with luminous text', 'subtle gradients and glows', 'glass morphism for overlays'],
  },
];

export function pickDirection(prompt: string, brandTokens?: string): DesignDirection {
  const p = prompt.toLowerCase();
  if (/dark\s*mode|modo\s*oscuro|fondo\s*negro/i.test(p)) return DESIGN_DIRECTIONS.find(d => d.id === 'dark-premium')!;
  if (!(/light\s*mode|modo\s*claro/i.test(p)) && brandTokens && /dark|#1[0-4]/i.test(brandTokens)) return DESIGN_DIRECTIONS.find(d => d.id === 'dark-premium')!;
  if (/fintech|crypto|premium|luxury/i.test(p)) return DESIGN_DIRECTIONS.find(d => d.id === 'dark-premium')!;
  if (/editorial|magazine|blog/i.test(p)) return DESIGN_DIRECTIONS.find(d => d.id === 'editorial-monocle')!;
  if (/consumer|education|wellness|friendly/i.test(p)) return DESIGN_DIRECTIONS.find(d => d.id === 'human-approachable')!;
  return DESIGN_DIRECTIONS.find(d => d.id === 'modern-minimal')!;
}

export function directionToCSS(d: DesignDirection): string {
  return `:root {
  --bg: ${d.palette.bg};
  --surface: ${d.palette.surface};
  --fg: ${d.palette.fg};
  --muted: ${d.palette.muted};
  --border: ${d.palette.border};
  --accent: ${d.palette.accent};
  --font-display: ${d.displayFont};
  --font-body: ${d.bodyFont};
${d.monoFont ? `  --font-mono: ${d.monoFont};\n` : ''}}`;
}

// These are kept for backward compatibility but no longer bloat the system prompt
export const ANTI_SLOP_CHECKLIST = '';
export const FIVE_DIM_CRITIQUE = '';
export const SPECIALIST_PERSONAS = '';

// ─────────────────────────────────────────────────────────────────────────────
// THE SYSTEM PROMPT — Clean, minimal, lets Opus be Opus
// ─────────────────────────────────────────────────────────────────────────────

export function composeODSystemPrompt(
  width: number,
  height: number,
  brandTokens: string,
  mode: string,
  prompt: string,
  creativeMode: CreativeMode = 'balanced',
  brandName?: string,
  repoUrl?: string,
  brandFontFamilies: string[] = [],
): string {
  const direction = pickDirection(prompt, brandTokens);

  // Override direction fonts when brand fonts exist
  if (brandFontFamilies.length > 0) {
    const brandFont = `'${brandFontFamilies[0]}', sans-serif`;
    direction.displayFont = brandFont;
    direction.bodyFont = brandFont;
  }

  const cssTokens = directionToCSS(direction);

  // ── Brand identity block ──
  const brandBlock = brandTokens
    ? `## Brand Identity (use these over defaults)\n${brandTokens}\n`
    : '';

  const fontRule = brandFontFamilies.length > 0
    ? `Use font-family: '${brandFontFamilies[0]}', sans-serif for ALL text. Brand fonts are already loaded via @font-face. Do NOT import Google Fonts.`
    : `Import real Google Fonts via @import. Avoid Inter/Roboto as display font.`;

  return `You are the world's best designer. You are Opus 4.7 — the most advanced creative AI ever built.

${brandName ? `You are designing for **${brandName}**.` : ''}
${repoUrl ? `Their product repository: ${repoUrl}` : ''}

${brandBlock}

## Default Design Tokens (override with brand tokens when available)
\`\`\`css
${cssTokens}
\`\`\`

${CLAUDE_DESIGN_ARTIFACT_CONTRACT}

## Technical Contract
- Complete \`<!DOCTYPE html>\` document
- All CSS in \`<style>\` — ${fontRule}
- Canvas: exactly ${width}×${height}px
- Body: \`<body style="margin:0;padding:0;overflow:hidden;width:${width}px;height:${height}px;">\`
- Fill the ENTIRE canvas. No tiny element floating in empty space.
- All visuals: HTML/CSS/SVG/canvas. No external scripts, no remote libraries.
- Output ONLY raw HTML. No markdown fences. No explanations.
- No filler copy. No lorem ipsum. No "Feature One / Feature Two."
- Every word, number, and image must be specific to THIS brief.
- Use oklch(), gradients, backdrop-filter, clip-path, SVG — use your full CSS arsenal.
- One decisive visual move per design. Restraint > decoration.`;
}
