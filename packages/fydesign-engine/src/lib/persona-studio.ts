// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  persona-studio.ts — AI-Influencer power workflows (Higgsfield parity)       ║
// ║                                                                              ║
// ║  Implements Photodump Studio, Instadump, FyID LoRA training, and the         ║
// ║  Brand Ambassador content-series workflow on top of the existing persona      ║
// ║  and brand-image engines.                                                    ║
// ║                                                                              ║
// ║  All four exports are designed to NEVER crash the whole call on a single     ║
// ║  image failure: each individual generation is wrapped in try/catch and       ║
// ║  failures are logged to stderr with a [persona-studio] prefix.               ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

// eslint-disable-next-line @typescript-eslint/no-explicit-any
import { listPersonas, loadPersona, generatePersonaImage } from './persona';
import { batchGenerate } from './supercomputer';
import { loadRefInline, generateBrandStill } from './ai/brand-image';
import { callAIJSON } from './ai/deepseek-client';
import { muapiGenerate } from './ai/muapi-client';
import { uploadToGCS, getBucket, generateGcsPath } from './gcs';
import JSZip from 'jszip';
import type { Persona, VideoBrandCtx } from './video/types';

// ─── Presets catalog types ────────────────────────────────────────────────────
// The catalog is a parallel build; import defensively so persona-studio compiles
// even when catalog.ts does not exist yet.

interface TrendPack {
  key: string;
  label: string;
  /** English image-generation prompt fragment describing the trend look/feel. */
  prompt: string;
}

// Attempt a dynamic import of the catalog; fall back to a built-in minimal set.
async function getTrendPacks(): Promise<TrendPack[]> {
  try {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const mod = await import('./presets/catalog') as any;
    const packs = mod.TREND_PACKS as TrendPack[] | undefined;
    if (Array.isArray(packs) && packs.length > 0) return packs;
  } catch {
    // catalog not yet built — use fallback list
  }
  // Fallback trend-pack catalog drawn from the Higgsfield blueprint.
  return [
    { key: 'mystique-city',        label: 'Mystique City',         prompt: 'moody urban night, neon reflections, editorial street style, cinematic bokeh' },
    { key: 'warm-ambient',         label: 'Warm Ambient',          prompt: 'golden hour warm light, cozy aesthetic, soft shadows, lifestyle photography' },
    { key: 'editorial-street',     label: 'Editorial Street Style', prompt: 'editorial street fashion, urban backdrop, natural light, magazine quality' },
    { key: 'subtle-flash',         label: 'Subtle Flash',          prompt: 'low light, soft on-camera flash, candid night photography, grain texture' },
    { key: 'old-smartphone',       label: 'Old Smartphone',        prompt: '2000s camera phone aesthetic, low resolution grain, nostalgic, overexposed highlights' },
    { key: 'frutiger-aero',        label: 'Frutiger Aero',         prompt: 'Y2K tech optimism, iridescent chrome, sky-blue gradients, glossy digital aesthetic' },
    { key: 'swag-era',             label: 'Swag Era',              prompt: '2010s hip-hop inspired fashion, bold streetwear, vibrant color blocking' },
    { key: 'y2k-outside',          label: 'Y2K Outside',           prompt: 'early 2000s outdoor candid, disposable camera look, lens flare, sun-kissed' },
    { key: 'nature-light',         label: 'Nature Light',          prompt: 'dappled sunlight through leaves, forest or garden setting, soft natural bokeh' },
    { key: 'theatrical-light',     label: 'Theatrical Light',      prompt: 'dramatic stage lighting, high contrast, vivid color gels, performer energy' },
    { key: 'siren',                label: 'Siren',                 prompt: 'femme fatale editorial, deep jewel tones, glamorous mystery, luxury fashion' },
    { key: 'candy-pop',            label: 'Candy Pop',             prompt: 'pastel confection colors, playful and sweet, studio white backdrop, fun fashion' },
    { key: 'flash-editorial',      label: 'Flash Editorial',       prompt: 'high fashion editorial, stark flash photography, bold makeup, magazine spread' },
    { key: 'cozy-minimalist',      label: 'Cozy Minimalist',       prompt: 'neutral tones, clean bedroom or cafe interior, soft morning light, calm aesthetic' },
    { key: 'grunge',               label: 'Grunge',                prompt: '90s grunge aesthetic, distressed textures, muted earth tones, raw energy' },
    { key: 'quiet-luxury',         label: 'Quiet Luxury',          prompt: 'understated luxury, neutral palette, fine fabrics, subtle brand confidence' },
    { key: 'gorpcore',             label: 'Gorpcore',              prompt: 'outdoorsy technical wear, earth tones, mountain or trail setting, utilitarian chic' },
    { key: 'coquette',             label: 'Coquette Core',         prompt: 'feminine bows and lace, ballet pink palette, soft focus, romantic fantasy' },
    { key: 'amalfi-summer',        label: 'Amalfi Summer',         prompt: 'Mediterranean summer, white and cobalt blue, sun-drenched coastline, linen fashion' },
    { key: '90s-grain',            label: '90s Grain',             prompt: 'film grain heavy, 1990s color palette, casual lifestyle, lomography aesthetic' },
    { key: 'night-beach',          label: 'Night Beach',           prompt: 'beach at night, bonfires, golden light on sand, relaxed mood' },
    { key: 'tumblr',               label: 'Tumblr',                prompt: 'early Tumblr indie aesthetic, desaturated colors, vintage filter, quirky personality' },
    { key: 'indie-sleaze',         label: 'Indie Sleaze',          prompt: '2000s indie scene, flash photography, lived-in style, alternative culture' },
    { key: '0-5-selfie',           label: '0.5 Selfie',            prompt: 'ultra-wide front camera selfie, slight distortion, candid natural expression' },
  ];
}

async function applyStyleFromCatalog(
  key: string,
  packs: TrendPack[],
): Promise<string | null> {
  try {
    // Import IMAGE_STYLES and look up the style clause directly.
    // NOTE: applyStyle(prompt, key) requires TWO arguments; calling it with one
    // argument silently puts the key in the prompt position and applies nothing.
    // We read the style clause directly from IMAGE_STYLES instead.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const mod = await import('./presets/catalog') as any;
    const imageStyles = mod.IMAGE_STYLES as Array<{ key: string; label: string; prompt: string }> | undefined;
    if (Array.isArray(imageStyles)) {
      const q = key.toLowerCase().trim();
      const style = imageStyles.find((s) => s.key.toLowerCase() === q || s.label.toLowerCase() === q);
      if (style) return style.prompt;
    }
  } catch {
    // catalog not yet available
  }
  // Fallback: look up the prompt from our built-in list
  const pack = packs.find((p) => p.key === key);
  return pack ? pack.prompt : null;
}

// ─── Internal helpers ─────────────────────────────────────────────────────────

/** Clamp count to [min, max]. */
function clamp(n: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, n));
}

/**
 * Run up to `concurrency` async tasks at a time; collect results (errors included
 * as null so callers can filter).
 */
async function poolSettled<T>(
  tasks: Array<() => Promise<T>>,
  concurrency: number,
): Promise<Array<T | null>> {
  const results: Array<T | null> = new Array(tasks.length).fill(null);
  let nextIdx = 0;

  async function worker(): Promise<void> {
    while (nextIdx < tasks.length) {
      const idx = nextIdx++;
      try {
        results[idx] = await tasks[idx]();
      } catch (e) {
        console.error(
          `[persona-studio] Fallo en tarea ${idx}:`,
          e instanceof Error ? e.message : e,
        );
        results[idx] = null;
      }
    }
  }

  const pool = Math.min(concurrency, tasks.length);
  const workers: Promise<void>[] = [];
  for (let i = 0; i < pool; i++) workers.push(worker());
  await Promise.all(workers);
  return results;
}

// ─── Exported function shapes ─────────────────────────────────────────────────

// Re-export Persona so callers who import from persona-studio don't need two imports.
export type { Persona };

// Silence unused-import lint: the imports are used but TS can't see through the
// dynamic-import-based fallback. Explicit references below ensure tree-shaking is correct.
void listPersonas;
void loadPersona;
void batchGenerate;

// ─── 1. photodump ─────────────────────────────────────────────────────────────

/**
 * Higgsfield Photodump Studio equivalent.
 *
 * Opus expands the persona into `count` (default 18, max 26) DISTINCT
 * scene/outfit/mood TEXT-FREE prompts spanning different settings (golden hour,
 * candid, urban, cozy, editorial, etc.) — all anchored to the SAME identity.
 * Each prompt is then sent to generatePersonaImage (which injects persona refs
 * for consistency). Returns all data URLs that succeeded.
 *
 * @param persona - the persona to photograph
 * @param ctx     - brand context (colors, fonts, vibe)
 * @param opts    - { count?: number } — 18 by default, capped at 26
 */
export async function photodump(
  persona: Persona,
  ctx: VideoBrandCtx,
  opts: { count?: number } = {},
): Promise<string[]> {
  const count = clamp(opts.count ?? 18, 1, 26);

  console.error(
    `[persona-studio] Photodump: generando ${count} escenas para "${persona.name}"…`,
  );

  // Step 1 — Opus expands the persona into N distinct scene/mood/outfit prompts.
  interface SceneEntry {
    scene: string;
    imagePrompt: string;
  }

  const opusSystem = `You are a world-class editorial photo director planning a "photo dump" for a social media AI influencer.

RULES — NON-NEGOTIABLE:
- Return a JSON ARRAY of exactly ${count} objects: { "scene": string, "imagePrompt": string }
- "scene": a SHORT label (2–5 words) naming the setting/vibe (e.g. "golden hour rooftop", "cozy bookstore").
- "imagePrompt": a VIVID, COMPLETELY TEXT-FREE English description of the photographic scene. ZERO readable text, words, numbers, signage, logos, UI, or app screens. Describe ONLY the person, clothing, pose, environment, light, mood, composition. No invented stats or claims.
- Each scene MUST be distinct: different settings, outfits, lighting moods, poses, camera distances — avoid ANY repetition.
- Include the full range: golden hour outdoors, urban candid, cozy interior, editorial studio, beach/nature, nightlife, sport/action, travel, mirror selfie, cafe, etc.
- The character's core identity (face, skin tone, hair, body, personality vibe) must remain THE SAME across all scenes — only wardrobe, setting, and mood change.
- Output ONLY valid JSON array — no markdown, no commentary.`;

  const opusPrompt = `Persona: ${persona.name}
Description: ${persona.description}
Brand: ${ctx.name} | Colors: ${ctx.brandColors} | Vibe: ${ctx.info}

Generate ${count} DISTINCT photo dump scenes as a JSON array of { scene, imagePrompt }.`;

  let scenes: SceneEntry[] = [];
  try {
    const raw = await callAIJSON<SceneEntry[] | { scenes?: SceneEntry[] }>(opusPrompt, {
      system: opusSystem,
      maxTokens: 6000,
    });

    if (Array.isArray(raw)) {
      scenes = raw as SceneEntry[];
    } else if (raw && typeof raw === 'object') {
      const obj = raw as { scenes?: SceneEntry[] };
      if (Array.isArray(obj.scenes)) scenes = obj.scenes;
    }

    // Filter invalid entries
    scenes = scenes.filter(
      (s): s is SceneEntry =>
        s !== null &&
        typeof s === 'object' &&
        typeof s.scene === 'string' &&
        typeof s.imagePrompt === 'string' &&
        s.scene.trim().length > 0 &&
        s.imagePrompt.trim().length > 0,
    );
  } catch (e) {
    console.error('[persona-studio] Error al expandir escenas con Opus:', e instanceof Error ? e.message : e);
  }

  // Fallback scenes if Opus failed or returned too few
  if (scenes.length < count) {
    console.error(
      `[persona-studio] Opus devolvió ${scenes.length}/${count} escenas — completando con variaciones`,
    );
    const fallbackScenes: Array<{ scene: string; imagePrompt: string }> = [
      { scene: 'golden hour outdoors', imagePrompt: `${persona.description}, warm golden hour light, outdoor urban setting, relaxed confident pose, bokeh background, photorealistic portrait` },
      { scene: 'urban candid', imagePrompt: `${persona.description}, candid street photography, natural midday light, modern city backdrop, lifestyle editorial` },
      { scene: 'cozy cafe interior', imagePrompt: `${persona.description}, cozy cafe, warm ambient light, wooden table, relaxed seated pose, comfortable everyday style` },
      { scene: 'editorial studio white', imagePrompt: `${persona.description}, clean white studio backdrop, dramatic directional light, editorial fashion shoot, confident full-body pose` },
      { scene: 'rooftop night', imagePrompt: `${persona.description}, rooftop at night, city lights bokeh, soft string lights, elevated urban mood, evening outfit` },
      { scene: 'nature morning light', imagePrompt: `${persona.description}, lush green park or forest, soft morning light through leaves, fresh natural energy, casual style` },
      { scene: 'mirror selfie', imagePrompt: `${persona.description}, full-length mirror selfie perspective, stylish interior, outfit focus, casual natural expression` },
      { scene: 'beach golden', imagePrompt: `${persona.description}, golden beach at sunset, warm sand tones, relaxed mood, travel lifestyle, natural beauty` },
      { scene: 'gym/sport', imagePrompt: `${persona.description}, athletic wear, gym or outdoor workout setting, dynamic active pose, strong confident energy` },
      { scene: 'luxury interior', imagePrompt: `${persona.description}, high-end interior, marble and natural light, understated luxury fashion, calm sophisticated mood` },
      { scene: 'travel airport', imagePrompt: `${persona.description}, modern airport terminal, travel outfit, moving with purpose, lifestyle travel photography` },
      { scene: 'bookstore', imagePrompt: `${persona.description}, independent bookstore, warm shelf lighting, casual intellectual mood, comfortable style` },
      { scene: 'poolside', imagePrompt: `${persona.description}, poolside luxury, turquoise water reflection, summer vacation, relaxed resort style` },
      { scene: 'rainy window', imagePrompt: `${persona.description}, looking through rain-streaked window, soft grey outdoor light, contemplative mood, cozy indoor setting` },
      { scene: 'market street', imagePrompt: `${persona.description}, colorful outdoor market, vibrant produce or flowers, natural candid light, travel lifestyle mood` },
      { scene: 'rooftop day', imagePrompt: `${persona.description}, bright daytime rooftop, skyline view, breezy outdoor fashion, energetic confident mood` },
      { scene: 'hotel lobby', imagePrompt: `${persona.description}, chic hotel lobby, dramatic architecture, polished editorial style, soft overhead lighting` },
      { scene: 'friends table', imagePrompt: `${persona.description}, social dining setting, warm restaurant light, laughter and energy, lifestyle candid` },
      { scene: 'car window', imagePrompt: `${persona.description}, looking out of car window, moving city lights, travel mood, cinematic framing` },
      { scene: 'morning home', imagePrompt: `${persona.description}, bright airy home, morning sun streaming in, cozy wake-up lifestyle, casual comfortable style` },
      { scene: 'concert crowd', imagePrompt: `${persona.description}, live music event crowd energy, stage lights backdrop, vibrant nightlife, excited expression` },
      { scene: 'art gallery', imagePrompt: `${persona.description}, contemporary art gallery, clean white walls, artistic sophisticated energy, gallery opening style` },
      { scene: 'farmer market', imagePrompt: `${persona.description}, weekend farmer's market, fresh flowers, dappled sunlight, healthy lifestyle mood` },
      { scene: 'yacht or boat', imagePrompt: `${persona.description}, yacht or sailing boat deck, ocean horizon, summer nautical style, freedom and luxury` },
      { scene: 'mountain hike', imagePrompt: `${persona.description}, mountain trail with scenic vista, outdoor adventure gear, golden late-afternoon alpine light` },
      { scene: 'rooftop sunset', imagePrompt: `${persona.description}, rooftop at magic hour, sky ablaze with color, silhouette or warm backlit portrait, cinematic mood` },
    ];
    while (scenes.length < count) {
      const fallback = fallbackScenes[scenes.length % fallbackScenes.length];
      scenes.push({ scene: fallback.scene, imagePrompt: fallback.imagePrompt });
    }
  }

  // Trim to exact count
  scenes = scenes.slice(0, count);

  // Step 2 — Generate each scene in parallel (concurrency 4 to avoid rate limits)
  const tasks = scenes.map((entry) => async (): Promise<string | null> => {
    try {
      const urls = await generatePersonaImage(persona, ctx, entry.imagePrompt, { count: 1 });
      const url = urls[0] ?? null;
      if (url) {
        console.error(`[persona-studio] Photodump ✓ "${entry.scene}"`);
      }
      return url;
    } catch (e) {
      console.error(
        `[persona-studio] Photodump fallo en escena "${entry.scene}":`,
        e instanceof Error ? e.message : e,
      );
      return null;
    }
  });

  const settled = await poolSettled(tasks, 4);
  const results = settled.filter((r): r is string => typeof r === 'string' && r.length > 0);

  console.error(
    `[persona-studio] Photodump completo: ${results.length}/${count} imágenes generadas`,
  );
  return results;
}

// ─── 2. instadump ─────────────────────────────────────────────────────────────

/**
 * Higgsfield Instadump equivalent.
 *
 * For each chosen trend pack (from TREND_PACKS; defaults to first `count` or 12),
 * builds an identity-preserving prompt and generates a brand still using the
 * portrait as a reference image.
 *
 * @param portraitSrc - data URL, file path, or http URL of the source portrait
 * @param ctx         - brand context
 * @param opts        - { trendKeys?: string[]; count?: number }
 */
export async function instadump(
  portraitSrc: string,
  ctx: VideoBrandCtx,
  opts: { trendKeys?: string[]; count?: number } = {},
): Promise<string[]> {
  const allPacks = await getTrendPacks();
  const wantCount = clamp(opts.count ?? 12, 1, allPacks.length);

  // Select trend packs
  let selectedPacks: TrendPack[];
  if (opts.trendKeys && opts.trendKeys.length > 0) {
    selectedPacks = opts.trendKeys
      .map((k) => allPacks.find((p) => p.key === k))
      .filter((p): p is TrendPack => p !== undefined)
      .slice(0, wantCount);
    // Pad if fewer keys matched than requested
    if (selectedPacks.length < wantCount) {
      const remaining = allPacks.filter((p) => !selectedPacks.includes(p));
      selectedPacks = [...selectedPacks, ...remaining.slice(0, wantCount - selectedPacks.length)];
    }
  } else {
    selectedPacks = allPacks.slice(0, wantCount);
  }

  console.error(
    `[persona-studio] Instadump: ${selectedPacks.length} trend packs, cargando retrato…`,
  );

  // Load the portrait as a reference once
  const portrait = await loadRefInline(portraitSrc);
  if (!portrait) {
    console.error('[persona-studio] Instadump: no se pudo cargar el retrato fuente — abortando');
    return [];
  }

  // Generate each trend in parallel (concurrency 3 for API safety)
  const tasks = selectedPacks.map((pack) => async (): Promise<string | null> => {
    try {
      // Resolve trend prompt (from catalog's applyStyle if available, else pack.prompt)
      const trendPromptFragment =
        (await applyStyleFromCatalog(pack.key, allPacks)) ?? pack.prompt;

      const fullPrompt =
        `${trendPromptFragment}. ` +
        `Keep the SAME person identity as the reference — identical facial features, skin tone, hair structure, and overall character. ` +
        `Photo-realistic, professional photography, NO readable text, NO UI elements.`;

      const result = await generateBrandStill(fullPrompt, {
        quality: 'brand',
        references: [portrait],
      });

      console.error(`[persona-studio] Instadump ✓ "${pack.label}"`);
      return result.dataUrl;
    } catch (e) {
      console.error(
        `[persona-studio] Instadump fallo en trend "${pack.label}":`,
        e instanceof Error ? e.message : e,
      );
      return null;
    }
  });

  const settled = await poolSettled(tasks, 3);
  const results = settled.filter((r): r is string => typeof r === 'string' && r.length > 0);

  console.error(
    `[persona-studio] Instadump completo: ${results.length}/${selectedPacks.length} imágenes`,
  );
  return results;
}

// ─── 3. trainFyID ─────────────────────────────────────────────────────────────

/**
 * FyID LoRA identity training (Soul-ID equivalent).
 *
 * Packs the reference images into a .zip, uploads to GCS, obtains a v4 signed
 * read URL (24 h), then submits to flux-lora-trainer via Muapi.
 *
 * flux-lora-trainer schema (verified 2026-06-14):
 *   required: images_data_url — public URL to a .zip of 10-50 training images
 *   optional: trigger_phrase  — unique word/phrase that activates the concept
 *             training_style  — 'subject' | 'style' (default 'subject')
 *   output:  lora_url         — hosted .safetensors (normalised to outputs[0])
 *
 * Escape hatch: set MUAPI_LORA_ZIP_URL in env to skip zipping and use that URL
 * directly (useful for re-running training on an already-hosted zip).
 *
 * This is a potentially expensive provider call. It is intentionally
 * opt-in and clearly marked.
 *
 * @param name      - character name (used as trigger phrase unless overridden)
 * @param refImages - array of data URLs, http URLs, or local file paths (10-50 recommended)
 * @param opts      - { trigger?: string } — override the trigger phrase
 */
export async function trainFyID(
  name: string,
  refImages: string[],
  opts: { trigger?: string } = {},
): Promise<{
  loraUrl?: string;
  model: string;
  cost?: number | null;
  note: string;
}> {
  const endpoint = process.env.MUAPI_LORA_TRAINER_MODEL || 'flux-lora-trainer';
  const triggerWord = opts.trigger || name;
  // Sanitise the name for use as a GCS object-path segment (no spaces / special chars).
  const safeId = name.toLowerCase().replace(/[^a-z0-9_-]/g, '-').slice(0, 48) || 'fyid';

  console.error(
    `[persona-studio] trainFyID: iniciando entrenamiento LoRA para "${name}" ` +
      `(${refImages.length} refs, endpoint=${endpoint}) — OPERACIÓN EXTERNA COSTOSA`,
  );

  if (refImages.length < 5) {
    console.error(
      '[persona-studio] trainFyID: advertencia — se recomienda 10-50 imágenes; ' +
        `se recibieron solo ${refImages.length}. La calidad de la identidad puede ser baja.`,
    );
  }

  try {
    let signedZipUrl: string;

    // ── Escape hatch: caller already has a hosted zip ────────────────────────
    const envZipUrl = process.env.MUAPI_LORA_ZIP_URL as string | undefined;
    if (envZipUrl) {
      console.error(
        `[persona-studio] trainFyID: usando MUAPI_LORA_ZIP_URL directamente (omitiendo empaquetado)`,
      );
      signedZipUrl = envZipUrl;
    } else {
      // ── Step 1: load each reference image to bytes ───────────────────────
      console.error(
        `[persona-studio] trainFyID: cargando ${refImages.length} imágenes de referencia…`,
      );

      interface LoadedImage {
        data: Buffer;
        ext: string;
      }

      const loadResults = await Promise.all(
        refImages.map(async (src, idx): Promise<LoadedImage | null> => {
          try {
            // Delegate to loadRefInline (handles data:, http:, local path).
            const ref = await loadRefInline(src);
            if (ref) {
              const ext =
                ref.mimeType === 'image/jpeg' || ref.mimeType === 'image/jpg'
                  ? 'jpg'
                  : ref.mimeType === 'image/webp'
                  ? 'webp'
                  : 'png';
              return { data: Buffer.from(ref.data, 'base64'), ext };
            }
            console.error(
              `[persona-studio] trainFyID: no se pudo cargar imagen ${idx + 1} — omitiendo`,
            );
            return null;
          } catch (e) {
            console.error(
              `[persona-studio] trainFyID: error cargando imagen ${idx + 1}:`,
              e instanceof Error ? e.message : e,
            );
            return null;
          }
        }),
      );

      const loaded = loadResults.filter((r): r is LoadedImage => r !== null);

      if (loaded.length === 0) {
        throw new Error(
          'No se pudo cargar ninguna imagen de referencia. Verifica que las rutas/URLs sean accesibles.',
        );
      }

      console.error(
        `[persona-studio] trainFyID: ${loaded.length}/${refImages.length} imágenes cargadas`,
      );

      // ── Step 2: build .zip in memory using jszip ─────────────────────────
      console.error(`[persona-studio] trainFyID: empaquetando imágenes en .zip…`);
      const zip = new JSZip();
      loaded.forEach((img, i) => {
        zip.file(`image-${i + 1}.${img.ext}`, img.data);
      });
      const zipBuffer = await zip.generateAsync({ type: 'nodebuffer', compression: 'DEFLATE' });

      console.error(
        `[persona-studio] trainFyID: zip generado — ${(zipBuffer.length / 1024).toFixed(1)} KB`,
      );

      // ── Step 3: upload zip to GCS + get v4 signed read URL (24 h) ────────
      const objectPath = generateGcsPath(`fyid-${safeId}`, 'lora-train', 'zip');
      console.error(
        `[persona-studio] trainFyID: subiendo zip a GCS → ${objectPath}…`,
      );
      await uploadToGCS(objectPath, zipBuffer, 'application/zip');

      const expiresMs = Date.now() + 24 * 3600 * 1000;
      const [url] = await getBucket().file(objectPath).getSignedUrl({
        version: 'v4',
        action: 'read',
        expires: expiresMs,
      });
      signedZipUrl = url;
      console.error(
        `[persona-studio] trainFyID: URL firmada obtenida (expira en 24 h)`,
      );
    }

    // ── Step 4: call flux-lora-trainer via Muapi ─────────────────────────────
    console.error(
      `[persona-studio] trainFyID: enviando a Muapi (${endpoint}) con trigger="${triggerWord}"…`,
    );

    const result = await muapiGenerate(
      endpoint,
      {
        images_data_url: signedZipUrl,
        trigger_phrase: triggerWord,
        training_style: 'subject',
      },
      {
        // LoRA training typically takes 5-20 minutes
        timeoutMs: 1_200_000,
        intervalMs: 10_000,
      },
    );

    // The muapi client normalises the result to outputs[]; flux-lora-trainer
    // returns lora_url which Muapi maps to outputs[0].
    const loraUrl = result.outputs?.[0];
    const costUsd = result.cost?.amount_usd ?? null;

    console.error(
      `[persona-studio] trainFyID completo — LoRA: ${loraUrl ?? '(sin URL en outputs)'}, ` +
        `costo: ${costUsd !== null ? `$${costUsd.toFixed(4)}` : 'desconocido'}`,
    );

    return {
      loraUrl,
      model: `muapi:${endpoint}`,
      cost: costUsd,
      note: loraUrl
        ? `Entrenamiento completado. LoRA disponible en: ${loraUrl}. ` +
          `Guarda esta URL como referencia de identidad para "${name}" en tu sistema de personas.`
        : `El trabajo completó pero no devolvió URL de LoRA. ` +
          `Revisa el resultado de Muapi (endpoint: ${endpoint}) manualmente.`,
    };
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    console.error(`[persona-studio] trainFyID falló:`, msg);
    return {
      loraUrl: undefined,
      model: `muapi:${endpoint}`,
      cost: null,
      note:
        `Entrenamiento LoRA falló: ${msg}. ` +
        `Verifica que MUAPI_API_KEY esté configurada, que GCS_ASSETS_BUCKET esté configurado, ` +
        `que el endpoint "${endpoint}" exista en tu cuenta de Muapi, ` +
        `y que las imágenes de referencia sean accesibles (URLs públicas, data URLs o rutas locales válidas). ` +
        `Escape hatch: setea MUAPI_LORA_ZIP_URL en .env.local para usar un zip ya alojado.`,
    };
  }
}

// ─── 4. brandAmbassador ───────────────────────────────────────────────────────

/**
 * Brand Ambassador content-series workflow (Higgsfield AI Influencer equivalent).
 *
 * Opus plans a short content series for the persona around a campaign brief,
 * then generates `pieces` (default 3) persona images aligned to the plan.
 *
 * Returns:
 *   plan   — the Opus-authored content series plan as a string
 *   images — data URLs of the generated ambassador images
 *
 * @param persona - the AI persona / brand face
 * @param ctx     - brand context
 * @param brief   - campaign brief / goal (e.g. "launch our summer skincare line")
 * @param opts    - { pieces?: number } — number of content pieces to generate (default 3)
 */
export async function brandAmbassador(
  persona: Persona,
  ctx: VideoBrandCtx,
  brief: string,
  opts: { pieces?: number } = {},
): Promise<{ plan: string; images: string[] }> {
  const pieces = clamp(opts.pieces ?? 3, 1, 12);

  console.error(
    `[persona-studio] brandAmbassador: planificando serie de ${pieces} piezas ` +
      `para "${persona.name}" — brief: "${brief.slice(0, 80)}…"`,
  );

  // Step 1 — Opus drafts the content series plan + image scene specs
  interface AmbassadorPlan {
    plan: string;
    scenes: Array<{ piece: number; caption: string; imagePrompt: string }>;
  }

  const planSystem = `You are a top-tier brand strategist and creative director for a social media marketing agency.
You are planning a short AI-influencer content series for a brand ambassador campaign.

RULES:
- Return a JSON object: { "plan": string, "scenes": Array<{ piece: number, caption: string, imagePrompt: string }> }
- "plan": 3–5 sentence narrative overview of the content series strategy, written in the same language as the brief.
- "scenes": exactly ${pieces} items, one per content piece, numbered 1–${pieces}.
  - "piece": the piece number (1-based integer)
  - "caption": a punchy social media caption for this piece (in the brief's language, 1–2 sentences, no hashtags)
  - "imagePrompt": a VIVID, COMPLETELY TEXT-FREE English description of a photographic still for this ambassador moment. NO readable text, NO UI, NO logos visible. Describe person, scene, light, mood, composition only.
- Each scene should support a different content format / moment: e.g. launch reveal, tutorial/how-to, lifestyle use, behind-the-scenes, hero product shot with ambassador.
- Output ONLY valid JSON — no markdown, no commentary.`;

  const planPrompt = `Brand Ambassador Campaign Brief:
Brand: ${ctx.name}
Colors: ${ctx.brandColors}
Fonts: ${ctx.fonts}
Brand info: ${ctx.info}

Ambassador persona: ${persona.name}
Description: ${persona.description}

Campaign brief: ${brief}

Plan a ${pieces}-piece content series for this ambassador campaign as { plan, scenes }.`;

  let planText = '';
  let scenes: Array<{ piece: number; caption: string; imagePrompt: string }> = [];

  try {
    const raw = await callAIJSON<AmbassadorPlan | null>(planPrompt, {
      system: planSystem,
      maxTokens: 4096,
    });

    if (raw && typeof raw === 'object' && typeof raw.plan === 'string') {
      planText = raw.plan;
      if (Array.isArray(raw.scenes)) {
        scenes = raw.scenes.filter(
          (s) =>
            s !== null &&
            typeof s === 'object' &&
            typeof s.imagePrompt === 'string' &&
            s.imagePrompt.trim().length > 0,
        );
      }
    }
  } catch (e) {
    console.error(
      '[persona-studio] brandAmbassador: error al generar plan con Opus:',
      e instanceof Error ? e.message : e,
    );
  }

  // Fallback plan if Opus failed
  if (!planText) {
    planText =
      `Content series of ${pieces} pieces for ${persona.name} as brand ambassador for ${ctx.name}. ` +
      `Campaign focus: ${brief}. Each piece showcases the ambassador in authentic on-brand moments.`;
  }

  // Fallback scenes if Opus returned too few
  if (scenes.length < pieces) {
    console.error(
      `[persona-studio] brandAmbassador: Opus devolvió ${scenes.length}/${pieces} escenas — completando con fallbacks`,
    );
    const fallbackPrompts = [
      `${persona.description}, holding a product with quiet confidence, clean studio background with brand accent lighting, editorial quality portrait`,
      `${persona.description}, lifestyle moment in an elegant setting, product naturally integrated, golden warm light, authentic candid feel`,
      `${persona.description}, dynamic outdoor setting related to the brand's world, energetic mood, aspirational lifestyle image`,
      `${persona.description}, behind-the-scenes casual moment, natural light, approachable and relatable energy, brand environment`,
      `${persona.description}, hero campaign shot, cinematic lighting, powerful confident pose, brand aesthetic at its peak`,
    ];
    while (scenes.length < pieces) {
      const idx = scenes.length;
      scenes.push({
        piece: idx + 1,
        caption: `${persona.name} x ${ctx.name} — Piece ${idx + 1}`,
        imagePrompt: fallbackPrompts[idx % fallbackPrompts.length],
      });
    }
  }

  scenes = scenes.slice(0, pieces);

  // Step 2 — Generate each ambassador image
  const tasks = scenes.map((scene) => async (): Promise<string | null> => {
    try {
      const urls = await generatePersonaImage(persona, ctx, scene.imagePrompt, { count: 1 });
      const url = urls[0] ?? null;
      if (url) {
        console.error(`[persona-studio] brandAmbassador ✓ Pieza ${scene.piece}: "${scene.caption.slice(0, 50)}"`);
      }
      return url;
    } catch (e) {
      console.error(
        `[persona-studio] brandAmbassador fallo en pieza ${scene.piece}:`,
        e instanceof Error ? e.message : e,
      );
      return null;
    }
  });

  const settled = await poolSettled(tasks, 3);
  const images = settled.filter((r): r is string => typeof r === 'string' && r.length > 0);

  console.error(
    `[persona-studio] brandAmbassador completo: ${images.length}/${pieces} imágenes, plan listo`,
  );

  return { plan: planText, images };
}
