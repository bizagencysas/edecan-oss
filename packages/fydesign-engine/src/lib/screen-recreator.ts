// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  fydesign — Screen Recreator                                               ║
// ║  Agent 1 (Gemini Flash): HTML/CSS structure with image placeholders        ║
// ║  Agent 2 (Vertex Imagen 4): Parallel AI image generation                   ║
// ║  Combiner: Injects real images into final HTML                             ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import type { ScreenInfo, ThemeInfo, LayoutType } from './types';
import { callDeepSeek as callGemini } from './ai/deepseek-client';
import { generateImagenImage, hasVertexCredentials } from './ai/imagen-client';

export interface RecreatedScreen {
  screenId: string;
  screenName: string;
  html: string;
  layout: LayoutType;
}

/* ─── Design Intelligence: Distilled from ui-ux-pro-max-skill ───────────── */

const DESIGN_INTELLIGENCE_PROMPT = `You are fydesign's Design Intelligence Engine — a world-class mobile UI designer who has studied thousands of award-winning apps. You create pixel-perfect HTML recreations that look INDISTINGUISHABLE from real app screenshots.

## YOUR DESIGN PHILOSOPHY
You approach every screen with love, creativity, and obsessive attention to detail. You don't just "render code" — you UNDERSTAND the intent behind every component and make it beautiful. You care deeply about the user experience and treat every pixel as sacred.

## TYPOGRAPHY MASTERY
- Font stack: -apple-system, 'SF Pro Display', 'SF Pro Text', 'Helvetica Neue', system-ui, sans-serif
- Hero headlines: 28-34px, font-weight 700-800, letter-spacing -0.5px, line-height 1.1-1.2
- Section headers: 20-22px, font-weight 600-700
- Body text: 15-17px, font-weight 400, line-height 1.5
- Captions/labels: 12-13px, font-weight 500, text-transform uppercase, letter-spacing 0.8px, opacity 0.5-0.6
- NEVER use the same font size for different hierarchy levels
- Use font-weight variation (400→800) to create visual rhythm

## SPACING SYSTEM (8pt grid)
- Micro spacing: 4px (between icon and label)
- Small: 8px (between related items)
- Medium: 12-16px (between components)
- Large: 24px (between sections)
- XL: 32-48px (between major sections)
- Screen padding: 20-24px horizontal, always consistent
- Card padding: 16-20px internal
- Button padding: 14-16px vertical, 24-32px horizontal
- NEVER use inconsistent spacing — maintain rhythm

## COLOR APPLICATION RULES
- Primary color: used for CTAs, active states, key accents — NEVER as large background areas
- Background: use subtle warm grays (#F8F8F8, #FAFAFA) or the brand's actual background — avoid pure white
- Text hierarchy: primary text at 87-100% opacity, secondary at 55-60%, disabled at 30-38%
- Shadows: use rgba(0,0,0,0.06-0.12) for subtle elevation, never harsh black shadows
- Borders: 1px solid rgba(0,0,0,0.06-0.1) for subtle separation
- Gradients: use subtle gradients (2-3 stops) for premium feel on hero areas and CTAs

## COMPONENT DESIGN RULES
- Cards: border-radius 12-16px, subtle shadow (0 2px 12px rgba(0,0,0,0.08)), 1px border for crispness
- Buttons: height 48-56px, border-radius 12-14px, font-weight 600, centered text
- Input fields: height 48-52px, border-radius 10-12px, border 1.5px, focus state with primary color
- Tab bars: height 49px, 5 items max, icon 24px + label 10px, active state uses primary color
- Navigation bars: height 44px + safe area, font-weight 600 for title, subtle bottom border
- List items: height 60-72px, left icon/avatar 40-44px, chevron right indicator
- Avatars: border-radius 50%, sizes 32/40/48/64px
- Badges: border-radius 999px, min-width 20px, font-size 11-12px, font-weight 700

## VISUAL DEPTH & POLISH
- Status bar: time "9:41" left-aligned, signal/wifi/battery right-aligned — ALWAYS present
- Home indicator: thin bar (134x5px, border-radius 3px) centered at bottom, opacity 0.15-0.25
- Subtle background textures: use very faint gradients or noise-like patterns for depth
- Icon style: use simple inline SVG icons or text labels. Do not use emoji as UI icons.
- For hero images and illustrations: use {{IMG:description of what should appear}} placeholder
- Micro-interactions implied: add hover-state-like styling (subtle highlights) on active items
- Use backdrop-filter: blur() sparingly for frosted glass effects on overlays

## IMAGE PLACEHOLDER FORMAT
When the design needs a PHOTOGRAPHIC or DECORATIVE visual asset (hero photo, ambient
illustration, product photo, avatar, background texture), insert this exact placeholder:
{{IMG:detailed description of the VISUAL needed}}
The image pipeline replaces these with AI-generated visuals that contain NO text.

CRITICAL — the AI image model cannot render readable text/numbers (it produces garbage
like "HEGADLE MOPFLARD"). So:
- {{IMG:}} descriptions must describe ONLY a visual scene — people, products, lighting,
  mood, environment, textures. NEVER ask for words, numbers, labels, gauges-with-values,
  charts-with-data, dashboards, or any UI/typography inside the image.
- Anything with real data (a score like 720, a price, a chart, a stat, a button label,
  an app UI) must be built with HTML/CSS/SVG by YOU — not requested inside {{IMG:}}.
- Good:  {{IMG:Smiling young professional woman, soft window light, circular crop, photoreal}}
- Good:  {{IMG:Abstract soft gradient blur in brand colors, premium, no objects}}
- BAD:   {{IMG:credit dashboard showing 720 score}}  ← build the gauge + "720" in CSS/SVG
- BAD:   {{IMG:pricing card that says $9.99}}        ← build the card + price in CSS

## LAYOUT INTELLIGENCE
For "list" layouts: vertical scroll feel, card-based items, search bar at top
For "grid" layouts: 2-3 column grids, uniform card sizes, clean gutters
For "detail" layouts: hero image/header at top, content below, sticky CTA at bottom
For "form" layouts: grouped input sections, labels above fields, primary CTA fixed bottom
For "profile" layouts: centered avatar, stats row, content sections below
For "chat" layouts: message bubbles (right=user, left=other), input bar at bottom
For "dashboard" layouts: KPI cards row, charts below, clean data visualization
For "onboarding" layouts: large illustration, headline, subtitle, page dots, CTA button
For "settings" layouts: grouped sections with headers, toggle switches, disclosure arrows

## ANTI-PATTERNS TO AVOID
- NEVER use lorem ipsum — always use realistic text from the detected UI elements
- NEVER use plain gray (#808080) for anything — use warm or cool neutrals
- NEVER use borders thicker than 2px (except for focus states)
- NEVER center-align body text — left-align for readability
- NEVER use more than 3 font sizes on one screen
- NEVER forget the status bar and home indicator
- NEVER use shadows without also having subtle borders
- NEVER make text smaller than 11px`;

/* ─── Agent 2: Image Generation (Vertex Imagen 4) ───────────────────────── */

async function generateImage(description: string): Promise<string> {
  try {
    // The no-text policy is enforced inside generateImagenImage (NO_TEXT_IMAGE_DIRECTIVE
    // + negative prompt). We only describe the VISUAL here — any labels/numbers belong
    // in the surrounding HTML/CSS, never baked into the generated pixels.
    const result = await generateImagenImage(
      `High-quality decorative/photographic asset for a mobile app screen: ${description}. Style: clean, modern, professional, app-store quality.`,
      { aspectRatio: '1:1' },
    );
    return result.dataUrl;
  } catch (e) {
    console.warn('Image generation error:', e instanceof Error ? e.message : e);
    return '';
  }
}

/**
 * Extract {{IMG:description}} placeholders from HTML
 */
function extractImagePlaceholders(html: string): string[] {
  const matches = html.match(/\{\{IMG:([^}]+)\}\}/g) || [];
  return matches.map(m => m.replace(/\{\{IMG:/, '').replace(/\}\}$/, ''));
}

/**
 * Replace placeholders with real image URLs or gradient fallbacks
 */
function injectImages(html: string, imageMap: Map<string, string>, brandColors: string[]): string {
  return html.replace(/\{\{IMG:([^}]+)\}\}/g, (match, desc) => {
    const url = imageMap.get(desc);
    if (url) {
      return `<img src="${url}" alt="${desc}" style="width:100%;height:100%;object-fit:cover;border-radius:inherit;" />`;
    }
    // Fallback: brand-colored gradient placeholder
    const c1 = brandColors[0] || '#6366F1';
    const c2 = brandColors[1] || brandColors[0] || '#8B5CF6';
    return `<div style="width:100%;height:100%;background:linear-gradient(135deg,${c1}22,${c2}33);border-radius:inherit;display:flex;align-items:center;justify-content:center;font-size:24px;opacity:0.6;">🖼</div>`;
  });
}

/* ─── Agent 1: HTML Structure Generation ────────────────────────────────── */

async function recreateScreen(
  screen: ScreenInfo,
  theme: ThemeInfo,
  appName: string,
): Promise<{ html: string; imagePlaceholders: string[] }> {
  const codeSnippet = screen.rawCode
    ? screen.rawCode.slice(0, 6000)
    : `// No raw code available\n// Screen: ${screen.screenName}\n// Layout: ${screen.estimatedLayout}\n// Texts: ${screen.texts.slice(0, 8).join(', ')}\n// Components: ${screen.components.slice(0, 10).join(', ')}\n// Icons: ${screen.icons.slice(0, 6).join(', ')}`;

  const prompt = `Recreate this mobile app screen as a SINGLE static HTML snippet.

APP: ${appName}
SCREEN: "${screen.screenName}" — ${screen.estimatedLayout} layout
BRAND COLORS:
  Primary: ${theme.primaryColor}
  Background: ${theme.backgroundColor}
  Dark BG: ${theme.darkBackgroundColor}
  Text: ${theme.textColor}
  Accents: ${theme.accentColors.join(', ')}
  Border radius: ${theme.borderRadius}px
  Brand: ${theme.brandName}

SOURCE CODE:
\`\`\`
${codeSnippet}
\`\`\`

DETECTED UI ELEMENTS:
  Texts: ${screen.texts.slice(0, 12).join(' | ')}
  Components: ${screen.components.slice(0, 15).join(', ')}
  Icons: ${screen.icons.slice(0, 8).join(', ')}

OUTPUT REQUIREMENTS:
1. Output a SINGLE <div> sized exactly 390×844px (iPhone 16 Pro viewport), overflow:hidden
2. Use ONLY inline styles — no <style> tags, no class names, no external CSS
3. Include iOS status bar (9:41, signal, wifi, battery) and home indicator bar
4. Use the brand colors intelligently — primary for accents/CTAs, not as large backgrounds
5. Use real text from detected elements — NEVER lorem ipsum
6. For PHOTOS/illustrations/textures, use {{IMG:visual-only description}} placeholders (NO text/numbers/UI inside — the image model garbles text). Build any gauge/score/number/chart/label yourself in CSS/SVG. Example: {{IMG:soft abstract gradient backdrop in brand colors, premium, no text}}
7. For icons, use Unicode/emoji that match semantically
8. This must look like a REAL screenshot from the App Store — premium, polished, alive
9. Apply proper visual hierarchy: size, weight, color, and spacing create clear reading order
10. No <html>, <head>, <body> tags — just the inner <div> content
11. Include 1-3 {{IMG:}} placeholders where visual assets would make the screen feel real`;

  try {
    const raw = await callGemini(prompt, {
      // model handled by deepseek-client
      system: DESIGN_INTELLIGENCE_PROMPT,
      temperature: 0.4,
      maxTokens: 8000,
    });
    const html = raw.replace(/^```html?\n?/i, '').replace(/\n?```$/i, '').trim();
    const imagePlaceholders = extractImagePlaceholders(html);
    return { html, imagePlaceholders };
  } catch (e) {
    console.error(`Screen recreation failed for ${screen.screenName}:`, e);
    return { html: generateFallbackScreen(screen, theme, appName), imagePlaceholders: [] };
  }
}

/**
 * Generate a styled fallback screen when AI recreation fails
 */
function generateFallbackScreen(screen: ScreenInfo, theme: ThemeInfo, appName: string): string {
  const bg = theme.backgroundColor || '#FAFAFA';
  const primary = theme.primaryColor || '#007AFF';
  const text = theme.textColor || '#1C1C1E';

  return `<div style="width:390px;height:844px;overflow:hidden;background:${bg};font-family:-apple-system,'SF Pro Display','SF Pro Text',system-ui,sans-serif;color:${text};position:relative;">
  <div style="display:flex;justify-content:space-between;padding:14px 24px 8px;font-size:15px;font-weight:600;">
    <span>9:41</span>
    <span style="display:flex;gap:4px;font-size:13px;">●●● ▲ 🔋</span>
  </div>
  <div style="padding:16px 24px;">
    <div style="font-size:28px;font-weight:700;letter-spacing:-0.5px;margin-bottom:8px;">${screen.screenName}</div>
    <div style="font-size:15px;color:${text}88;margin-bottom:24px;">${screen.texts[0] || appName}</div>
    ${screen.texts.slice(1, 6).map(t => `<div style="padding:16px;background:${primary}0A;border:1px solid ${primary}15;border-radius:${theme.borderRadius}px;margin-bottom:10px;font-size:14px;">${t}</div>`).join('')}
    ${screen.components.slice(0, 3).map(c => `<div style="padding:14px 24px;background:${primary};color:white;border-radius:${theme.borderRadius}px;text-align:center;font-weight:600;font-size:15px;margin-bottom:10px;box-shadow:0 2px 8px ${primary}33;">${c}</div>`).join('')}
  </div>
  <div style="position:absolute;bottom:8px;left:50%;transform:translateX(-50%);width:134px;height:5px;background:${text};border-radius:3px;opacity:0.18;"></div>
</div>`;
}

/**
 * DUAL-AGENT PIPELINE: Recreate ALL screens with parallel image generation
 *
 * Phase 1: Agent 1 (Gemini Flash) generates HTML for all screens (batched)
 * Phase 2: Agent 2 (Vertex Imagen 4) generates all images in parallel
 * Phase 3: Combiner injects images into HTML
 */
export async function recreateAllScreens(
  screens: ScreenInfo[],
  theme: ThemeInfo,
  appName: string,
): Promise<RecreatedScreen[]> {
  console.log(`[DualAgent] Starting pipeline for ${screens.length} screens...`);

  const phase1Results: { screenId: string; screenName: string; html: string; layout: LayoutType; placeholders: string[] }[] = [];
  const batchSize = 3;

  for (let i = 0; i < screens.length; i += batchSize) {
    const batch = screens.slice(i, i + batchSize);
    const batchResults = await Promise.all(
      batch.map(async (screen) => {
        const { html, imagePlaceholders } = await recreateScreen(screen, theme, appName);
        return {
          screenId: screen.id,
          screenName: screen.screenName,
          html,
          layout: screen.estimatedLayout,
          placeholders: imagePlaceholders,
        };
      }),
    );
    phase1Results.push(...batchResults);
  }

  const allDescriptions = new Set<string>();
  phase1Results.forEach(r => r.placeholders.forEach(d => allDescriptions.add(d)));

  const imageMap = new Map<string, string>();

  if (allDescriptions.size > 0 && hasVertexCredentials()) {
    const imgModel = process.env.GOOGLE_PREMIUM_IMAGE_MODEL || 'gemini-3-pro-image-preview';
    console.log(`[DualAgent] Generating ${allDescriptions.size} images via ${imgModel}...`);
    const imagePromises = Array.from(allDescriptions).map(async (desc) => {
      const url = await generateImage(desc);
      if (url) imageMap.set(desc, url);
    });
    await Promise.all(imagePromises);
    console.log(`[DualAgent] Generated ${imageMap.size}/${allDescriptions.size} images`);
  } else if (allDescriptions.size > 0) {
    console.log(`[DualAgent] ${allDescriptions.size} placeholders, but Vertex credentials missing — using gradient fallbacks`);
  }

  // ═══ PHASE 3: Combine HTML + Images ═══
  const brandColors = [theme.primaryColor, ...theme.accentColors];
  const results: RecreatedScreen[] = phase1Results.map(r => ({
    screenId: r.screenId,
    screenName: r.screenName,
    html: injectImages(r.html, imageMap, brandColors),
    layout: r.layout,
  }));

  console.log(`[DualAgent] Pipeline complete: ${results.length} screens with ${imageMap.size} generated images`);
  return results;
}
