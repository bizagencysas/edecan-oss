// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  product-compositing — Higgsfield "Marketing Studio / Product Placement"    ║
// ║  Parity: composite a REAL product photo into a generated scene keyframe     ║
// ║  while preserving the product's logo, on-pack text, shape and materials.   ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { generateImagenImage } from './ai/imagen-client';
import { loadRefInline } from './ai/brand-image';
import type { VideoBrandCtx, VideoAspect } from './video/types';

/** The instruction appended to every product-compositing prompt. */
const PRODUCT_FIDELITY_DIRECTIVE =
  'CRITICAL product-placement rules: ' +
  '(1) Place the EXACT product from the reference image naturally in the scene — its shape, ' +
  'materials, finish, packaging geometry, label art and brand logo must be UNCHANGED and ' +
  'photorealistic. Do NOT alter, simplify or redraw the product. ' +
  '(2) Treat the product as a hero: it must be clearly visible, well-lit and photogenic. ' +
  '(3) Leave generous negative space (clean background area) around / above / below the ' +
  'product for headline copy — a comfortable text-safe zone. ' +
  '(4) The rest of the scene (background, environment, props, atmosphere, people) may be ' +
  'fully generated; only the product itself must be faithful to the reference. ' +
  '(5) No generated text anywhere outside the product label that is already on the product ' +
  'in the reference. No watermarks, no titles, no captions, no stickers in the scene.';

/**
 * Composite a real product photo into a generated scene keyframe.
 *
 * Loads productSrc (and any extraRefs, e.g. a brand logo image) via loadRefInline
 * as Vertex reference images; builds a composite prompt; calls generateImagenImage
 * via the Nano Banana (gemini-*-image) path so references are honored.
 *
 * @param productSrc  Path, HTTP URL or data URL of the product photo.
 * @param scenePrompt Natural-language description of the desired scene/setting.
 * @param ctx         Brand context (colors, name, etc.) for on-brand scene cues.
 * @param opts.aspect Aspect ratio for the output image (default '9:16').
 * @param opts.extraRefs Additional reference images (brand logo, prop, etc.).
 * @returns           data URL of the composited keyframe + model identifier.
 */
export async function compositeProductKeyframe(
  productSrc: string,
  scenePrompt: string,
  ctx: VideoBrandCtx,
  opts?: { aspect?: VideoAspect; extraRefs?: string[] },
): Promise<{ dataUrl: string; model: string }> {
  const aspect = opts?.aspect ?? '9:16';
  const model =
    process.env.GOOGLE_PREMIUM_IMAGE_MODEL || 'gemini-3-pro-image-preview';

  // Load all reference images in parallel; silently drop any that fail.
  const refSrcs = [productSrc, ...(opts?.extraRefs ?? [])];
  const refResults = await Promise.all(refSrcs.map((src) => loadRefInline(src)));
  const references = refResults.filter(
    (r): r is { data: string; mimeType: string } => r !== null,
  );

  if (references.length === 0) {
    console.error(
      '[product-compositing] No se pudo cargar ninguna imagen de referencia; ' +
        'la imagen se generará sin referencias.',
    );
  }

  // Build the compositing prompt: scene context + strict fidelity rules.
  const brandCue =
    ctx.colors.length > 0
      ? ` Brand palette: ${ctx.brandColors}.`
      : '';
  const prompt =
    `${scenePrompt}${brandCue}\n\n` +
    PRODUCT_FIDELITY_DIRECTIVE;

  try {
    const result = await generateImagenImage(prompt, {
      aspectRatio: aspect,
      references,
      model,
      // We want product label/logo preserved, so allow text rendering on the product itself.
      allowText: true,
    });
    return { dataUrl: result.dataUrl, model: `vertex:nano-banana(product)` };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error(`[product-compositing] Error generando keyframe de producto: ${msg}`);
    throw err;
  }
}

/**
 * Generate a set of varied on-brand product hero shots.
 *
 * Each shot composites the real product photo into a different angle/setting
 * using compositeProductKeyframe. Failures per image are tolerated — the
 * successful data URLs are returned; partial results are valid.
 *
 * @param productSrc  Path, HTTP URL or data URL of the product photo.
 * @param ctx         Brand context.
 * @param opts.count  Number of hero shots to generate (default 3).
 * @param opts.aspect Aspect ratio (default '9:16').
 * @returns           Array of data URLs (length <= opts.count).
 */
export async function productHeroSet(
  productSrc: string,
  ctx: VideoBrandCtx,
  opts?: { count?: number; aspect?: VideoAspect },
): Promise<string[]> {
  const count = opts?.count ?? 3;
  const aspect = opts?.aspect ?? '9:16';

  // Varied scene prompts — different angles, settings and moods for diversity.
  const sceneVariants: string[] = [
    `Editorial product hero shot: the product is centered on a clean, textured studio surface ` +
      `with soft directional light, shallow depth of field, minimalist composition, ` +
      `premium lifestyle feel. On-brand color palette in the background.`,
    `Lifestyle product shot: the product appears naturally in a real-world setting that ` +
      `reflects its ideal consumer moment — warm natural light, environmental context, ` +
      `candid yet styled, photojournalistic energy.`,
    `Dramatic close-up product hero: extreme angle, rich rim lighting that reveals the ` +
      `product's materials and texture, dark moody background with a single strong key light, ` +
      `luxury commercial photography.`,
    `Flat-lay product composition: the product is arranged top-down with complementary props ` +
      `and brand-color accents, overhead studio lighting, clean negative space to the sides.`,
    `Dynamic motion blur product shot: the product is sharp in the foreground while the ` +
      `background has a subtle zoom blur suggesting energy and speed, bold and punchy.`,
  ];

  // Take 'count' variants (cycle if count > preset list).
  const selected = Array.from(
    { length: count },
    (_, i) => sceneVariants[i % sceneVariants.length],
  );

  const results: string[] = [];

  for (let i = 0; i < selected.length; i++) {
    const variant = selected[i];
    try {
      console.error(
        `[product-compositing] Generando hero shot ${i + 1}/${count} — ` +
          `"${variant.slice(0, 60)}…"`,
      );
      const { dataUrl } = await compositeProductKeyframe(productSrc, variant, ctx, {
        aspect,
      });
      results.push(dataUrl);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error(
        `[product-compositing] Hero shot ${i + 1}/${count} falló (se omite): ${msg}`,
      );
      // Tolerate per-image failure — continue with remaining shots.
    }
  }

  console.error(
    `[product-compositing] Hero set completo: ${results.length}/${count} imágenes generadas.`,
  );
  return results;
}
