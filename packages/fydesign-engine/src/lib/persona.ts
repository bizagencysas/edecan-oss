// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  persona.ts — AI Influencer / Persona engine (Soul-ID-equivalent)            ║
// ║                                                                              ║
// ║  A persona is a saved "photo dump": a description + a set of reference       ║
// ║  images, reused to keep a consistent character/face/spokesperson across      ║
// ║  generations WITHOUT any model training. Consistency is achieved by:          ║
// ║    1. Embedding persona.description in every generation prompt                ║
// ║    2. Passing the reference images inline to Imagen / Gemini                  ║
// ║    3. Instructing the model to preserve identity/face/wardrobe/vibe           ║
// ║                                                                              ║
// ║  Data lives at:                                                              ║
// ║    FYDESIGN_STATE_ROOT/personas/<brand>/<id>.json  (manifest)                ║
// ║    FYDESIGN_STATE_ROOT/personas/<brand>/<id>/ref-N.png  (references)         ║
// ║                                                                              ║
// ║  Env:                                                                        ║
// ║    MUAPI_LIPSYNC_MODEL   lipsync endpoint on muapi.ai (e.g. sync-lipsync-v2) ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import path from 'node:path';
import { readFile, writeFile, mkdir, readdir } from 'node:fs/promises';

import type { Persona, VideoBrandCtx, VideoAspect } from './video/types';
import { callAI } from './ai/deepseek-client';
import {
  generateBrandStill,
  hostStillForMuapi,
  loadRefInline,
} from './ai/brand-image';
import { hasMuapi, muapiGenerate } from './ai/muapi-client';
import { uploadToGCS, getBucket, generateGcsPath } from './gcs';
import { requiredRuntimePath } from './runtime-env';

// ─── Helpers ─────────────────────────────────────────────────────────────────

/** Convert a human name to a safe directory/file slug (lowercase, no spaces). */
function slug(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^\w\s-]/g, '')   // strip non-word chars except hyphen
    .trim()
    .replace(/[\s_]+/g, '-')    // spaces/underscores → hyphen
    .replace(/-+/g, '-');       // collapse runs of hyphens
}

/**
 * Upload an audio Buffer to GCS and return a signed URL usable by Muapi,
 * or fall back to a base64 data URL if GCS is not configured.
 */
async function hostAudioForMuapi(buf: Buffer, mimeType: string): Promise<string> {
  try {
    const ext = mimeType.includes('wav') ? 'wav' : 'mp3';
    const objectPath = generateGcsPath(
      `persona-vo-${Date.now()}-${Math.floor(Math.random() * 1e6)}`,
      'persona-audio',
      ext,
    );
    await uploadToGCS(objectPath, buf, mimeType);
    const [url] = await getBucket().file(objectPath).getSignedUrl({
      version: 'v4',
      action: 'read',
      expires: Date.now() + 24 * 3600 * 1000,
    });
    if (url) return url;
  } catch {
    /* GCS no configurado — caemos a data URL */
  }
  return `data:${mimeType};base64,${buf.toString('base64')}`;
}

// ─── Directory helpers ────────────────────────────────────────────────────────

/**
 * Absolute path to the personas directory for a brand.
 * e.g. FYDESIGN_STATE_ROOT/personas/acme
 */
export function personaDir(brand: string): string {
  return path.join(requiredRuntimePath('FYDESIGN_STATE_ROOT'), 'personas', slug(brand));
}

// ─── List / Load ─────────────────────────────────────────────────────────────

/** Read every *.json in personaDir. Missing directory → []. */
export async function listPersonas(brand: string): Promise<Persona[]> {
  const dir = personaDir(brand);
  let entries: string[];
  try {
    entries = await readdir(dir);
  } catch {
    return [];
  }
  const personas: Persona[] = [];
  for (const entry of entries) {
    if (!entry.endsWith('.json')) continue;
    try {
      const raw = await readFile(path.join(dir, entry), 'utf8');
      const p = JSON.parse(raw) as Persona;
      personas.push(p);
    } catch (e) {
      console.error(`[persona] Error leyendo ${entry}:`, e instanceof Error ? e.message : e);
    }
  }
  return personas;
}

/**
 * Load a persona by id slug OR case-insensitive name match.
 * Returns null if not found.
 */
export async function loadPersona(brand: string, name: string): Promise<Persona | null> {
  const all = await listPersonas(brand);
  const needle = name.toLowerCase();
  return (
    all.find((p) => p.id === needle) ??
    all.find((p) => p.name.toLowerCase() === needle) ??
    null
  );
}

// ─── Create ──────────────────────────────────────────────────────────────────

/**
 * Create a new persona for a brand.
 *
 * - id = slug(name)
 * - Creates personaDir/<id>/ and copies/downloads all refs as ref-N.png
 * - If description is empty, asks Opus for a concise visual description
 * - Writes personaDir/<id>.json and returns the Persona object
 */
export async function createPersona(
  brand: string,
  name: string,
  opts: { description?: string; refs: string[]; voice?: string },
): Promise<Persona> {
  const id = slug(name);
  const dir = personaDir(brand);
  const refDir = path.join(dir, id);

  // Ensure directories exist
  await mkdir(refDir, { recursive: true });

  // Download / copy every reference image to disk
  const writtenPaths: string[] = [];
  for (let i = 0; i < opts.refs.length; i++) {
    const src = opts.refs[i];
    const destPath = path.join(refDir, `ref-${i}.png`);
    try {
      const inline = await loadRefInline(src);
      if (!inline) {
        console.error(`[persona] No se pudo cargar la referencia ${i}: ${src.slice(0, 80)}`);
        continue;
      }
      await writeFile(destPath, Buffer.from(inline.data, 'base64'));
      writtenPaths.push(destPath);
    } catch (e) {
      console.error(`[persona] Error guardando ref-${i}:`, e instanceof Error ? e.message : e);
    }
  }

  // Auto-generate description via Opus if not provided
  let description = opts.description || '';
  if (!description) {
    try {
      console.error(`[persona] Generando descripción visual para "${name}" vía AI…`);
      description = await callAI(
        `You are a casting director. Write a SHORT (2–3 sentences) visual description of an AI influencer / brand spokesperson named "${name}" for the brand "${brand}". Cover: approximate age/demographic, skin tone/hair, wardrobe vibe, overall energy/attitude. Be specific enough that an image model can reproduce the same character. No names, no celebrities. English only.`,
        { maxTokens: 200 },
      );
      description = description.trim();
    } catch (e) {
      console.error('[persona] No se pudo generar la descripción:', e instanceof Error ? e.message : e);
      description = `Brand spokesperson for ${brand} named ${name}.`;
    }
  }

  const persona: Persona = {
    id,
    name,
    brand,
    description,
    refs: writtenPaths,
    ...(opts.voice ? { voice: opts.voice } : {}),
    createdAt: new Date().toISOString(),
  };

  // Write manifest
  const manifestPath = path.join(dir, `${id}.json`);
  await writeFile(manifestPath, JSON.stringify(persona, null, 2), 'utf8');
  console.error(`[persona] Creado: ${manifestPath} (${writtenPaths.length} refs)`);

  return persona;
}

// ─── Generate image ───────────────────────────────────────────────────────────

/**
 * Generate one or more on-brand images of a persona.
 *
 * Builds a prompt that embeds persona.description + brand palette +
 * identity-consistency instructions, then loads up to 3 reference images
 * and calls generateBrandStill for each requested output.
 *
 * Returns an array of base64 data URLs.
 */
export async function generatePersonaImage(
  persona: Persona,
  ctx: VideoBrandCtx,
  prompt: string,
  opts: { aspect?: VideoAspect; count?: number } = {},
): Promise<string[]> {
  const count = Math.min(Math.max(1, opts.count ?? 1), 4);
  const aspect = opts.aspect ?? '4:3';

  // Load up to 3 reference images inline
  const refSrcs = persona.refs.slice(0, 3);
  const references: Array<{ data: string; mimeType: string }> = [];
  for (const src of refSrcs) {
    const inline = await loadRefInline(src);
    if (inline) references.push(inline);
  }

  // Build identity-locking prompt
  const brandBlock = ctx.brandColors
    ? `Brand palette: ${ctx.brandColors}.`
    : '';
  const fontsBlock = ctx.fonts ? `Brand fonts: ${ctx.fonts}.` : '';
  const prompt2 = [
    prompt,
    `PERSONA: ${persona.description}`,
    'Keep the SAME person/face/identity as the reference images — consistent facial features, skin tone, hair, wardrobe and vibe.',
    brandBlock,
    fontsBlock,
    'Photo-realistic, professional photography, no text, no UI.',
  ]
    .filter(Boolean)
    .join(' ');

  const results: string[] = [];
  for (let i = 0; i < count; i++) {
    try {
      const still = await generateBrandStill(prompt2, {
        quality: 'brand',
        aspect,
        references: references.length > 0 ? references : undefined,
      });
      results.push(still.dataUrl);
    } catch (e) {
      console.error(`[persona] Error generando imagen ${i + 1}/${count}:`, e instanceof Error ? e.message : e);
    }
  }

  return results;
}

// ─── Talking head (lipsync) ───────────────────────────────────────────────────

/**
 * Generate a talking-head video of a persona speaking a script.
 *
 * Flow:
 *   1. Get a portrait still (baseImage arg, or generate one)
 *   2. Host the still via hostStillForMuapi → image_url
 *   3. Generate voiceover audio via audio-client (if available) → audio_url
 *   4. Submit to Muapi lipsync endpoint and wait for output
 *
 * Env:
 *   MUAPI_LIPSYNC_MODEL   the Muapi endpoint for lipsync (e.g. sync-lipsync-v2).
 *                         Check muapi.ai/models for available lipsync models.
 *                         If unset AND lipsyncModel arg is omitted, this function
 *                         throws a clear error explaining what to set.
 *
 * Returns { url, cost, model }.
 */
export async function generateTalkingHead(
  persona: Persona,
  ctx: VideoBrandCtx,
  opts: {
    script: string;
    baseImage?: string;
    lipsyncModel?: string;
  },
): Promise<{ url: string; cost?: { amount_usd?: number } | null; model: string }> {
  if (!hasMuapi()) {
    throw new Error(
      '[persona] Muapi no está configurado. Necesitas MUAPI_API_KEY (crea una en muapi.ai).',
    );
  }

  // 1. Resolve the portrait (provided OR generate a front-facing headshot)
  let portraitDataUrl: string;
  if (opts.baseImage) {
    // Accept file path or data URL directly
    if (opts.baseImage.startsWith('data:')) {
      portraitDataUrl = opts.baseImage;
    } else {
      const buf = await readFile(opts.baseImage);
      portraitDataUrl = `data:image/png;base64,${buf.toString('base64')}`;
    }
  } else {
    console.error('[persona] Generando retrato frontal para talking-head…');
    const portraits = await generatePersonaImage(
      persona,
      ctx,
      'a clean, well-lit front-facing portrait, head and shoulders, neutral studio background',
      { aspect: '3:4', count: 1 },
    );
    if (portraits.length === 0) {
      throw new Error('[persona] No se pudo generar el retrato base para el talking-head.');
    }
    portraitDataUrl = portraits[0];
  }

  // 2. Host the portrait so Muapi can fetch it
  const image_url = await hostStillForMuapi(portraitDataUrl);

  // 3. Generate voiceover audio (optional — graceful fallback to text-to-speech in Muapi)
  let audio_url: string | undefined;
  try {
    // Dynamic import: audio-client may not exist yet (built in parallel).
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const audioClient = await import('./ai/audio-client') as any;
    if (typeof audioClient.generateVoiceover === 'function') {
      const vo = await audioClient.generateVoiceover(opts.script, {
        voice: persona.voice,
      });
      if (vo?.file) {
        const audioBuf = await readFile(vo.file);
        // Detect mime type from file extension
        const ext = (vo.file.split('.').pop() || 'mp3').toLowerCase();
        const mimeType = ext === 'wav' ? 'audio/wav' : 'audio/mpeg';
        audio_url = await hostAudioForMuapi(audioBuf, mimeType);
        console.error(`[persona] Voiceover generado y subido: ${vo.file}`);
      }
    }
  } catch {
    // audio-client no disponible — Muapi usará TTS interno (campo text)
    console.error('[persona] audio-client no disponible — usando TTS de Muapi (campo text)');
  }

  // 4. Resolve the lipsync endpoint
  //
  //    Priority: opts.lipsyncModel → MUAPI_LIPSYNC_MODEL env → clear error.
  //
  //    Common Muapi lipsync model IDs (check muapi.ai/models for current list):
  //      sync-lipsync-v2       — Sync Labs v2, high quality (recommended)
  //      sync-lipsync-v1       — Sync Labs v1, faster
  //      hedra-character-v1    — Hedra character animation
  //      wav2lip               — classic Wav2Lip (lower quality, very fast)
  //
  const endpoint =
    opts.lipsyncModel ||
    process.env.MUAPI_LIPSYNC_MODEL ||
    (() => {
      throw new Error(
        '[persona] No se encontró un modelo lipsync. ' +
          'Establece MUAPI_LIPSYNC_MODEL en tu .env.local con el nombre del endpoint de Muapi, ' +
          'por ejemplo: MUAPI_LIPSYNC_MODEL=sync-lipsync-v2 ' +
          '(Revisa https://muapi.ai/models para la lista actualizada de modelos lipsync disponibles.)',
      );
    })();

  // 5. Submit to Muapi lipsync and wait
  console.error(`[persona] Enviando talking-head a Muapi/${endpoint}…`);
  const body: Record<string, unknown> = {
    image_url,
    ...(audio_url ? { audio_url } : { text: opts.script }),
  };

  const result = await muapiGenerate(endpoint, body, {
    timeoutMs: 600_000,
    intervalMs: 5_000,
  });

  const url = result.outputs?.[0];
  if (!url) {
    throw new Error(`[persona] Muapi lipsync (${endpoint}) no devolvió ningún output.`);
  }

  return {
    url,
    cost: result.cost ?? null,
    model: `muapi:${endpoint}`,
  };
}
