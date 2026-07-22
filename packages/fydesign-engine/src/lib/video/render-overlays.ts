// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  render-overlays.ts — brand motion-graphics overlays → PNG (via Playwright) ║
// ║                                                                              ║
// ║  Renders transparent PNG overlays (shot copy, logo bug) and an opaque end   ║
// ║  card that ffmpeg composites over video clips.                               ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { renderHtmlToPng } from '../render-png';
import type { VideoBrandCtx, ShotOverlay } from './types';

// ── helpers ─────────────────────────────────────────────────────────────────

/** Escape user-supplied copy for safe inline HTML. */
function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/**
 * Build a <link> tag to load the brand font from Google Fonts.
 * Falls back to Inter if the font string looks unusual or is absent.
 */
function googleFontLink(fonts: string): string {
  // fonts may be "Poppins, sans-serif" or just "Inter" — grab the first token.
  const first = (fonts || '').split(',')[0].trim().replace(/['"]/g, '');
  if (!first || first.toLowerCase().startsWith('system') || first.toLowerCase() === 'sans-serif') {
    return `<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">`;
  }
  const encoded = encodeURIComponent(first);
  return `<link href="https://fonts.googleapis.com/css2?family=${encoded}:wght@400;600;700;800&display=swap" rel="stylesheet">`;
}

/** Resolve the best logomark data URL from the token map. */
function resolveLogomark(logoTokens: Record<string, string>): string | null {
  return logoTokens['__LOGOMARK_LIGHT__'] || logoTokens['__LOGOMARK__'] || null;
}

/** Resolve the best full logo data URL from the token map. */
function resolveLogo(logoTokens: Record<string, string>): string | null {
  return logoTokens['__LOGO_LIGHT__'] || logoTokens['__LOGO__'] || null;
}

/** Return a "safe" accent color — default to a neutral brand blue if missing. */
function accentColor(ctx: VideoBrandCtx): string {
  return (ctx.colors && ctx.colors[0]) || '#1A56FF';
}

/** Derive a readable family name from ctx.fonts for CSS. */
function fontFamily(ctx: VideoBrandCtx): string {
  const raw = (ctx.fonts || '').split(',')[0].trim().replace(/['"]/g, '');
  if (!raw || raw.toLowerCase().startsWith('system')) return 'Inter, system-ui, sans-serif';
  return `'${raw}', Inter, system-ui, sans-serif`;
}

// ── color contrast helpers ────────────────────────────────────────────────────
function parseColor(c: string): { r: number; g: number; b: number } | null {
  if (!c) return null;
  const s = c.trim();
  let m = /^#([0-9a-f]{3})$/i.exec(s);
  if (m) { const h = m[1]; return { r: parseInt(h[0] + h[0], 16), g: parseInt(h[1] + h[1], 16), b: parseInt(h[2] + h[2], 16) }; }
  m = /^#([0-9a-f]{6})$/i.exec(s);
  if (m) { const h = m[1]; return { r: parseInt(h.slice(0, 2), 16), g: parseInt(h.slice(2, 4), 16), b: parseInt(h.slice(4, 6), 16) }; }
  m = /^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/i.exec(s);
  if (m) return { r: +m[1], g: +m[2], b: +m[3] };
  return null;
}
/** Perceived luminance 0 (black) … 1 (white). */
function luminance(c: string): number {
  const p = parseColor(c);
  if (!p) return 0.5;
  return (0.299 * p.r + 0.587 * p.g + 0.114 * p.b) / 255;
}
function toHex({ r, g, b }: { r: number; g: number; b: number }): string {
  const h = (n: number) => Math.max(0, Math.min(255, Math.round(n))).toString(16).padStart(2, '0');
  return `#${h(r)}${h(g)}${h(b)}`;
}
/** Mix a color toward black by `amount` (0..1). */
function darken(c: string, amount: number): string {
  const p = parseColor(c) || { r: 26, g: 86, b: 255 };
  return toHex({ r: p.r * (1 - amount), g: p.g * (1 - amount), b: p.b * (1 - amount) });
}
/**
 * A guaranteed-DARK, on-brand end-card background. Picks the darkest brand color
 * if it's dark enough; otherwise darkens the accent into a deep brand-tinted tone.
 * Prevents the white-on-pale-tint invisibility when colors[0] is a light tint.
 */
function darkOnBrandBg(ctx: VideoBrandCtx): string {
  const candidates = (ctx.colors || []).filter(Boolean);
  const darkest = candidates.map((c) => ({ c, l: luminance(c) })).sort((a, b) => a.l - b.l)[0];
  if (darkest && darkest.l <= 0.45) return darkest.c;          // a real dark brand color exists
  const accent = accentColor(ctx);
  return luminance(accent) > 0.45 ? darken(accent, 0.74) : accent; // darken a light accent
}

// ── renderShotOverlay ────────────────────────────────────────────────────────

/**
 * Render a transparent, full-frame PNG overlay carrying on-brand motion-graphics
 * copy for a single shot: headline, subtext, CTA pill, and/or a lower-third strip.
 *
 * Returns null when the overlay has no visible copy (nothing to burn).
 */
export async function renderShotOverlay(
  ctx: VideoBrandCtx,
  overlay: ShotOverlay,
  size: { w: number; h: number },
  logoTokens: Record<string, string>,
): Promise<Buffer | null> {
  const { headline, subtext, cta, lowerThird } = overlay;
  const hasCopy = headline || subtext || cta || lowerThird;
  if (!hasCopy) return null;

  const accent = accentColor(ctx);
  const ff = fontFamily(ctx);
  const position = overlay.position || 'bottom';
  const logomarkSrc = resolveLogomark(logoTokens);

  // Scale typography and spacing relative to frame height — tasteful, not shouty.
  const basePx = Math.round(size.h * 0.026);
  const headlinePx = Math.round(size.h * 0.044);       // was 0.072 (gigantic) → tighter
  const subPx = Math.round(size.h * 0.024);
  const ctaPx = Math.round(size.h * 0.022);
  const padH = Math.round(size.w * 0.07);
  const padV = Math.round(size.h * 0.055);

  // Logo bug: fixed top corner, ~5% of frame width.
  const bugSize = Math.round(size.w * 0.05);
  const logomarkHtml = logomarkSrc
    ? `<img src="${logomarkSrc}" alt="logo" style="
        position:fixed; top:${Math.round(size.h * 0.025)}px; left:${Math.round(size.w * 0.025)}px;
        width:${bugSize}px; height:${bugSize}px; object-fit:contain; opacity:0.92;
        filter:drop-shadow(0 1px 4px rgba(0,0,0,0.45));
        z-index:10;" />`
    : '';

  // Position of the copy band.
  const alignY =
    position === 'top'
      ? `top:${padV}px;`
      : position === 'center'
        ? `top:50%; transform:translateY(-50%);`
        : `bottom:${padV}px;`;

  // Lower-third strip (sits below main copy when position=bottom, else above).
  const lowerThirdHtml = lowerThird
    ? `<div style="
        display:inline-block;
        background:${accent};
        color:#fff;
        font-size:${Math.round(basePx * 0.72)}px;
        font-weight:700;
        letter-spacing:0.08em;
        text-transform:uppercase;
        padding:${Math.round(basePx * 0.3)}px ${Math.round(basePx * 0.7)}px;
        border-radius:3px;
        margin-top:${Math.round(basePx * 0.5)}px;
      ">${escapeHtml(lowerThird)}</div>`
    : '';

  const ctaHtml = cta
    ? `<div style="margin-top:${Math.round(basePx * 0.6)}px;">
        <span style="
          display:inline-block;
          background:${accent};
          color:#fff;
          font-size:${ctaPx}px;
          font-weight:700;
          padding:${Math.round(ctaPx * 0.55)}px ${Math.round(ctaPx * 1.4)}px;
          border-radius:${Math.round(ctaPx * 1.2)}px;
          letter-spacing:0.03em;
          box-shadow:0 3px 14px rgba(0,0,0,0.35);
        ">${escapeHtml(cta)}</span>
      </div>`
    : '';

  const headlineHtml = headline
    ? `<div style="
        font-size:${headlinePx}px;
        font-weight:800;
        line-height:1.08;
        color:#fff;
        text-shadow:0 2px 12px rgba(0,0,0,0.55), 0 1px 3px rgba(0,0,0,0.5);
        letter-spacing:-0.01em;
        max-width:${Math.round(size.w * 0.82)}px;
      ">${escapeHtml(headline)}</div>`
    : '';

  const subtextHtml = subtext
    ? `<div style="
        font-size:${subPx}px;
        font-weight:600;
        color:rgba(255,255,255,0.88);
        text-shadow:0 1px 6px rgba(0,0,0,0.5);
        margin-top:${Math.round(basePx * 0.35)}px;
        max-width:${Math.round(size.w * 0.75)}px;
        line-height:1.35;
      ">${escapeHtml(subtext)}</div>`
    : '';

  // Scrim gradient — covers only the text band, stays fully transparent elsewhere.
  // For top: gradient goes downward; for bottom: upward; for center: radial.
  const scrimGradient =
    position === 'top'
      ? `linear-gradient(to bottom, rgba(0,0,0,0.62) 0%, rgba(0,0,0,0) 100%)`
      : position === 'center'
        ? `radial-gradient(ellipse 90% 55% at 50% 50%, rgba(0,0,0,0.55) 0%, rgba(0,0,0,0) 100%)`
        : `linear-gradient(to top, rgba(0,0,0,0.68) 0%, rgba(0,0,0,0) 100%)`;

  // Scrim covers ~40% of frame height behind the text band.
  const scrimHeight = Math.round(size.h * 0.42);
  const scrimPositionCss =
    position === 'top'
      ? `top:0; left:0; right:0; height:${scrimHeight}px;`
      : position === 'center'
        ? `top:${Math.round((size.h - scrimHeight) / 2)}px; left:0; right:0; height:${scrimHeight}px;`
        : `bottom:0; left:0; right:0; height:${scrimHeight}px;`;

  const html = `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
${googleFontLink(ctx.fonts)}
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  html, body {
    width: ${size.w}px; height: ${size.h}px;
    overflow: hidden; background: transparent;
    font-family: ${ff};
  }
</style>
</head>
<body>
  <!-- Gradient scrim behind the text band only -->
  <div style="
    position:fixed; ${scrimPositionCss}
    background:${scrimGradient};
    pointer-events:none; z-index:1;
  "></div>

  <!-- Copy band -->
  <div style="
    position:fixed; left:${padH}px; right:${padH}px;
    ${alignY}
    z-index:2;
    display:flex; flex-direction:column;
    ${position === 'center' ? 'align-items:center; text-align:center;' : 'align-items:flex-start; text-align:left;'}
  ">
    ${headlineHtml}
    ${subtextHtml}
    ${ctaHtml}
    ${lowerThirdHtml}
  </div>

  <!-- Logo bug -->
  ${logomarkHtml}
</body>
</html>`;

  try {
    const buf = await renderHtmlToPng(html, size.w, size.h, 'shot-overlay', { transparent: true });
    return buf;
  } catch (err) {
    console.error('[render-overlays] renderShotOverlay falló:', err instanceof Error ? err.message : err);
    throw err;
  }
}

// ── renderEndCard ────────────────────────────────────────────────────────────

/**
 * Render a full-frame OPAQUE branded end card.
 * Solid on-brand background, centered brand lockup (logo or wordmark), headline,
 * CTA pill, and small handle below.
 */
export async function renderEndCard(
  ctx: VideoBrandCtx,
  endCard: { headline?: string; cta?: string; handle?: string },
  size: { w: number; h: number },
  logoTokens: Record<string, string>,
): Promise<Buffer> {
  const accent = accentColor(ctx);
  const ff = fontFamily(ctx);

  // Guaranteed-dark, on-brand background so the white lockup/headline/CTA always
  // have strong contrast (fixes white-on-pale-tint when colors[0] is a light tint).
  const bgColor = darkOnBrandBg(ctx);
  // CTA pill text uses the dark bg tone on a white pill → always legible.
  const ctaTextColor = bgColor;

  const headlinePx = Math.round(size.h * 0.046);
  const ctaPx = Math.round(size.h * 0.024);
  const handlePx = Math.round(size.h * 0.020);
  const logoMaxW = Math.round(size.w * 0.42);
  const logoMaxH = Math.round(size.h * 0.14);

  // Logo: prefer full logo for end card.
  const logoSrc = resolveLogo(logoTokens);
  const wordmark = escapeHtml(ctx.name || 'Brand');

  const lockupHtml = logoSrc
    ? `<img src="${logoSrc}" alt="${wordmark}" style="
        max-width:${logoMaxW}px; max-height:${logoMaxH}px;
        object-fit:contain; display:block; margin:0 auto;
        filter:brightness(0) invert(1);
        margin-bottom:${Math.round(size.h * 0.04)}px;
      " />`
    : `<div style="
        font-size:${Math.round(size.h * 0.056)}px;
        font-weight:800;
        color:#fff;
        letter-spacing:-0.02em;
        margin-bottom:${Math.round(size.h * 0.04)}px;
        text-shadow:0 2px 8px rgba(0,0,0,0.2);
      ">${wordmark}</div>`;

  const headlineHtml = endCard.headline
    ? `<div style="
        font-size:${headlinePx}px;
        font-weight:700;
        color:#fff;
        line-height:1.1;
        letter-spacing:-0.01em;
        text-shadow:0 2px 10px rgba(0,0,0,0.25);
        margin-bottom:${Math.round(size.h * 0.032)}px;
        max-width:${Math.round(size.w * 0.78)}px;
        text-align:center;
      ">${escapeHtml(endCard.headline)}</div>`
    : '';

  const ctaHtml = endCard.cta
    ? `<div style="margin-bottom:${Math.round(size.h * 0.03)}px;">
        <span style="
          display:inline-block;
          background:#fff;
          color:${ctaTextColor};
          font-size:${ctaPx}px;
          font-weight:700;
          padding:${Math.round(ctaPx * 0.6)}px ${Math.round(ctaPx * 1.6)}px;
          border-radius:${Math.round(ctaPx * 1.2)}px;
          letter-spacing:0.02em;
          box-shadow:0 4px 18px rgba(0,0,0,0.22);
        ">${escapeHtml(endCard.cta)}</span>
      </div>`
    : '';

  const handleHtml = endCard.handle
    ? `<div style="
        font-size:${handlePx}px;
        font-weight:600;
        color:rgba(255,255,255,0.7);
        letter-spacing:0.02em;
        margin-top:${Math.round(size.h * 0.012)}px;
      ">${escapeHtml(endCard.handle)}</div>`
    : '';

  const html = `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
${googleFontLink(ctx.fonts)}
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  html, body {
    width: ${size.w}px; height: ${size.h}px;
    overflow: hidden;
    background: ${bgColor};
    font-family: ${ff};
    display: flex; align-items: center; justify-content: center;
  }
  .card {
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    text-align: center;
    padding: ${Math.round(size.w * 0.08)}px;
    width: 100%;
  }
</style>
</head>
<body>
  <div class="card">
    ${lockupHtml}
    ${headlineHtml}
    ${ctaHtml}
    ${handleHtml}
  </div>
</body>
</html>`;

  try {
    const buf = await renderHtmlToPng(html, size.w, size.h, 'end-card');
    return buf;
  } catch (err) {
    console.error('[render-overlays] renderEndCard falló:', err instanceof Error ? err.message : err);
    throw err;
  }
}

// ── renderLogoBug ─────────────────────────────────────────────────────────────

/**
 * Render a tiny transparent overlay — full-frame but mostly empty — with just the
 * logomark pinned to a corner at ~7% of frame width.
 *
 * Returns null when no logomark token is available.
 */
export async function renderLogoBug(
  logoTokens: Record<string, string>,
  size: { w: number; h: number },
): Promise<Buffer | null> {
  const logomarkSrc = resolveLogomark(logoTokens);
  if (!logomarkSrc) return null;

  const bugSize = Math.round(size.w * 0.07);
  const margin = Math.round(size.w * 0.025);

  const html = `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  html, body {
    width: ${size.w}px; height: ${size.h}px;
    overflow: hidden; background: transparent;
  }
</style>
</head>
<body>
  <img src="${logomarkSrc}" alt="logo" style="
    position:fixed;
    top:${margin}px; left:${margin}px;
    width:${bugSize}px; height:${bugSize}px;
    object-fit:contain;
    opacity:0.92;
    filter:drop-shadow(0 1px 4px rgba(0,0,0,0.45));
  " />
</body>
</html>`;

  try {
    const buf = await renderHtmlToPng(html, size.w, size.h, 'logo-bug', { transparent: true });
    return buf;
  } catch (err) {
    console.error('[render-overlays] renderLogoBug falló:', err instanceof Error ? err.message : err);
    throw err;
  }
}
