// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  MARKETPLACE CARDS — drop the REAL product into a platform-accurate listing     ║
// ║                                                                              ║
// ║  Higgsfield "Product / Marketplace" parity, the FyDesign way: the product is    ║
// ║  composited PHOTOREALISTICALLY (label/logo/shape untouched) by Nano Banana Pro,  ║
// ║  then the card CHROME — title, price, CTA, brand bar — is rendered as crisp      ║
// ║  HTML/CSS and rasterized with Playwright. Text is NEVER baked by the image       ║
// ║  model (it garbles prices), and NOTHING is invented: a price/title is printed    ║
// ║  ONLY when the caller passes a real one. No fake ratings, review counts, or      ║
// ║  "Best Seller" badges. (Same rule as the rest of FyDesign: datos reales.)        ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { compositeProductKeyframe } from '../product-compositing';
import { renderHtmlToPng } from '../render-png';
import type { VideoBrandCtx, VideoAspect } from './types';

const esc = (s: string) =>
  String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

export interface CardData {
  productDataUrl: string;
  ctx: VideoBrandCtx;
  /** Real product title/headline. Falls back to the brand name. */
  title: string;
  /** Real price string to print verbatim, e.g. "$49.99" / "MXN 899". Empty → no price shown. */
  price: string;
}

export interface CardTemplate {
  key: string;
  name: string;
  desc: string;
  /** Output pixel size of the rasterized card. */
  w: number;
  h: number;
  /** Aspect for the product composite (text-safe zones left for the chrome). */
  aspect: VideoAspect;
  /** Scene the product is composited into for THIS card style. */
  scene: (brief: string) => string;
  /** The card chrome (HTML/CSS) wrapped around the composited product. */
  render: (d: CardData) => string;
}

// Shared CSS reset + brand tokens. primary = first brand color, accent = second (or primary).
function head(ctx: VideoBrandCtx): string {
  const primary = ctx.colors[0] || '#111827';
  const accent = ctx.colors[1] || primary;
  return `<style>
    *{margin:0;padding:0;box-sizing:border-box;-webkit-font-smoothing:antialiased}
    :root{--primary:${esc(primary)};--accent:${esc(accent)}}
    html,body{width:100%;height:100%;font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;background:#fff;color:#111827}
    .prod{width:100%;height:100%;object-fit:cover;display:block}
    .pill{display:inline-block;padding:6px 14px;border-radius:999px;font-weight:700;font-size:13px;letter-spacing:.02em}
    .cta{display:flex;align-items:center;justify-content:center;font-weight:800;color:#fff;background:var(--primary)}
    .brandbar{display:flex;align-items:center;gap:10px;font-weight:800;letter-spacing:.04em;text-transform:uppercase}
    .dot{width:10px;height:10px;border-radius:50%;background:var(--accent)}
  </style>`;
}

// A price block that renders ONLY when a real price exists (anti-invention).
const priceTag = (price: string, color = 'var(--primary)') =>
  price ? `<div style="font-size:34px;font-weight:900;color:${color};letter-spacing:-.01em">${esc(price)}</div>` : '';

export const CARD_TEMPLATES: CardTemplate[] = [
  {
    key: 'amazon', name: 'Amazon Listing', desc: 'Search-result style tile on white.', w: 1000, h: 1250, aspect: '1:1',
    scene: (b) => `the product centered on a pure seamless white studio background, even soft e-commerce lighting, crisp catalog focus, no props. ${b}`,
    render: (d) => `<!doctype html><html><head>${head(d.ctx)}</head><body>
      <div style="width:1000px;height:1250px;background:#fff;display:flex;flex-direction:column;border:1px solid #e5e7eb">
        <div style="flex:1;min-height:0;background:#fff;display:flex;align-items:center;justify-content:center;padding:40px">
          <img class="prod" style="object-fit:contain" src="${d.productDataUrl}"/>
        </div>
        <div style="padding:28px 36px 36px;border-top:1px solid #f1f1f1">
          <div style="font-size:26px;line-height:1.25;color:#0F1111;font-weight:500;max-height:66px;overflow:hidden">${esc(d.title)}</div>
          <div style="margin-top:14px;display:flex;align-items:center;justify-content:space-between">
            ${priceTag(d.price, '#0F1111')}
            <div class="pill cta" style="padding:14px 30px;border-radius:999px;background:#FFD814;color:#0F1111;font-size:18px">Add to Cart</div>
          </div>
        </div>
      </div></body></html>`,
  },
  {
    key: 'etsy', name: 'Etsy Listing', desc: 'Warm, handmade lifestyle card.', w: 1000, h: 1250, aspect: '3:4',
    scene: (b) => `the product styled in a warm, cozy handmade-aesthetic flat-lay with natural linen, dried flowers and soft window light, artisanal editorial mood. ${b}`,
    render: (d) => `<!doctype html><html><head>${head(d.ctx)}</head><body>
      <div style="width:1000px;height:1250px;background:#fff;display:flex;flex-direction:column;border-radius:14px;overflow:hidden;border:1px solid #eee">
        <div style="flex:1;min-height:0"><img class="prod" src="${d.productDataUrl}"/></div>
        <div style="padding:30px 34px 36px">
          <div class="brandbar" style="font-size:13px;color:#A0522D"><span class="dot" style="background:#F1641E"></span>${esc(d.ctx.name)}</div>
          <div style="margin-top:12px;font-size:27px;line-height:1.25;color:#222;font-weight:600">${esc(d.title)}</div>
          <div style="margin-top:16px">${priceTag(d.price, '#222')}</div>
        </div>
      </div></body></html>`,
  },
  {
    key: 'shopify', name: 'Shopify PDP', desc: 'Clean DTC product card.', w: 1080, h: 1350, aspect: '3:4',
    scene: (b) => `the product as a premium DTC hero on a soft pastel studio gradient, modern minimal styling, gentle shadow, generous clean space. ${b}`,
    render: (d) => `<!doctype html><html><head>${head(d.ctx)}</head><body>
      <div style="width:1080px;height:1350px;background:linear-gradient(180deg,#fafafa,#fff);display:flex;flex-direction:column">
        <div style="flex:1;min-height:0"><img class="prod" src="${d.productDataUrl}"/></div>
        <div style="padding:40px 48px 52px;background:#fff">
          <div class="brandbar" style="font-size:13px;color:var(--primary)"><span class="dot"></span>${esc(d.ctx.name)}</div>
          <div style="margin-top:14px;font-size:34px;line-height:1.2;font-weight:800;color:#111">${esc(d.title)}</div>
          <div style="margin-top:22px;display:flex;align-items:center;gap:22px">
            ${priceTag(d.price)}
            <div class="cta" style="flex:1;height:62px;border-radius:14px;font-size:19px">Comprar ahora</div>
          </div>
        </div>
      </div></body></html>`,
  },
  {
    key: 'ebay', name: 'eBay Listing', desc: 'Marketplace deal tile.', w: 1000, h: 1000, aspect: '1:1',
    scene: (b) => `the product on a clean light-gray seamless marketplace background, neutral product lighting, straightforward and trustworthy. ${b}`,
    render: (d) => `<!doctype html><html><head>${head(d.ctx)}</head><body>
      <div style="width:1000px;height:1000px;background:#fff;display:flex;flex-direction:column;border:1px solid #e5e7eb">
        <div style="flex:1;min-height:0"><img class="prod" style="object-fit:contain;background:#f7f7f7" src="${d.productDataUrl}"/></div>
        <div style="padding:26px 32px 32px">
          <div style="font-size:24px;line-height:1.25;color:#111;font-weight:500;max-height:62px;overflow:hidden">${esc(d.title)}</div>
          <div style="margin-top:14px;display:flex;align-items:center;justify-content:space-between">
            ${priceTag(d.price, '#3665F3')}
            <div class="pill" style="background:#FEE9B2;color:#5A3E00">Buy It Now</div>
          </div>
        </div>
      </div></body></html>`,
  },
  {
    key: 'mercadolibre', name: 'MercadoLibre', desc: 'LATAM marketplace card.', w: 1000, h: 1100, aspect: '1:1',
    scene: (b) => `the product on a bright clean white marketplace background, sharp friendly product lighting, LATAM e-commerce style. ${b}`,
    render: (d) => `<!doctype html><html><head>${head(d.ctx)}</head><body>
      <div style="width:1000px;height:1100px;background:#fff;display:flex;flex-direction:column;border:1px solid #ededed">
        <div style="flex:1;min-height:0"><img class="prod" style="object-fit:contain" src="${d.productDataUrl}"/></div>
        <div style="padding:26px 32px 34px;border-top:1px solid #f3f3f3">
          <div style="font-size:24px;line-height:1.25;color:#333;font-weight:400;max-height:62px;overflow:hidden">${esc(d.title)}</div>
          <div style="margin-top:14px">${priceTag(d.price, '#111')}</div>
          <div style="margin-top:8px;font-size:15px;color:#00A650;font-weight:600">Envío gratis</div>
          <div class="cta" style="margin-top:18px;height:54px;border-radius:6px;background:#3483FA;font-size:18px">Comprar ahora</div>
        </div>
      </div></body></html>`,
  },
  {
    key: 'app_store', name: 'App Store', desc: 'Feature screenshot card.', w: 1080, h: 1920, aspect: '9:16',
    scene: (b) => `the product as a bold hero on a vivid on-brand gradient, dramatic premium lighting, lots of clean space above and below for a headline. ${b}`,
    render: (d) => `<!doctype html><html><head>${head(d.ctx)}</head><body>
      <div style="width:1080px;height:1920px;position:relative;background:linear-gradient(160deg,var(--primary),var(--accent));overflow:hidden">
        <div style="position:absolute;inset:0"><img class="prod" src="${d.productDataUrl}"/></div>
        <div style="position:absolute;inset:0;background:linear-gradient(180deg,rgba(0,0,0,.45) 0%,rgba(0,0,0,0) 30%,rgba(0,0,0,0) 60%,rgba(0,0,0,.7) 100%)"></div>
        <div style="position:absolute;top:90px;left:80px;right:80px;color:#fff">
          <div class="brandbar" style="font-size:16px;opacity:.9"><span class="dot" style="background:#fff"></span>${esc(d.ctx.name)}</div>
          <div style="margin-top:20px;font-size:74px;line-height:1.05;font-weight:900;letter-spacing:-.02em;text-shadow:0 2px 24px rgba(0,0,0,.35)">${esc(d.title)}</div>
        </div>
        ${d.price ? `<div style="position:absolute;bottom:96px;left:80px;color:#fff;font-size:40px;font-weight:900">${esc(d.price)}</div>` : ''}
      </div></body></html>`,
  },
  {
    key: 'thumbnail', name: 'YouTube Thumbnail', desc: 'High-CTR 16:9 thumbnail.', w: 1280, h: 720, aspect: '16:9',
    scene: (b) => `the product as a punchy hero with bold contrast and dramatic rim lighting, energetic background, leaving the left third clear for a big headline. ${b}`,
    render: (d) => `<!doctype html><html><head>${head(d.ctx)}</head><body>
      <div style="width:1280px;height:720px;position:relative;overflow:hidden;background:#000">
        <div style="position:absolute;inset:0"><img class="prod" src="${d.productDataUrl}"/></div>
        <div style="position:absolute;inset:0;background:linear-gradient(90deg,rgba(0,0,0,.82) 0%,rgba(0,0,0,.35) 42%,rgba(0,0,0,0) 70%)"></div>
        <div style="position:absolute;top:64px;left:64px;right:540px;color:#fff">
          <div style="font-size:88px;line-height:.98;font-weight:900;letter-spacing:-.03em;text-transform:uppercase;text-shadow:0 4px 18px rgba(0,0,0,.6)">${esc(d.title)}</div>
          <div style="margin-top:26px;display:inline-block"><span class="pill" style="background:var(--accent);color:#fff;font-size:22px;padding:10px 22px">${esc(d.ctx.name)}</span></div>
        </div>
      </div></body></html>`,
  },
  {
    key: 'review_badge', name: 'Testimonial Card', desc: 'Square quote/feature card.', w: 1080, h: 1080, aspect: '1:1',
    scene: (b) => `the product elegantly placed to the right on a soft neutral studio background with premium soft lighting, leaving the left half clean for a quote. ${b}`,
    render: (d) => `<!doctype html><html><head>${head(d.ctx)}</head><body>
      <div style="width:1080px;height:1080px;position:relative;overflow:hidden;background:#fff">
        <div style="position:absolute;inset:0"><img class="prod" src="${d.productDataUrl}"/></div>
        <div style="position:absolute;inset:0;background:linear-gradient(90deg,rgba(255,255,255,.96) 0%,rgba(255,255,255,.85) 40%,rgba(255,255,255,0) 70%)"></div>
        <div style="position:absolute;top:120px;left:90px;right:480px">
          <div class="brandbar" style="font-size:15px;color:var(--primary)"><span class="dot"></span>${esc(d.ctx.name)}</div>
          <div style="margin-top:26px;font-size:46px;line-height:1.2;font-weight:800;color:#111">${esc(d.title)}</div>
          <div style="margin-top:26px">${priceTag(d.price)}</div>
        </div>
      </div></body></html>`,
  },
];

export function findCardTemplate(key?: string): CardTemplate | null {
  if (!key) return null;
  const k = key.toLowerCase().replace(/[\s-]+/g, '_');
  return CARD_TEMPLATES.find((t) => t.key === k || t.name.toLowerCase().replace(/[\s-]+/g, '_') === k) || null;
}

export function cardMenu(): Array<{ key: string; name: string; desc: string }> {
  return CARD_TEMPLATES.map((t) => ({ key: t.key, name: t.name, desc: t.desc }));
}

/**
 * Build N marketplace cards: composite the REAL product into the template's scene
 * (Nano Banana Pro, product untouched), then frame it with crisp HTML/CSS chrome and
 * rasterize to PNG. Returns data URLs. Per-card failures are tolerated; throws only if
 * every card fails. NEVER invents a price/title — prints only what the caller provides.
 */
export async function marketplaceCard(
  productSrc: string,
  ctx: VideoBrandCtx,
  opts: { template?: string; prompt?: string; title?: string; price?: string; count?: number } = {},
): Promise<{ template: string; images: string[] }> {
  const t = findCardTemplate(opts.template) || CARD_TEMPLATES[0];
  const n = Math.max(1, Math.min(4, opts.count || 1));
  const title = (opts.title || '').trim() || ctx.name;
  const price = (opts.price || '').trim();
  const images: string[] = [];
  for (let i = 0; i < n; i++) {
    try {
      const { dataUrl } = await compositeProductKeyframe(productSrc, t.scene(opts.prompt || ''), ctx, { aspect: t.aspect });
      if (!dataUrl) continue;
      const html = t.render({ productDataUrl: dataUrl, ctx, title, price });
      const png = await renderHtmlToPng(html, t.w, t.h, `card-${t.key}`, { fast: true });
      if (png.length) images.push(`data:image/png;base64,${png.toString('base64')}`);
    } catch (e) {
      console.error('[marketplace-cards] una tarjeta falló (se continúa):', e instanceof Error ? e.message : e);
    }
  }
  if (images.length === 0) throw new Error('[marketplace-cards] no se generó ninguna tarjeta (revisa la foto del producto / Playwright / GCS).');
  return { template: t.key, images };
}
