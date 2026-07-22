// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  PRODUCT PHOTOSHOOT — 10 named product-image modes (Higgsfield-style)          ║
// ║                                                                              ║
// ║  Each mode wraps a plain brief into a curated product-photography prompt and    ║
// ║  composites the user's REAL product (its exact label/shape preserved) into the  ║
// ║  scene via compositeProductKeyframe (Nano Banana Pro + product fidelity).       ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { compositeProductKeyframe } from '../product-compositing';
import type { VideoBrandCtx, VideoAspect } from './types';

export interface ShootMode {
  key: string;
  name: string;
  desc: string;
  aspect: VideoAspect;
  scene: (brief: string) => string;
}

export const SHOOT_MODES: ShootMode[] = [
  { key: 'product_shot', name: 'Product Shot', desc: 'Clean catalog shot on a seamless studio backdrop.', aspect: '1:1',
    scene: (b) => `Professional e-commerce product shot: the product centered on a seamless soft-gradient studio backdrop, even softbox lighting, crisp focus, a subtle floor reflection, premium catalog quality. ${b}` },
  { key: 'lifestyle_scene', name: 'Lifestyle Scene', desc: 'Product in a real, aspirational context.', aspect: '3:4',
    scene: (b) => `Lifestyle photo: the product naturally placed in a real aspirational everyday setting with tasteful props and soft natural window light, editorial styling, shallow depth of field. ${b}` },
  { key: 'closeup_with_person', name: 'Closeup with Person', desc: 'Hands/person using the product.', aspect: '3:4',
    scene: (b) => `Close-up of a real person's hands holding or using the product, warm natural light, authentic candid feel, the product in sharp focus. ${b}` },
  { key: 'moodboard_pin', name: 'Moodboard Pin', desc: 'Pinterest aesthetic flat-lay.', aspect: '3:4',
    scene: (b) => `A Pinterest-worthy moodboard flat-lay featuring the product, a curated palette, textures and complementary objects, soft diffused light, trendy and aspirational. ${b}` },
  { key: 'hero_banner', name: 'Hero Banner', desc: 'Web/campaign banner with copy space.', aspect: '16:9',
    scene: (b) => `A wide premium hero banner: the product as the focal point with generous negative space for copy, clean dramatic lighting, campaign-grade composition. ${b}` },
  { key: 'social_carousel', name: 'Social Carousel', desc: 'Carousel slide.', aspect: '4:3',
    scene: (b) => `A bold social-media carousel slide featuring the product, a vibrant on-brand background, clean composition with room for a short headline, scroll-stopping. ${b}` },
  { key: 'ad_creative_pack', name: 'Ad Creative', desc: 'High-converting performance ad creative.', aspect: '1:1',
    scene: (b) => `A high-converting performance ad creative: the product as hero, punchy contrast, one clear focal point, thumb-stopping energy, space for a hook. ${b}` },
  { key: 'virtual_model_tryout', name: 'Virtual Model', desc: 'A model using/wearing the product.', aspect: '3:4',
    scene: (b) => `A photoreal model naturally using or wearing the product in an editorial fashion/lifestyle setting, flattering light, the product clearly featured and recognizable. ${b}` },
  { key: 'conceptual_product', name: 'Conceptual', desc: 'CGI / surreal / floating.', aspect: '1:1',
    scene: (b) => `A surreal conceptual CGI-style render: the product floating amid abstract shapes and elements, dramatic studio lighting, bold artistic composition. ${b}` },
  { key: 'restyle', name: 'Restyle', desc: 'Re-imagine the look/scene, same product.', aspect: '1:1',
    scene: (b) => `Restyle the scene around the product into a fresh aesthetic — new background, mood and lighting — while keeping the product itself identical. ${b}` },
];

export function findShootMode(key?: string): ShootMode | null {
  if (!key) return null;
  const k = key.toLowerCase().replace(/[\s-]+/g, '_');
  return SHOOT_MODES.find((m) => m.key === k || m.name.toLowerCase().replace(/[\s-]+/g, '_') === k) || null;
}

export function shootMenu(): Array<{ key: string; name: string; desc: string }> {
  return SHOOT_MODES.map((m) => ({ key: m.key, name: m.name, desc: m.desc }));
}

/**
 * Generate N product images in a named mode, compositing the REAL product photo.
 * Returns data URLs (failures per image tolerated; throws only if all fail).
 */
export async function productPhotoshoot(
  productSrc: string,
  ctx: VideoBrandCtx,
  opts: { mode?: string; prompt?: string; count?: number; aspect?: string } = {},
): Promise<{ mode: string; images: string[] }> {
  const m = findShootMode(opts.mode) || SHOOT_MODES[0];
  const n = Math.max(1, Math.min(6, opts.count || 1));
  const aspect = (opts.aspect as VideoAspect) || m.aspect;
  const images: string[] = [];
  for (let i = 0; i < n; i++) {
    try {
      const { dataUrl } = await compositeProductKeyframe(productSrc, m.scene(opts.prompt || ''), ctx, { aspect });
      if (dataUrl) images.push(dataUrl);
    } catch (e) {
      console.error('[product-photoshoot] una imagen falló (se continúa):', e instanceof Error ? e.message : e);
    }
  }
  if (images.length === 0) throw new Error('[product-photoshoot] no se generó ninguna imagen (revisa la foto del producto / GCS).');
  return { mode: m.key, images };
}
