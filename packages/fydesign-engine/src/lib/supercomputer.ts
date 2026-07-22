// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  supercomputer — Higgsfield-class BATCH engine                               ║
// ║                                                                              ║
// ║  Opus expands one brief into N distinct on-brand concepts, then generates    ║
// ║  them ALL in parallel via a bounded worker pool.  Pure orchestration —       ║
// ║  no overlays, no assembly; just raw stills from generateBrandStill.          ║
// ║                                                                              ║
// ║  CONTRACT (from src/lib/video/types.ts):                                     ║
// ║    batchGenerate(ctx, brief, opts): Promise<Array<BatchItem>>                ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { callAIJSON } from './ai/deepseek-client';
import { generateBrandStill } from './ai/brand-image';
import type { VideoBrandCtx, VideoAspect } from './video/types';

// ─── Public types ─────────────────────────────────────────────────────────────

export interface BatchItem {
  dataUrl?: string;
  url?: string;
  concept: string;
  cost?: { amount_usd?: number } | null;
  model: string;
}

export interface BatchOpts {
  count: number;
  kind?: 'image' | 'post';
  quality?: 'ultra' | 'standard' | 'fast' | 'brand';
  aspect?: VideoAspect;
  concurrency?: number;
  references?: Array<{ data: string; mimeType: string }>;
  onProgress?: (done: number, total: number, label: string) => void;
}

// ─── Internal shape Opus returns ──────────────────────────────────────────────

interface ConceptEntry {
  concept: string;
  imagePrompt: string;
}

// ─── Async worker pool ────────────────────────────────────────────────────────

/**
 * Run `tasks` concurrently with at most `limit` workers at a time.
 * Never rejects — individual task rejections are surfaced as Error values.
 */
async function poolAll<T>(
  tasks: Array<() => Promise<T>>,
  limit: number,
): Promise<Array<T | Error>> {
  const results: Array<T | Error> = new Array(tasks.length);
  let nextIdx = 0;

  async function worker(): Promise<void> {
    while (nextIdx < tasks.length) {
      const idx = nextIdx++;
      try {
        results[idx] = await tasks[idx]();
      } catch (e) {
        results[idx] = e instanceof Error ? e : new Error(String(e));
      }
    }
  }

  const workerCount = Math.min(limit, tasks.length);
  const workers: Promise<void>[] = [];
  for (let i = 0; i < workerCount; i++) workers.push(worker());
  await Promise.all(workers);
  return results;
}

// ─── Concept expansion via Opus ───────────────────────────────────────────────

async function expandBrief(
  ctx: VideoBrandCtx,
  brief: string,
  count: number,
): Promise<ConceptEntry[]> {
  const system = `You are an elite visual creative director expanding a marketing brief into a diverse set of on-brand still-image concepts.

RULES — NON-NEGOTIABLE:
- Return a JSON ARRAY of exactly ${count} objects: { "concept": string, "imagePrompt": string }
- "concept": a SHORT label (3–6 words, in the brief's language) naming the distinct idea.
- "imagePrompt": a VIVID, TEXT-FREE, UI-FREE English description of a photographic/cinematic still. ZERO readable text, words, numbers, signage, logos, app screens, phone screens, or UI elements. Describe ONLY people, products, environment, light, mood, composition. Leave calm negative space. If a device appears its screen is blank/off.
- VARIETY: different angles, scenes, moods, lighting conditions, subject distances — avoid repetition.
- ON-BRAND: weave the brand palette (${ctx.brandColors}) and overall vibe naturally into each scene.
- ABSOLUTELY NO INVENTED FACTS: no statistics, prices, follower counts, awards, or testimonials.
- Output ONLY valid JSON array — no markdown, no commentary.`;

  const prompt = `Brand: ${ctx.name}
Colors: ${ctx.brandColors}
Fonts: ${ctx.fonts}
Brand info: ${ctx.info}
${ctx.screens ? `Visual screens/assets: ${ctx.screens}` : ''}

Brief: ${brief}

Expand this brief into ${count} DISTINCT visual concepts as a JSON array of { concept, imagePrompt }.`;

  const raw = await callAIJSON<ConceptEntry[] | { concepts?: ConceptEntry[] }>(prompt, {
    system,
    maxTokens: 4096,
  });

  // Opus sometimes wraps the array in an object key
  let entries: ConceptEntry[] = [];
  if (Array.isArray(raw)) {
    entries = raw as ConceptEntry[];
  } else if (raw && typeof raw === 'object' && Array.isArray((raw as { concepts?: ConceptEntry[] }).concepts)) {
    entries = (raw as { concepts: ConceptEntry[] }).concepts;
  }

  // Filter to valid entries
  entries = entries.filter(
    (e): e is ConceptEntry =>
      e !== null &&
      typeof e === 'object' &&
      typeof e.concept === 'string' &&
      typeof e.imagePrompt === 'string' &&
      e.concept.trim().length > 0 &&
      e.imagePrompt.trim().length > 0,
  );

  if (entries.length === 0) {
    console.error('[supercomputer] Opus returned no valid concepts — using fallback');
    entries = [{ concept: brief.slice(0, 60), imagePrompt: brief }];
  }

  // Pad if Opus returned fewer than requested
  if (entries.length < count) {
    console.error(`[supercomputer] Opus devolvió ${entries.length} conceptos de ${count} solicitados — completando con variaciones`);
    const base = entries[entries.length - 1];
    while (entries.length < count) {
      const n = entries.length + 1;
      entries.push({
        concept: `${base.concept} (variación ${n})`,
        imagePrompt: `${base.imagePrompt}, different angle, different lighting mood, variation ${n}`,
      });
    }
  }

  // Slice if Opus over-delivered
  if (entries.length > count) entries = entries.slice(0, count);

  return entries;
}

// ─── Main export ──────────────────────────────────────────────────────────────

/**
 * Expand one brief into `count` distinct on-brand concepts (via Opus) and generate
 * them all in parallel with a worker pool of size `concurrency` (default 4).
 *
 * Individual generation failures are silently skipped (logged to stderr) — the
 * overall batch never rejects.
 *
 * `kind` is reserved for future 'post' support; currently everything is treated as
 * a raw 'image' (no overlays applied here).
 */
export async function batchGenerate(
  ctx: VideoBrandCtx,
  brief: string,
  opts: BatchOpts,
): Promise<BatchItem[]> {
  const {
    count,
    // kind is reserved — treat everything as raw image for now
    quality = 'standard',
    aspect = '1:1',
    concurrency = 4,
    references,
    onProgress,
  } = opts;

  console.error(`[supercomputer] Iniciando batch: ${count} conceptos, concurrencia=${concurrency}, calidad=${quality}`);

  // Step 1 — Opus expands the brief into N distinct concepts
  let concepts: ConceptEntry[];
  try {
    concepts = await expandBrief(ctx, brief, count);
  } catch (e) {
    console.error('[supercomputer] Error expandiendo brief con Opus:', e instanceof Error ? e.message : e);
    // Graceful fallback: generate count copies of the raw brief
    concepts = Array.from({ length: count }, (_, i) => ({
      concept: `Concepto ${i + 1}`,
      imagePrompt: brief,
    }));
  }

  console.error(`[supercomputer] ${concepts.length} conceptos listos — generando imágenes en paralelo`);

  // Step 2 — Generate in parallel with bounded concurrency
  let done = 0;
  const total = concepts.length;

  const tasks = concepts.map((entry) => async (): Promise<BatchItem> => {
    const { concept, imagePrompt } = entry;
    const result = await generateBrandStill(imagePrompt, {
      quality,
      aspect,
      references,
    });
    done++;
    onProgress?.(done, total, concept);
    console.error(`[supercomputer] ✓ ${done}/${total} — "${concept}"`);
    return {
      dataUrl: result.dataUrl,
      concept,
      cost: null,
      model: result.model,
    };
  });

  const settled = await poolAll(tasks, Math.max(1, concurrency));

  // Collect successes, log failures
  const items: BatchItem[] = [];
  for (let i = 0; i < settled.length; i++) {
    const r = settled[i];
    if (r instanceof Error) {
      console.error(`[supercomputer] Fallo en concepto "${concepts[i].concept}":`, r.message);
      // Skip — do not include failed items in the output
    } else {
      items.push(r as BatchItem);
    }
  }

  console.error(`[supercomputer] Batch completo: ${items.length}/${total} generados exitosamente`);
  return items;
}
