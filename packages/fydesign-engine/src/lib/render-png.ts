// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Shared HTML → PNG renderer (Playwright)                                   ║
// ║  Single source of truth used by:                                           ║
// ║    • /api/design/export-png  (the deployed app)                            ║
// ║    • scripts/fydesign-gen.ts (one-shot local generator, zero server)       ║
// ║  Relative import of ./browser so it resolves under Next AND tsx.           ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { launchBrowser } from './browser';

/**
 * Strip the fyd-export-bar (CSS + JS + HTML) before rendering — it would appear
 * in the screenshot and its CDN <script> can block Playwright.
 */
export function stripExportBar(html: string): string {
  return html
    .replace(/<style id="fyd-export-bar-css">[\s\S]*?<\/style>/gi, '')
    .replace(/<script id="fyd-export-bar-js">[\s\S]*?<\/script>/gi, '')
    .replace(/<div id="fyd-export-bar">[\s\S]*?<\/div>/gi, '')
    .replace(/<div id="fyd-export-tooltip"[^>]*>[\s\S]*?<\/div>/gi, '');
}

/**
 * Render a self-contained HTML string to a PNG buffer at exact pixel dimensions.
 * Mirrors the live-preview pipeline: load → let entry animations run 2s → freeze
 * → clean up animation artifacts → screenshot.
 */
export async function renderHtmlToPng(
  html: string,
  width: number,
  height: number,
  label?: string,
  opts?: { transparent?: boolean; fast?: boolean; blockNetwork?: boolean },
): Promise<Buffer> {
  if (!html) throw new Error('html required');
  if (!(width > 0) || !(height > 0)) throw new Error('width and height required');
  const transparent = !!opts?.transparent;
  // Video overlays (logo bugs, lower-thirds, end-cards) are STATIC and need a
  // transparent background so ffmpeg can composite them over a clip. `fast` skips
  // the 2s entry-animation settle that static slides use.
  const fast = !!opts?.fast || transparent;

  const cleanHtml = stripExportBar(html);
  const browser = await launchBrowser();
  try {
    const page = await browser.newPage();

    // Block external <script> requests that would hang Playwright (keep CSS + images).
    await page.route('**/*', (route) => {
      const url = route.request().url();
      const type = route.request().resourceType();
      if (opts?.blockNetwork && /^https?:/i.test(url)) {
        route.abort().catch(() => {});
        return;
      }
      if (type === 'script' && !url.startsWith('data:')) {
        route.abort().catch(() => {});
        return;
      }
      route.continue().catch(() => {});
    });

    await page.setViewportSize({ width, height });
    await page
      .setContent(cleanHtml, { waitUntil: 'domcontentloaded', timeout: 20000 })
      .catch((e) => console.error('[render-png] setContent warning:', e instanceof Error ? e.message : e));

    // Phase 0: fonts ready
    await page
      .evaluate(() => (document as Document & { fonts?: { ready: Promise<unknown> } }).fonts?.ready)
      .catch(() => {});

    // Phase 1: let entry animations run naturally for 2s (don't freeze mid-frame)
    await page.waitForTimeout(fast ? 100 : 2000);

    // Phase 2: freeze looping animations without resetting position
    await page.addStyleTag({
      content: `*, *::before, *::after {
        animation-play-state: paused !important;
        transition-duration: 0s !important;
        transition-delay: 0s !important;
      }`,
    });

    // Phase 3: force-finish Web Animations API animations
    await page
      .evaluate(() => {
        try {
          (document.getAnimations as (opts?: { subtree?: boolean }) => Animation[])({ subtree: true }).forEach(
            (anim) => {
              try {
                anim.finish();
              } catch {
                /* non-finishable */
              }
            },
          );
        } catch {
          /* unsupported */
        }
      })
      .catch(() => {});

    // Phase 4: clean up animation artifacts (decorative overlays, ghost elements)
    await page
      .evaluate(() => {
        document.querySelectorAll('*').forEach((el) => {
          const style = getComputedStyle(el);
          const htmlEl = el as HTMLElement;
          const pos = style.position;
          if ((pos === 'absolute' || pos === 'fixed') && el.children.length === 0) {
            const bg = style.backgroundColor;
            const hasAnim = style.animationName && style.animationName !== 'none';
            const text = (el.textContent || '').trim();
            if (hasAnim && bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'transparent' && !text) {
              htmlEl.style.setProperty('display', 'none', 'important');
              return;
            }
          }
          const opacity = parseFloat(style.opacity);
          if (opacity > 0 && opacity < 0.05) {
            htmlEl.style.setProperty('opacity', '0', 'important');
            htmlEl.style.setProperty('pointer-events', 'none', 'important');
          }
        });
      })
      .catch(() => {});

    await page.waitForTimeout(200);

    if (label) console.error(`[render-png] ${width}×${height} (${label})`);
    const buffer = await page.screenshot({ type: 'png', fullPage: false, omitBackground: transparent });
    return Buffer.from(buffer);
  } finally {
    await browser.close();
  }
}
