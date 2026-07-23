// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  fydesign — Brand Style Learning                                              ║
// ║                                                                              ║
// ║  buildBrandStyleGuide(cfg) reads a brand's REAL design-system docs (Markdown, ║
// ║  txt, CSS tokens, etc.) from GCS, plus its colors / fonts / notes, and asks   ║
// ║  the configured model to distill a concise STYLE GUIDE that makes              ║
// ║  generated designs replicate the brand's ESTABLISHED aesthetic — not a         ║
// ║  generic "on-brand" look.                                                      ║
// ║                                                                              ║
// ║  Self-contained, RELATIVE imports only (runs under tsx, no @/ alias). Never   ║
// ║  throws: on any failure it returns a short fallback guide from colors/fonts.  ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { callAI } from './ai/deepseek-client';

const MAX_DOCS = 4;          // how many text docs to actually fetch from GCS
const MAX_DOC_TEXT = 6000;   // total chars of doc text fed to Opus
const PER_DOC_CAP = 3500;    // per-doc char cap (so one huge file can't crowd out others)

interface RawAsset { name?: string; url?: string; isImage?: boolean }
interface DocAsset { name: string; url: string }

// Binary / non-prose extensions that sometimes live inside a "Design System"
// folder and therefore match the name regex — we must NOT try to decode these.
const BINARY_EXT = /\.(ttf|otf|woff2?|eot|png|jpe?g|gif|webp|svg|ico|pdf|zip|mp4|mov|webm|ds_store)$/i;
// Genuinely text design-system docs worth reading as prose / tokens.
const TEXT_EXT = /\.(md|txt|css|scss|less|json|ya?ml|html?)$/i;
// Name signals a design-system / brand / style doc even without a clear extension.
const DOC_NAME = /\.md$|\.txt$|design[\s-]?system|brand|guideline|style/i;

function safeParseAssets(raw: unknown): RawAsset[] {
  let ua: unknown = raw;
  if (typeof ua === 'string') {
    try { ua = JSON.parse(ua); } catch { return []; }
  }
  return Array.isArray(ua) ? (ua as RawAsset[]) : [];
}

function safeParseAnalysis(raw: unknown): Record<string, unknown> {
  if (!raw) return {};
  if (typeof raw === 'string') {
    try { return JSON.parse(raw) as Record<string, unknown>; } catch { return {}; }
  }
  return typeof raw === 'object' ? (raw as Record<string, unknown>) : {};
}

// Pick the text design-system docs, best-first: README/SKILL/guideline-style
// Markdown & text before token CSS/JSON, longer-extension docs before HTML previews.
function pickDocs(assets: RawAsset[]): DocAsset[] {
  const docs = assets
    .filter((a) => a && a.url && a.name && !a.isImage)
    .map((a) => ({ name: String(a.name), url: String(a.url) }))
    .filter((a) => DOC_NAME.test(a.name) && !BINARY_EXT.test(a.name) && TEXT_EXT.test(a.name));

  const rank = (name: string): number => {
    const n = name.toLowerCase();
    if (/(readme|skill|guideline|brand|style|voice|tone)\.(md|txt)$/.test(n)) return 0;
    if (/\.(md|txt)$/.test(n)) return 1;                 // any other prose doc
    if (/colors?|type|token|theme|palette|font/.test(n) && /\.(css|scss|less|json|ya?ml)$/.test(n)) return 2;
    if (/\.(css|scss|less|json|ya?ml)$/.test(n)) return 3; // other token files
    return 4;                                             // html previews, etc. (last resort)
  };

  return docs.sort((a, b) => rank(a.name) - rank(b.name)).slice(0, MAX_DOCS);
}

// Resolve an /api/assets/<id>/file URL → GCS bytes → UTF-8 text. Never throws.
async function fetchDocText(url: string): Promise<string> {
  const m = url.match(/\/api\/assets\/([^/]+)\/file/);
  if (!m) return '';
  try {
    const { getDb } = await import('./db');
    const sql = getDb();
    const rows = await sql`SELECT metadata, mime_type FROM asset_registry WHERE id = ${m[1]} LIMIT 1`;
    const row = rows[0] as { metadata?: { gcsPath?: string } } | undefined;
    const gcsPath = row?.metadata?.gcsPath;
    if (!gcsPath) return '';
    const { readFromGCS } = await import('./gcs');
    const file = await readFromGCS(gcsPath);
    if (!file?.buffer) return '';
    return file.buffer.toString('utf8');
  } catch {
    return '';
  }
}

// Strip noisy boilerplate so the char budget holds real signal.
function tidy(text: string): string {
  return text
    .replace(/\r\n/g, '\n')
    .replace(/```[\s\S]*?```/g, ' ')   // drop fenced code blocks (token dumps add little prose)
    .replace(/<[^>]+>/g, ' ')          // strip HTML tags if an .html preview slipped in
    .replace(/[ \t]+/g, ' ')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

function colorsFrom(analysis: Record<string, unknown>): string[] {
  const theme = (analysis.theme as Record<string, unknown>) || {};
  const accents = Array.isArray(theme.accentColors) ? (theme.accentColors as unknown[]) : [];
  return [theme.primaryColor, ...accents].filter(Boolean).map(String);
}

// Minimal guide when there is nothing to synthesize from (or Opus is unreachable).
function fallbackGuide(name: string, colors: string[], fonts: string): string {
  const palette = colors.length ? colors.join(', ') : 'the established brand palette';
  const type = fonts || 'a clean modern sans-serif (Inter / system-ui)';
  return [
    `BRAND STYLE GUIDE — ${name}`,
    `Palette: use ${palette} as the core colors; keep them dominant and consistent across every surface.`,
    `Typography: ${type}; large confident headlines, generous line-height, tight heading tracking.`,
    `Look & feel: premium, modern, high-contrast, lots of breathing room; no clip-art, no rainbow gradients, no off-brand colors.`,
    `Do: stay disciplined to the palette and type; Don't: introduce stock-photo clichés or colors outside the set.`,
  ].join('\n');
}

/**
 * Build a concise, brand-specific STYLE GUIDE string (~<=400 words) that captures
 * the brand's REAL aesthetic: exact fonts, color-usage rules, visual motifs,
 * layout conventions, tone & voice, and clear do's/don'ts.
 *
 * Robust by design: missing assets are skipped, all GCS/DB/AI calls are guarded,
 * and it never throws — on total failure it returns a short fallback guide.
 */
export async function buildBrandStyleGuide(cfg: any): Promise<string> {
  const analysis = safeParseAnalysis(cfg?.analysis_json);
  const theme = (analysis.theme as Record<string, unknown>) || {};
  const name = String(cfg?.company_name || analysis.appName || 'Brand');
  const colors = colorsFrom(analysis);
  const brandColors = String(analysis.brand_colors || (analysis as Record<string, unknown>).brandColors || '');
  const fonts = String(analysis.brand_fonts || (analysis as Record<string, unknown>).brandFonts || '');
  const description = String(analysis.description || '');
  const notes = String(cfg?.brand_notes || '');

  // 1. Gather real design-system doc text from GCS (best-effort).
  let docText = '';
  try {
    const docs = pickDocs(safeParseAssets(cfg?.uploaded_assets));
    const parts: string[] = [];
    let used = 0;
    for (const doc of docs) {
      if (used >= MAX_DOC_TEXT) break;
      const raw = await fetchDocText(doc.url);
      const clean = tidy(raw);
      if (!clean) continue;
      const remaining = MAX_DOC_TEXT - used;
      const chunk = clean.slice(0, Math.min(PER_DOC_CAP, remaining));
      parts.push(`### ${doc.name}\n${chunk}`);
      used += chunk.length;
    }
    docText = parts.join('\n\n').slice(0, MAX_DOC_TEXT);
  } catch {
    docText = '';
  }

  // 2. Ask the configured model to synthesize the guide.
  const sys =
    'You are a meticulous brand designer. You distill a brand\'s REAL, established visual identity ' +
    'into a tight, actionable style guide that another designer (or an image model) can follow to ' +
    'reproduce the EXACT brand aesthetic — never a generic "on-brand" look. Be concrete and specific. ' +
    'Output ONLY the style guide as plain prose with short labeled lines or bullets. No preamble, no markdown headers, no code fences.';

  const ctxLines = [
    `BRAND: ${name}`,
    colors.length ? `THEME COLORS (hex): ${colors.join(', ')}` : '',
    brandColors ? `BRAND COLORS (notes): ${brandColors}` : '',
    fonts ? `FONTS: ${fonts}` : '',
    description ? `DESCRIPTION: ${description}` : '',
    notes ? `BRAND NOTES: ${notes}` : '',
    (theme.primaryColor) ? `PRIMARY: ${String(theme.primaryColor)}` : '',
  ].filter(Boolean).join('\n');

  const docBlock = docText
    ? `\n\nThe brand provided these REAL design-system documents — treat them as the SOURCE OF TRUTH and extract exact fonts, color tokens/usage rules, motifs and voice from them:\n"""\n${docText}\n"""`
    : `\n\n(No design-system documents were provided — synthesize the guide from the brand colors, fonts, notes and description above, and from what such a brand's polished identity would look like.)`;

  const paletteRule = (colors.length || brandColors)
    ? `\n\nPALETTE AUTHORITY: The THEME/BRAND COLORS above are the brand's CURRENT, CORRECT palette. If any provided document shows a different or older palette, IGNORE those colors and use the palette above. Introduce NO colors outside it — especially no greens/emerald and no pure black unless explicitly listed.`
    : '';
  const ask = `${ctxLines}${docBlock}${paletteRule}

Write a STYLE GUIDE (MAX ~400 words) that an art director and an AI image generator can follow to make every asset look unmistakably like ${name}. Cover, concisely:
- TYPOGRAPHY: exact typeface name(s) and how/where each weight is used (headlines vs body vs numerics).
- COLOR: the exact palette (hex) and precise usage rules — which color leads, which accents, backgrounds vs text, what NOT to use.
- VISUAL MOTIFS: signature textures, gradients, shapes, iconography and imagery/photography style.
- LAYOUT & COMPOSITION: spacing, radii, grid feel, how elements are arranged.
- TONE & VOICE: how copy reads.
- DO'S & DON'TS: a few sharp, brand-specific rules.

Be specific to THIS brand (cite its real fonts/colors/motifs). Output ONLY the guide.`;

  try {
    const guide = await callAI(ask, { system: sys, maxTokens: 1200 });
    const out = (guide || '').trim();
    if (out.length >= 40) return out;
  } catch {
    /* fall through to fallback */
  }

  return fallbackGuide(name, colors, fonts);
}
