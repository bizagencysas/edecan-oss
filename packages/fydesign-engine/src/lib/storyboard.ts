// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  storyboard — Higgsfield "Popcorn" storyboard generator                    ║
// ║                                                                            ║
// ║  Opus breaks a brief into N cinematic frame descriptions, then renders     ║
// ║  each frame as an on-brand, text-free still via generateBrandStill.        ║
// ║  A shared style-anchor sentence (derived from the first frame's prompt)    ║
// ║  is prepended to every subsequent frame for visual consistency.             ║
// ║                                                                            ║
// ║  Higgsfield Popcorn equivalent: Auto Mode → up to 8 frames (default 6)    ║
// ║  or caller-specified up to MAX_FRAMES (12). Aspect ratios 16:9, 9:16,     ║
// ║  1:1, 3:4, 4:3 (passed through to generateBrandStill).                    ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { callAIJSON } from './ai/deepseek-client';
import { generateBrandStill } from './ai/brand-image';
import type { VideoBrandCtx, VideoAspect } from './video/types';

// ─── Constants ───────────────────────────────────────────────────────────────

const DEFAULT_FRAMES = 6;
const MAX_FRAMES = 12;

// ─── Internal types ───────────────────────────────────────────────────────────

interface FrameSpec {
  /** Short narrative caption in the brief's language (shown to the viewer). */
  caption: string;
  /**
   * TEXT-FREE, UI-FREE English still description — fed to generateBrandStill.
   * Must not mention any UI elements, text overlays, logos, or apps.
   */
  imagePrompt: string;
}

interface StoryboardPlan {
  /** One-sentence cinematic concept for the sequence. */
  concept: string;
  frames: FrameSpec[];
}

// ─── Public API ───────────────────────────────────────────────────────────────

export interface StoryboardFrame {
  caption: string;
  dataUrl: string;
}

export interface StoryboardResult {
  concept: string;
  frames: StoryboardFrame[];
}

/**
 * Higgsfield Popcorn equivalent — generate a multi-frame cinematic storyboard.
 *
 * 1. Opus breaks `brief` into `frames` (default 6, max 12) cinematic frame
 *    descriptions: { caption (brief's language), imagePrompt (TEXT-FREE English) }.
 * 2. Each imagePrompt is rendered via generateBrandStill with a shared style-anchor
 *    sentence prepended for visual consistency.
 * 3. Returns { concept, frames: Array<{ caption, dataUrl }> }.
 *    Per-frame failures are tolerated (skipped, not fatal).
 */
export async function generateStoryboard(
  ctx: VideoBrandCtx,
  brief: string,
  opts: {
    frames?: number;
    aspect?: VideoAspect;
  } = {},
): Promise<StoryboardResult> {
  const frameCount = Math.min(
    Math.max(1, opts.frames ?? DEFAULT_FRAMES),
    MAX_FRAMES,
  );
  const aspect: VideoAspect = opts.aspect ?? '16:9';

  // ── Step 1: Opus generates the storyboard plan ──────────────────────────────
  console.error('[Storyboard] Generando plan de storyboard con Opus…');

  const brandColorHints = ctx.colors.length > 0
    ? `Paleta de colores de la marca: ${ctx.colors.slice(0, 6).join(', ')}.`
    : '';

  const planningPrompt = `
Eres un director cinematográfico de primer nivel. Genera un storyboard de ${frameCount} frames para la siguiente propuesta creativa.

BRIEF:
${brief}

MARCA: ${ctx.name}
${brandColorHints}
${ctx.info ? `CONTEXTO DE MARCA: ${ctx.info}` : ''}

INSTRUCCIONES ESTRICTAS:
- Devuelve un JSON con exactamente esta forma:
  {
    "concept": "<una frase: el concepto visual/narrativo de la secuencia>",
    "frames": [
      {
        "caption": "<caption breve en el idioma del brief>",
        "imagePrompt": "<descripción en INGLÉS de un still fotográfico, SIN texto, SIN UI, SIN logos, SIN apps>"
      }
    ]
  }
- El "concept" es una sola oración que describe el arco narrativo visual.
- Cada "caption" va en el idioma del brief (puede ser español u otro idioma).
- Cada "imagePrompt" DEBE estar en inglés, describir solo la escena visual, sin texto, sin interfaces, sin logotipos, sin mencionar ninguna app o pantalla.
- Los frames deben formar una secuencia coherente con inicio, desarrollo y cierre.
- Honra los colores de la marca: sugiere iluminación y paleta que evoquen esos colores.
- No inventes datos, nombres de personas reales, ni lugares específicos sin confirmar.
- Genera exactamente ${frameCount} frames.
`.trim();

  const plan = await callAIJSON<StoryboardPlan>(planningPrompt, {
    maxTokens: 4096,
  });

  if (!plan || !Array.isArray(plan.frames) || plan.frames.length === 0) {
    console.error('[Storyboard] Error: Opus no devolvió un plan válido');
    return { concept: '', frames: [] };
  }

  const concept = (plan.concept || '').trim();
  const specs: FrameSpec[] = plan.frames.slice(0, MAX_FRAMES).filter(
    (f) => f && typeof f.imagePrompt === 'string' && f.imagePrompt.trim().length > 0,
  );

  console.error(`[Storyboard] Plan generado — concepto: "${concept}" — ${specs.length} frames`);

  // ── Step 2: Derive a shared style-anchor from the first frame ──────────────
  // The style anchor is a short sentence describing the overall visual look
  // (lighting, color grade, lens feel, mood) consistent across all frames.
  // We extract it from the first frame's imagePrompt rather than inventing it,
  // so it is grounded in Opus's intent.
  let styleAnchor = '';
  if (specs.length > 0) {
    const firstPrompt = specs[0].imagePrompt.trim();
    // Take up to the first sentence (period / semicolon) as the style anchor,
    // or the whole prompt if short enough. Cap at 200 chars to stay tight.
    const firstSentenceMatch = firstPrompt.match(/^[^.;!?]{10,200}[.;!?]?/);
    const candidate = firstSentenceMatch ? firstSentenceMatch[0].trim() : firstPrompt.slice(0, 200).trim();
    // Build a style-anchor phrase focused on look & feel, not subject content.
    styleAnchor = `Cinematic visual style: ${candidate}.`;
    console.error(`[Storyboard] Anchor de estilo: "${styleAnchor.slice(0, 80)}…"`);
  }

  // ── Step 3: Render each frame ──────────────────────────────────────────────
  console.error(`[Storyboard] Renderizando ${specs.length} frames en paralelo…`);

  const frameResults = await Promise.allSettled(
    specs.map(async (spec, idx): Promise<StoryboardFrame> => {
      // Prepend the style anchor to every prompt (including frame 0, redundantly harmless)
      const fullPrompt = styleAnchor
        ? `${styleAnchor} ${spec.imagePrompt.trim()}`
        : spec.imagePrompt.trim();

      console.error(`[Storyboard] Frame ${idx + 1}/${specs.length} — renderizando…`);

      const still = await generateBrandStill(fullPrompt, {
        quality: 'standard',
        aspect,
      });

      console.error(`[Storyboard] Frame ${idx + 1} OK — modelo: ${still.model}`);

      return {
        caption: (spec.caption || `Frame ${idx + 1}`).trim(),
        dataUrl: still.dataUrl,
      };
    }),
  );

  // Collect successful frames in original order; skip failures gracefully.
  const frames: StoryboardFrame[] = [];
  for (let i = 0; i < frameResults.length; i++) {
    const result = frameResults[i];
    if (result.status === 'fulfilled') {
      frames.push(result.value);
    } else {
      console.error(
        `[Storyboard] Frame ${i + 1} falló (omitido):`,
        result.reason instanceof Error ? result.reason.message : result.reason,
      );
    }
  }

  console.error(`[Storyboard] Completado — ${frames.length}/${specs.length} frames generados`);

  return { concept, frames };
}
