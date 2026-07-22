// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  brand-aesthetic — Soul HEX + Moodboard equivalents for FyDesign            ║
// ║                                                                              ║
// ║  Replicates Higgsfield's two core brand-fidelity moat features:             ║
// ║    • Soul HEX  — palette-lock directive injected into any prompt            ║
// ║    • Soul Moodboard — Opus-distilled style descriptor from ref-image set    ║
// ║                                                                              ║
// ║  Pure TypeScript / tsx; NO Next.js routes. TS strict.                       ║
// ║  Self-contained: all calls are best-effort with graceful try/catch.         ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { callAI } from './ai/deepseek-client';
import { loadRefInline } from './ai/brand-image';
import type { VideoBrandCtx } from './video/types';

// ─── Soul HEX equivalent ─────────────────────────────────────────────────────

/**
 * Append a strict color-palette directive to `prompt` (Soul HEX equivalent).
 *
 * Injects:
 *   "Color palette LOCKED to exactly: #XX, #YY; grade the whole image to these
 *    brand colors; do not introduce off-palette hues."
 *
 * If `colors` is empty, the original prompt is returned unchanged.
 *
 * @param prompt  The base generation prompt.
 * @param colors  HEX color codes, e.g. ['#1A1A2E', '#E94560']. Empty = no-op.
 * @returns       The conditioned prompt string.
 */
export function applyBrandColorLock(prompt: string, colors: string[]): string {
  if (!colors || colors.length === 0) return prompt;

  // Normalise: keep only values that look like hex codes (#RGB or #RRGGBB),
  // strip any extras to keep the directive concise.
  const valid = colors
    .map((c) => c.trim())
    .filter((c) => /^#[0-9A-Fa-f]{3,8}$/.test(c));

  if (valid.length === 0) return prompt;

  const list = valid.join(', ');
  const directive =
    `Color palette LOCKED to exactly: ${list}; ` +
    `grade the whole image to these brand colors; ` +
    `do not introduce off-palette hues.`;

  return `${prompt.trimEnd()} ${directive}`;
}

// ─── Soul Moodboard equivalent ────────────────────────────────────────────────

/** Maximum reference images fed to Opus for moodboard distillation (mirrors Higgsfield cap). */
const MAX_MOODBOARD_REFS = 8;

/**
 * Distill a set of reference images into a STRUCTURED style descriptor (Soul Moodboard).
 *
 * Loads up to {@link MAX_MOODBOARD_REFS} refs via {@link loadRefInline}, then calls
 * Opus (via callAI with opts.images) asking it to produce a concise descriptor
 * covering palette HEX, framing, lighting, mood, textures, and era — in ~5 lines.
 *
 * Fully best-effort: on any failure returns ''.
 *
 * @param refImages  Paths / http URLs / data URLs of reference images.
 * @param ctx        Brand context (used for the Opus system prompt to anchor the brand).
 * @returns          A ~5-line structured style descriptor, or '' on failure.
 */
export async function distillMoodboard(
  refImages: string[],
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  ctx: VideoBrandCtx | Record<string, any>,
): Promise<string> {
  if (!refImages || refImages.length === 0) return '';

  try {
    // Load inline images (best-effort; skip nulls)
    const sources = refImages.slice(0, MAX_MOODBOARD_REFS);
    const loaded = await Promise.all(sources.map((src) => loadRefInline(src).catch(() => null)));
    const images = loaded.filter(
      (im): im is { data: string; mimeType: string } => im !== null && !!im.data && !!im.mimeType,
    );

    if (images.length === 0) {
      console.error('[brand-aesthetic] distillMoodboard: ninguna imagen de referencia se cargó correctamente');
      return '';
    }

    const brandName = (ctx as VideoBrandCtx).name || 'la marca';

    const system =
      'Eres un director de arte experto en identidad visual de marcas. ' +
      'Analiza imágenes de referencia y extrae un descriptor de estilo ESTRUCTURADO ' +
      'y conciso que capture la esencia visual exacta del conjunto — no generalices. ' +
      'Responde SOLO con el descriptor, sin preámbulo, sin bloques de código.';

    const prompt =
      `Estas ${images.length} imágenes de referencia definen el moodboard visual de "${brandName}". ` +
      `Destila su estética en un descriptor ESTRUCTURADO de ~5 líneas concisas, cubriendo EXACTAMENTE:\n` +
      `1. PALETA HEX: los 3-6 colores dominantes como códigos HEX exactos.\n` +
      `2. ENCUADRE Y COMPOSICIÓN: tipo de plano, regla de tercios, espacio negativo, ángulo.\n` +
      `3. ILUMINACIÓN: temperatura, dirección, dureza, estilo (natural/estudio/cinematográfico).\n` +
      `4. ESTADO DE ÁNIMO Y ATMÓSFERA: una o dos palabras que definen el mood y el tono emocional.\n` +
      `5. TEXTURAS Y ERA: materiales, grano, acabado, referencia temporal/cultural si la hay.\n` +
      `Sé específico y usa adjetivos técnicos de fotografía/cinematografía. Devuelve SOLO el descriptor.`;

    const descriptor = await callAI(prompt, {
      system,
      images,
      maxTokens: 512,
    });

    const trimmed = (descriptor || '').trim();
    if (!trimmed) {
      console.error('[brand-aesthetic] distillMoodboard: Opus devolvió respuesta vacía');
      return '';
    }

    return trimmed;
  } catch (err) {
    console.error(
      '[brand-aesthetic] distillMoodboard: error al destilar moodboard —',
      err instanceof Error ? err.message : String(err),
    );
    return '';
  }
}

// ─── Moodboard clause helper ──────────────────────────────────────────────────

/**
 * Wrap a moodboard descriptor into a prompt clause (Soul Moodboard injection).
 *
 * Returns a "Match this established brand moodboard: ..." string suitable for
 * appending to any image or video generation prompt.
 *
 * If `descriptor` is empty or whitespace-only, returns ''.
 *
 * @param descriptor  The structured style descriptor produced by {@link distillMoodboard}.
 * @returns           A ready-to-append prompt clause, or '' if descriptor is empty.
 */
export function moodboardClause(descriptor: string): string {
  const d = (descriptor || '').trim();
  if (!d) return '';
  // Cap at ~400 chars so the clause doesn't blow the image-prompt budget.
  const capped = d.length > 400 ? d.slice(0, 400) + '…' : d;
  return `Match this established brand moodboard: ${capped}`;
}
