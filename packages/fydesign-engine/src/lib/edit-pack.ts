// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  edit-pack — image EDIT superpowers (Higgsfield app-pack clone)             ║
// ║                                                                              ║
// ║  Each function resolves the input to a fetchable URL via hostStillForMuapi, ║
// ║  calls muapiGenerate(endpoint, body), and returns the standard media shape: ║
// ║    { url?, dataUrl?, file?, model: string, cost? }                          ║
// ║                                                                              ║
// ║  All endpoint names are overridable via MUAPI_*_MODEL env vars.             ║
// ║  Blueprint: docs/higgsfield-blueprint.md §Enhance & Style app pack          ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { muapiGenerate } from './ai/muapi-client';
import { hostStillForMuapi } from './ai/brand-image';
import { IMAGE_STYLES as CATALOG_STYLES, find as catalogFind } from './presets/catalog';

// ─── Return type ──────────────────────────────────────────────────────────────

export interface EditResult {
  url?: string;
  dataUrl?: string;
  file?: string;
  model: string;
  cost?: { amount_usd?: number } | null;
}

// ─── Style lookup (canonical source: src/lib/presets/catalog.ts) ─────────────

/**
 * Resolve the prompt clause for a style key from the canonical catalog.
 * Normalises the key (lowercase, spaces → dashes) before lookup.
 * Returns undefined when the key is absent from the catalog.
 */
function resolveStyleClause(styleKey: string): string | undefined {
  const normalised = styleKey.toLowerCase().replace(/\s+/g, '-');
  return catalogFind(CATALOG_STYLES, normalised)?.prompt;
}

/**
 * Apply a named style key from the canonical IMAGE_STYLES catalog
 * (src/lib/presets/catalog.ts) into a text instruction.
 * Returns the instruction unchanged if the key is not found in the catalog.
 */
export function applyStyle(instruction: string, styleKey: string): string {
  const clause = resolveStyleClause(styleKey);
  if (!clause) return instruction;
  return `${instruction}. Style the result ${clause}`;
}

// ─── Private helpers ──────────────────────────────────────────────────────────

/** opts bag accepted by every function for endpoint override. */
interface EditOpts {
  /** Override the Muapi endpoint. */
  model?: string;
}

/**
 * Route through nano-banana-edit (required: prompt + images_list).
 * images_list[0] is always the main imageUrl; append any extra refs after it.
 */
async function nanaBananaEdit(
  endpoint: string,
  imageUrl: string,
  prompt: string,
  extraRefs: string[] = [],
): Promise<EditResult> {
  const body: Record<string, unknown> = {
    prompt,
    images_list: [imageUrl, ...extraRefs],
  };
  const result = await muapiGenerate(endpoint, body);
  return {
    url: result.outputs?.[0] ?? undefined,
    model: `muapi:${endpoint}`,
    cost: result.cost ?? null,
  };
}

/**
 * Route through a single-image-only endpoint (required: image_url only).
 * Used by: ai-image-extension, ai-background-remover, ai-skin-enhancer.
 */
async function imageUrlOnlyEdit(
  endpoint: string,
  imageUrl: string,
): Promise<EditResult> {
  const result = await muapiGenerate(endpoint, { image_url: imageUrl });
  return {
    url: result.outputs?.[0] ?? undefined,
    model: `muapi:${endpoint}`,
    cost: result.cost ?? null,
  };
}

// ─── Public API ───────────────────────────────────────────────────────────────

// ─── 1. Inpaint ───────────────────────────────────────────────────────────────

/**
 * Brush-inpaint: rewrite the region described in instruction while keeping
 * everything else intact (Higgsfield "Soul Inpaint" / "Nano Banana Pro Inpaint").
 * Endpoint: nano-banana-edit  (overridable via MUAPI_INPAINT_MODEL)
 */
export async function inpaint(
  imageSrc: string,
  instruction: string,
  opts: EditOpts = {},
): Promise<EditResult> {
  const endpoint = opts.model || process.env.MUAPI_INPAINT_MODEL || 'nano-banana-edit';
  console.error('[edit-pack] inpaint →', endpoint);
  try {
    const imageUrl = await hostStillForMuapi(imageSrc);
    return await nanaBananaEdit(endpoint, imageUrl, instruction);
  } catch (e) {
    console.error('[edit-pack] inpaint falló:', e);
    throw e;
  }
}

// ─── 2. Place Object (Banana Placement) ───────────────────────────────────────

/**
 * Place / swap an object or product into the image at a region described in
 * instruction. Optionally pass a product reference image (Higgsfield "Banana
 * Placement" — prompt or 1-2 refs, no precise mask required).
 * Endpoint: nano-banana-edit  (overridable via MUAPI_PLACE_OBJECT_MODEL)
 */
export async function placeObject(
  imageSrc: string,
  instruction: string,
  productRefUrl?: string,
  opts: EditOpts = {},
): Promise<EditResult> {
  const endpoint = opts.model || process.env.MUAPI_PLACE_OBJECT_MODEL || 'nano-banana-edit';
  console.error('[edit-pack] placeObject →', endpoint);
  try {
    const imageUrl = await hostStillForMuapi(imageSrc);
    const extraRefs = productRefUrl ? [productRefUrl] : [];
    return await nanaBananaEdit(endpoint, imageUrl, instruction, extraRefs);
  } catch (e) {
    console.error('[edit-pack] placeObject falló:', e);
    throw e;
  }
}

// ─── 3. Expand Image (outpaint) ────────────────────────────────────────────────

/**
 * Extend the image beyond its edges with context-aware generation (Higgsfield
 * "Expand Image"). The ai-image-extension endpoint only takes image_url —
 * direction and aspect are not controllable via this API.
 * Endpoint: ai-image-extension  (overridable via MUAPI_EXPAND_IMAGE_MODEL)
 */
export async function expandImage(
  imageSrc: string,
  opts: EditOpts & { aspect?: string } = {},
): Promise<EditResult> {
  const endpoint = opts.model || process.env.MUAPI_EXPAND_IMAGE_MODEL || 'ai-image-extension';
  console.error('[edit-pack] expandImage →', endpoint);
  try {
    const imageUrl = await hostStillForMuapi(imageSrc);
    // ai-image-extension real schema: required [image_url] only — no prompt, no aspect_ratio.
    return await imageUrlOnlyEdit(endpoint, imageUrl);
  } catch (e) {
    console.error('[edit-pack] expandImage falló:', e);
    throw e;
  }
}

// ─── 4. Relight ───────────────────────────────────────────────────────────────

/**
 * 3-D–aware relighting (Higgsfield "Relight"). Translates direction-pad,
 * temperature and mode into an instruction Nano Banana can execute.
 * Endpoint: nano-banana-edit  (overridable via MUAPI_RELIGHT_MODEL)
 */
export async function relight(
  imageSrc: string,
  opts: EditOpts & {
    direction?: 'top' | 'front' | 'right' | 'left' | 'back' | 'bottom';
    temperature?: number; // Kelvin, e.g. 3200 (warm) — 6500 (cool)
    mode?: 'soft' | 'hard';
  } = {},
): Promise<EditResult> {
  const endpoint = opts.model || process.env.MUAPI_RELIGHT_MODEL || 'nano-banana-edit';
  const dir = opts.direction || 'front';
  const kelvin = opts.temperature ?? 5500;
  const mode = opts.mode || 'soft';
  const instruction = `Relight the subject from the ${dir}. Key light at ${kelvin}K. ${mode === 'hard' ? 'Hard key with deep shadows.' : 'Soft wraparound light, minimal harsh shadows.'} Preserve all other elements of the scene.`;
  console.error('[edit-pack] relight →', endpoint, { dir, kelvin, mode });
  try {
    const imageUrl = await hostStillForMuapi(imageSrc);
    return await nanaBananaEdit(endpoint, imageUrl, instruction);
  } catch (e) {
    console.error('[edit-pack] relight falló:', e);
    throw e;
  }
}

// ─── 5. Remove Background ─────────────────────────────────────────────────────

/**
 * Remove the background, returning a transparent PNG URL (Higgsfield "Background
 * Remover"). Best for products, portraits, cut-outs.
 * Endpoint: ai-background-remover  (overridable via MUAPI_BG_REMOVER_MODEL)
 */
export async function removeBackground(
  imageSrc: string,
  opts: EditOpts = {},
): Promise<EditResult> {
  const endpoint = opts.model || process.env.MUAPI_BG_REMOVER_MODEL || 'ai-background-remover';
  console.error('[edit-pack] removeBackground →', endpoint);
  try {
    const imageUrl = await hostStillForMuapi(imageSrc);
    // ai-background-remover real schema: required [image_url] only.
    return await imageUrlOnlyEdit(endpoint, imageUrl);
  } catch (e) {
    console.error('[edit-pack] removeBackground falló:', e);
    throw e;
  }
}

// ─── 6. Swap Outfit ───────────────────────────────────────────────────────────

/**
 * Virtual try-on / outfit swap (Higgsfield "Outfit Swap" / "AI Stylist"). Describe
 * the target garment in instruction; optionally supply a garment reference image.
 *
 * With garmentRefUrl → ai-dress-change (required: model_image_url, garment_image_url).
 * Without garmentRefUrl → nano-banana-edit with a text instruction.
 *
 * Endpoint: ai-dress-change / nano-banana-edit  (overridable via MUAPI_OUTFIT_SWAP_MODEL)
 */
export async function swapOutfit(
  imageSrc: string,
  instruction: string,
  garmentRefUrl?: string,
  opts: EditOpts = {},
): Promise<EditResult> {
  console.error('[edit-pack] swapOutfit →', garmentRefUrl ? 'ai-dress-change' : 'nano-banana-edit');
  try {
    const imageUrl = await hostStillForMuapi(imageSrc);

    if (garmentRefUrl) {
      // ai-dress-change real schema: required [model_image_url, garment_image_url] — no prompt.
      const endpoint = opts.model || process.env.MUAPI_OUTFIT_SWAP_MODEL || 'ai-dress-change';
      const result = await muapiGenerate(endpoint, {
        model_image_url: imageUrl,
        garment_image_url: garmentRefUrl,
      });
      return {
        url: result.outputs?.[0] ?? undefined,
        model: `muapi:${endpoint}`,
        cost: result.cost ?? null,
      };
    }

    // No garment ref — describe the outfit change as a text edit via nano-banana-edit.
    const endpoint = opts.model || process.env.MUAPI_OUTFIT_SWAP_MODEL || 'nano-banana-edit';
    return await nanaBananaEdit(endpoint, imageUrl, `change the outfit: ${instruction}`);
  } catch (e) {
    console.error('[edit-pack] swapOutfit falló:', e);
    throw e;
  }
}

// ─── 7. Face Swap ─────────────────────────────────────────────────────────────

/**
 * Swap the face in imageSrc with the face from faceRefUrl (Higgsfield face-swap
 * via browser extension / Nano Banana 2).
 * Endpoint: ai-image-face-swap  (overridable via MUAPI_FACE_SWAP_MODEL)
 * Real schema: required [image_url, swap_url] — no prompt, no image_urls.
 */
export async function faceSwap(
  imageSrc: string,
  faceRefUrl: string,
  opts: EditOpts = {},
): Promise<EditResult> {
  const endpoint = opts.model || process.env.MUAPI_FACE_SWAP_MODEL || 'ai-image-face-swap';
  console.error('[edit-pack] faceSwap →', endpoint);
  try {
    const imageUrl = await hostStillForMuapi(imageSrc);
    const result = await muapiGenerate(endpoint, {
      image_url: imageUrl,
      swap_url: faceRefUrl,
    });
    return {
      url: result.outputs?.[0] ?? undefined,
      model: `muapi:${endpoint}`,
      cost: result.cost ?? null,
    };
  } catch (e) {
    console.error('[edit-pack] faceSwap falló:', e);
    throw e;
  }
}

// ─── 8. Headshot ──────────────────────────────────────────────────────────────

/**
 * Studio-quality headshot from a casual selfie (Higgsfield "AI Headshot
 * Generator"). Optionally pass a style instruction (e.g. "corporate white
 * background, navy suit").
 * Endpoint: portrait-stylist  (overridable via MUAPI_HEADSHOT_MODEL)
 * Real schema: required [image_url, name] — name = style/scene description.
 */
export async function headshot(
  imageSrc: string,
  opts: EditOpts & { instruction?: string } = {},
): Promise<EditResult> {
  const endpoint = opts.model || process.env.MUAPI_HEADSHOT_MODEL || 'portrait-stylist';
  const name = opts.instruction || 'professional studio headshot, clean background, soft key light';
  console.error('[edit-pack] headshot →', endpoint);
  try {
    const imageUrl = await hostStillForMuapi(imageSrc);
    const result = await muapiGenerate(endpoint, {
      image_url: imageUrl,
      name,
    });
    return {
      url: result.outputs?.[0] ?? undefined,
      model: `muapi:${endpoint}`,
      cost: result.cost ?? null,
    };
  } catch (e) {
    console.error('[edit-pack] headshot falló:', e);
    throw e;
  }
}

// ─── 9. Skin Enhance ──────────────────────────────────────────────────────────

/**
 * Natural skin retouching and detail reconstruction (Higgsfield "Skin Enhancer" /
 * GFPGAN/CodeFormer face-restore family).
 * Endpoint: ai-skin-enhancer  (overridable via MUAPI_SKIN_ENHANCE_MODEL)
 * Real schema: required [image_url] only.
 */
export async function skinEnhance(
  imageSrc: string,
  opts: EditOpts = {},
): Promise<EditResult> {
  const endpoint = opts.model || process.env.MUAPI_SKIN_ENHANCE_MODEL || 'ai-skin-enhancer';
  console.error('[edit-pack] skinEnhance →', endpoint);
  try {
    const imageUrl = await hostStillForMuapi(imageSrc);
    // ai-skin-enhancer real schema: required [image_url] only.
    return await imageUrlOnlyEdit(endpoint, imageUrl);
  } catch (e) {
    console.error('[edit-pack] skinEnhance falló:', e);
    throw e;
  }
}

// ─── 10. Object Erase ─────────────────────────────────────────────────────────

/**
 * Remove / erase an object described in instruction, filling with a
 * context-aware background (Higgsfield draw-to-edit / "AI Object Eraser").
 * Routes through nano-banana-edit (prompt + images_list).
 * Endpoint: nano-banana-edit  (overridable via MUAPI_OBJECT_ERASE_MODEL)
 */
export async function objectErase(
  imageSrc: string,
  instruction: string,
  opts: EditOpts = {},
): Promise<EditResult> {
  const endpoint = opts.model || process.env.MUAPI_OBJECT_ERASE_MODEL || 'nano-banana-edit';
  const prompt = 'remove the described object and fill the area naturally and seamlessly';
  console.error('[edit-pack] objectErase →', endpoint, '|', instruction);
  try {
    const imageUrl = await hostStillForMuapi(imageSrc);
    // images_list = [imageUrl] only; the instruction is encoded in the prompt field.
    return await nanaBananaEdit(endpoint, imageUrl, `${prompt} — ${instruction}`);
  } catch (e) {
    console.error('[edit-pack] objectErase falló:', e);
    throw e;
  }
}

// ─── 11. Style Transfer ───────────────────────────────────────────────────────

/**
 * Transfer the look of a styleRefUrl (or a named styleKey from IMAGE_STYLES)
 * onto imageSrc while preserving content and subject identity
 * (Higgsfield "Style Snap" / "Soul Reference").
 *
 * Both paths go through nano-banana-edit (prompt + images_list):
 * - With styleRefUrl → images_list = [imageUrl, styleRefUrl]; prompt = restyle clause.
 * - With styleKey only → images_list = [imageUrl]; prompt = catalog clause.
 *
 * Endpoint overridable via MUAPI_STYLE_TRANSFER_MODEL (ref) / MUAPI_STYLE_EDIT_MODEL (text).
 */
export async function styleTransfer(
  imageSrc: string,
  opts: EditOpts & { styleRefUrl?: string; styleKey?: string } = {},
): Promise<EditResult> {
  const { styleRefUrl, styleKey } = opts;

  if (styleRefUrl) {
    const endpoint = opts.model || process.env.MUAPI_STYLE_TRANSFER_MODEL || 'nano-banana-edit';
    const styleClause = styleKey
      ? (resolveStyleClause(styleKey) ?? `in the ${styleKey} style`)
      : '';
    const prompt = styleClause
      ? `restyle the first image in the visual style of the second image, ${styleClause}`
      : 'restyle the first image in the visual style of the second image';
    console.error('[edit-pack] styleTransfer (ref) →', endpoint);
    try {
      const imageUrl = await hostStillForMuapi(imageSrc);
      return await nanaBananaEdit(endpoint, imageUrl, prompt, [styleRefUrl]);
    } catch (e) {
      console.error('[edit-pack] styleTransfer falló:', e);
      throw e;
    }
  }

  // Text-only style transfer → nano-banana-edit
  const endpoint = opts.model || process.env.MUAPI_STYLE_EDIT_MODEL || 'nano-banana-edit';
  const clause = styleKey
    ? (resolveStyleClause(styleKey) ?? `in the ${styleKey} style`)
    : '';
  const prompt = clause
    ? `restyle the first image ${clause}`
    : 'restyle the image with a distinct new aesthetic while preserving subject identity';
  console.error('[edit-pack] styleTransfer (text) →', endpoint, { styleKey });
  try {
    const imageUrl = await hostStillForMuapi(imageSrc);
    return await nanaBananaEdit(endpoint, imageUrl, prompt);
  } catch (e) {
    console.error('[edit-pack] styleTransfer falló:', e);
    throw e;
  }
}

// ─── 12. Product Photo ────────────────────────────────────────────────────────

/**
 * Professional product photography: place the product from imageSrc into a new
 * studio or lifestyle context described in instruction (Higgsfield "Packshot" /
 * "Product Photography" / product-placement family).
 * Routes through nano-banana-edit (prompt + images_list).
 * Endpoint: nano-banana-edit  (overridable via MUAPI_PRODUCT_PHOTO_MODEL)
 */
export async function productPhoto(
  imageSrc: string,
  instruction: string,
  opts: EditOpts = {},
): Promise<EditResult> {
  const endpoint = opts.model || process.env.MUAPI_PRODUCT_PHOTO_MODEL || 'nano-banana-edit';
  const prompt = `Product photography: ${instruction}. Keep the product shape and branding pixel-perfect; only change the background, lighting and scene.`;
  console.error('[edit-pack] productPhoto →', endpoint);
  try {
    const imageUrl = await hostStillForMuapi(imageSrc);
    return await nanaBananaEdit(endpoint, imageUrl, prompt);
  } catch (e) {
    console.error('[edit-pack] productPhoto falló:', e);
    throw e;
  }
}

// ─── 13. Multi-Angle (Higgsfield "Angles 2.0") ────────────────────────────────

/** Default camera-angle descriptions used when opts.angles is not provided. */
const DEFAULT_ANGLES: readonly string[] = [
  '3/4 view from the left',
  '3/4 view from the right',
  'profile side view',
  'slight low angle',
  'slight high angle',
  'straight-on front',
] as const;

/**
 * Generate alternate camera angles of the SAME subject from one image
 * (Higgsfield "Angles 2.0" parity).
 *
 * For each angle a single nano-banana-edit call is made (prompt + images_list).
 * Failures per-angle are caught and skipped — the returned array contains only
 * successful results.
 *
 * @param imageSrc  Any source accepted by hostStillForMuapi (URL, dataUrl, file path).
 * @param opts.angles  Custom angle descriptions. If omitted, the first
 *                     (opts.count || 4) entries of DEFAULT_ANGLES are used.
 * @param opts.count   How many default angles to use (1–6). Ignored when
 *                     opts.angles is supplied. Defaults to 4.
 * @param opts.model   Override the Muapi endpoint.
 *                     Falls back to MUAPI_ANGLES_MODEL env var, then 'nano-banana-edit'.
 *
 * @returns Array of EditResult — one entry per successfully generated angle.
 */
export async function multiAngle(
  imageSrc: string,
  opts: EditOpts & { angles?: string[]; count?: number } = {},
): Promise<EditResult[]> {
  const endpoint = opts.model || process.env.MUAPI_ANGLES_MODEL || 'nano-banana-edit';

  const angles: string[] =
    opts.angles && opts.angles.length > 0
      ? opts.angles
      : DEFAULT_ANGLES.slice(0, Math.max(1, Math.min(opts.count ?? 4, DEFAULT_ANGLES.length)));

  console.error('[edit-pack] multiAngle →', endpoint, `(${angles.length} ángulos)`);

  // Resolve the image URL once, reuse across all angle calls.
  let imageUrl: string;
  try {
    imageUrl = await hostStillForMuapi(imageSrc);
  } catch (e) {
    console.error('[edit-pack] multiAngle: error al resolver imageSrc:', e);
    throw e;
  }

  const results = await Promise.allSettled(
    angles.map(async (angle) => {
      const instruction =
        `show the exact same subject and scene from a ${angle}, ` +
        'identical identity, lighting and styling, just a different camera angle';
      console.error('[edit-pack] multiAngle ángulo →', angle);
      // nano-banana-edit: required [prompt, images_list]
      return nanaBananaEdit(endpoint, imageUrl, instruction);
    }),
  );

  const successful: EditResult[] = [];
  for (let i = 0; i < results.length; i++) {
    const r = results[i];
    if (r.status === 'fulfilled') {
      successful.push(r.value);
    } else {
      console.error(`[edit-pack] multiAngle: ángulo "${angles[i]}" falló:`, r.reason);
    }
  }

  return successful;
}
