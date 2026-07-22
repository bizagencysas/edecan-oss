// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  AUTO-BRAND — "paste a link, I derive the whole brand"                          ║
// ║                                                                              ║
// ║  Zero config: give a website URL and Opus derives an EPHEMERAL brand identity   ║
// ║  (name, palette, logo, fonts, voice, REAL product facts) — no Neon row, no       ║
// ║  brand pre-registration. It fetches the HTML (title/og/theme-color/            ║
// ║  favicon/colors), screenshots the homepage with the shared headless browser,     ║
// ║  and shows BOTH to Opus vision to read the brand the way a human would. Strict   ║
// ║  anti-invention: only what is literally on the page — never fabricated prices,   ║
// ║  stats, claims or testimonials. The result plugs straight into the existing      ║
// ║  generators as a VideoBrandCtx.                                                 ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { callAIJSON, type InlineImage } from './ai/deepseek-client';
import { loadRefInline } from './ai/brand-image';
import type { VideoBrandCtx } from './video/types';

const LOG = '[auto-brand]';

export interface AutoBrand {
  name: string;
  colors: string[];       // hex, primary first
  fonts: string;          // "Display + Body" or comma list
  voice: string;          // brand tone of voice
  description: string;    // REAL facts only (anti-invention)
  valueProps: string[];   // REAL selling points pulled from the page
  industry: string;
  logoUrl: string;        // best logo/og-image we found (absolute URL)
  ogImage: string;
  sourceUrl: string;
  screenshot: boolean;    // whether Opus actually saw a render of the site
}

const ANTI_INVENTION = `You are a brand strategist reading a company's website to extract its REAL identity.
HARD RULE — anti-invention: use ONLY what is literally present on the page text and the screenshot.
NEVER invent or guess prices, statistics, percentages, follower/customer counts, awards, testimonials or claims.
If a fact is not on the page, leave it out. Colors must be hex values you can actually justify from the design.
Return STRICT JSON only.`;

function abs(href: string | undefined, base: string): string {
  if (!href) return '';
  try { return new URL(href, base).toString(); } catch { return ''; }
}

const HEX = /#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b/g;

/** A strict, full hex check — used for EVERY color before it can reach the palette. */
function validHex(c: unknown): c is string {
  return typeof c === 'string' && /^#(?:[0-9a-f]{3}|[0-9a-f]{6})$/i.test(c.trim());
}

function normHex(h: string): string {
  let v = h.trim().toLowerCase();
  if (v.length === 4) v = '#' + v[1] + v[1] + v[2] + v[2] + v[3] + v[3];
  return v;
}

/** True for layout/chrome grays (low saturation AND extreme lightness) — never a brand color. */
function isNeutral(hex: string): boolean {
  const n = parseInt(hex.slice(1), 16);
  const r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b), avg = (r + g + b) / 3;
  return (max - min) <= 18 && (avg <= 32 || avg >= 224);
}

/** Pull the most-used BRAND hex colors from raw HTML/CSS (frequency-ranked, neutrals dropped). */
function harvestColors(html: string): string[] {
  const counts = new Map<string, number>();
  for (const m of html.match(HEX) || []) {
    const v = normHex(m);
    // skip pure black/white AND near-neutral chrome grays so we surface the BRAND colors
    if (isNeutral(v)) continue;
    counts.set(v, (counts.get(v) || 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6).map(([c]) => c);
}

function metaContent(html: string, names: string[]): string {
  for (const n of names) {
    const re = new RegExp(`<meta[^>]+(?:name|property)=["']${n}["'][^>]*content=["']([^"']+)["']`, 'i');
    const m = html.match(re) || html.match(new RegExp(`<meta[^>]+content=["']([^"']+)["'][^>]*(?:name|property)=["']${n}["']`, 'i'));
    if (m) return m[1].trim();
  }
  return '';
}

function linkHref(html: string, rels: string[]): string {
  for (const r of rels) {
    const m = html.match(new RegExp(`<link[^>]+rel=["'][^"']*${r}[^"']*["'][^>]*href=["']([^"']+)["']`, 'i'))
      || html.match(new RegExp(`<link[^>]+href=["']([^"']+)["'][^>]*rel=["'][^"']*${r}[^"']*["']`, 'i'));
    if (m) return m[1].trim();
  }
  return '';
}

function visibleText(html: string): string {
  return html
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&[a-z]+;/gi, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 4000);
}

function normalizeUrl(u: string): string {
  const t = u.trim();
  return /^https?:\/\//i.test(t) ? t : `https://${t}`;
}

/**
 * Best-effort screenshot of the HTML that already crossed the guarded fetch.
 * It never navigates Chromium to a remote origin and blocks every subresource,
 * closing the separate browser/DNS-rebinding path around the Node fetch guard.
 */
async function screenshotSite(html: string): Promise<InlineImage | null> {
  if (!html.trim()) return null;
  let browser: import('playwright-core').Browser | undefined;
  try {
    const { launchBrowser } = await import('./browser');
    browser = await launchBrowser();
    const context = await browser.newContext({ javaScriptEnabled: false });
    const page = await context.newPage();
    await page.setViewportSize({ width: 1280, height: 900 });
    // The fetched document may reference arbitrary subresources. The text,
    // inline CSS and data URLs remain available without another network hop.
    await page.route('**/*', (route) => {
      const requestUrl = route.request().url();
      if (/^(?:data|blob|about):/i.test(requestUrl)) {
        route.continue().catch(() => {});
      } else {
        route.abort().catch(() => {});
      }
    });
    await page.setContent(html, { waitUntil: 'domcontentloaded', timeout: 30_000 });
    await page.waitForTimeout(250);
    const buf = await page.screenshot({ type: 'png' });
    return { mimeType: 'image/png', data: Buffer.from(buf).toString('base64') };
  } catch (e) {
    console.error(`${LOG} no se pudo capturar el sitio (se sigue sin screenshot):`, e instanceof Error ? e.message : e);
    return null;
  } finally {
    try { await browser?.close(); } catch { /* ignore */ }
  }
}

/**
 * Derive an ephemeral brand identity from a website URL. Fetches the page, screenshots it,
 * and has Opus (vision) read the brand — strictly from what's actually there.
 */
export async function deriveBrandFromUrl(
  inputUrl: string,
  opts: { brandName?: string } = {},
): Promise<AutoBrand> {
  const url = normalizeUrl(inputUrl);
  const host = (() => { try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return url; } })();

  let html = '';
  try {
    const r = await fetch(url, {
      headers: { 'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36' },
      signal: AbortSignal.timeout(30_000),
      redirect: 'follow',
    });
    if (r.ok) html = await r.text();
    else console.error(`${LOG} fetch HTML devolvió ${r.status} (se sigue con screenshot/vision)`);
  } catch (e) {
    console.error(`${LOG} fetch HTML falló (se sigue con screenshot/vision):`, e instanceof Error ? e.message : e);
  }

  const title = (html.match(/<title[^>]*>([^<]+)<\/title>/i)?.[1] || '').trim();
  const ogSite = metaContent(html, ['og:site_name']);
  const ogTitle = metaContent(html, ['og:title']);
  const metaDesc = metaContent(html, ['description', 'og:description']);
  const themeColor = metaContent(html, ['theme-color']);
  const ogImage = abs(metaContent(html, ['og:image', 'twitter:image']), url);
  const favicon = abs(linkHref(html, ['apple-touch-icon', 'icon', 'shortcut icon']), url);
  const harvested = harvestColors(html);
  // theme-color must pass the SAME strict hex check as every other color (a value like
  // "#fff radial-gradient(...)" or "#default" would otherwise become the primary color).
  const themeHex = validHex(themeColor) && !isNeutral(normHex(themeColor)) ? [normHex(themeColor)] : [];
  const regexColors = [...themeHex, ...harvested].filter(validHex);
  const text = visibleText(html);

  // Show Opus the screenshot + the best logo image we found.
  const screenshot = await screenshotSite(html);
  const logoSrc = ogImage || favicon;
  const logoInline = logoSrc ? await loadRefInline(logoSrc).catch(() => null) : null;
  const images: InlineImage[] = [];
  if (screenshot) images.push(screenshot);
  if (logoInline) images.push({ mimeType: logoInline.mimeType, data: logoInline.data });

  const ask = `Website: ${url}
<title>: ${title || '(none)'}
og:site_name: ${ogSite || '(none)'}
og:title: ${ogTitle || '(none)'}
meta description: ${metaDesc || '(none)'}
theme-color: ${themeColor || '(none)'}
hex colors harvested from the CSS (frequency-ranked, may include noise): ${regexColors.join(', ') || '(none)'}
${images.length ? `\nYou are also given ${images.length} image(s): ${screenshot ? 'a screenshot of the homepage' : ''}${screenshot && logoInline ? ' and ' : ''}${logoInline ? 'the logo/og-image' : ''}.` : ''}

Visible page text (truncated):
"""
${text || '(no text could be fetched — rely on the screenshot)'}
"""

Read this brand and return JSON (anti-invention — only what's truly here):
{
  "name": "the brand/company name (clean, no tagline)",
  "colors": ["#hex primary", "#hex secondary", "#hex accent"],
  "fonts": "the typographic feel, e.g. 'Geometric sans display + humanist body'",
  "voice": "the brand's tone of voice in one phrase",
  "description": "2-4 sentences of REAL facts about what they offer (no invented numbers/claims)",
  "valueProps": ["real selling point 1", "real selling point 2", "..."],
  "industry": "the category/industry"
}`;

  type Extract = Partial<Pick<AutoBrand, 'name' | 'colors' | 'fonts' | 'voice' | 'description' | 'valueProps' | 'industry'>>;
  let res: Extract | null = null;
  try {
    res = await callAIJSON<Extract>(ask, { system: ANTI_INVENTION, images: images.length ? images : undefined, maxTokens: 1500, json: true });
  } catch (e) {
    console.error(`${LOG} extracción con visión lanzó error:`, e instanceof Error ? e.message : e);
    res = null;
  }
  // The vision path needs an image-capable provider. For text-only providers, callAIJSON
  // RETURNS null (it doesn't throw) — so detect a null result and retry text-only,
  // otherwise the brand read silently degrades to regex-only colors with no fonts/voice/facts.
  if (!res && images.length) {
    console.error(`${LOG} visión sin resultado, reintento solo-texto con el modelo configurado…`);
    try { res = await callAIJSON<Extract>(ask, { system: ANTI_INVENTION, maxTokens: 1500, json: true }); } catch { res = null; }
  }

  const opusColors = Array.isArray(res?.colors) ? res!.colors.filter(validHex).map((c) => normHex(c)) : [];
  const colors = (opusColors.length ? opusColors : regexColors).filter(validHex).slice(0, 5);

  const name = (opts.brandName || res?.name || ogSite || ogTitle || title || host).toString().trim().slice(0, 80) || host;
  const description = (res?.description || metaDesc || '').toString().trim();
  const valueProps = Array.isArray(res?.valueProps) ? res!.valueProps.map(String).map((s) => s.trim()).filter(Boolean).slice(0, 8) : [];

  if (!colors.length && !description && !logoSrc) {
    throw new Error(`${LOG} no se pudo derivar identidad de marca de ${url} (sin colores, sin texto, sin logo). ¿La URL es correcta y pública?`);
  }

  return {
    name,
    colors: colors.length ? colors : ['#111827'],
    fonts: (res?.fonts || '').toString().trim(),
    voice: (res?.voice || '').toString().trim(),
    description,
    valueProps,
    industry: (res?.industry || '').toString().trim(),
    logoUrl: logoSrc,
    ogImage,
    sourceUrl: url,
    screenshot: !!screenshot,
  };
}

/** Turn a derived AutoBrand into the ephemeral VideoBrandCtx the generators consume. */
export function autoBrandToCtx(ab: AutoBrand): VideoBrandCtx {
  const info = [
    ab.description,
    ab.valueProps.length ? `Value props (real, from the site): ${ab.valueProps.join('; ')}.` : '',
    ab.voice ? `Voice: ${ab.voice}.` : '',
    ab.industry ? `Industry: ${ab.industry}.` : '',
    `Source: ${ab.sourceUrl}.`,
  ].filter(Boolean).join(' ');
  return {
    name: ab.name,
    colors: ab.colors,
    brandColors: ab.colors.join(', '),
    fonts: ab.fonts,
    screens: '',
    info,
    logo: ab.logoUrl,
    assets: ab.logoUrl ? [{ name: 'logo', url: ab.logoUrl }] : [],
  };
}
