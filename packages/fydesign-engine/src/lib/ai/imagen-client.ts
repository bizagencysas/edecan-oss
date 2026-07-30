// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Vertex AI Image Generation Client                                         ║
// ║  Default model: gemini-3-pro-image-preview (Nano Banana Pro)               ║
// ║   - Better than Imagen 4 for design assets — follows prompt instructions   ║
// ║     literally (e.g. "credit gauge showing 720" actually shows 720).        ║
// ║   - Lives in the "global" Vertex AI location.                              ║
// ║                                                                            ║
// ║  Drop-in alternative: set GOOGLE_PREMIUM_IMAGE_MODEL=imagen-4.0-generate-001║
// ║  and VERTEX_GOOGLE_IMAGE_LOCATION=us-central1.                             ║
// ║                                                                            ║
// ║  Auth: service account JSON in GOOGLE_CREDENTIALS_JSON env (Vercel-safe).  ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { GoogleGenAI, Modality } from '@google/genai';

// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  NO-TEXT POLICY (the single most important rule for image models)           ║
// ║                                                                            ║
// ║  Image models (Imagen 4, Nano Banana / gemini-*-image) HALLUCINATE garbage ║
// ║  text whenever a prompt mentions words, labels, UI copy, or "showing X".    ║
// ║  Result: "HEGADLE MOPFLARD", "GLASEMOPHUE", "YOUR TEXT" baked into pixels.  ║
// ║                                                                            ║
// ║  Policy: the image model ONLY renders the VISUAL (people, scenes,          ║
// ║  backgrounds, products, hands, environments, light). ALL real text         ║
// ║  (titles, prices, badges, captions, labels) is composed later in HTML/CSS  ║
// ║  on top of the image. So we ALWAYS forbid text in the prompt + negative    ║
// ║  prompt, unless a caller explicitly opts in via { allowText: true }.       ║
// ║                                                                            ║
// ║  This is enforced at THIS chokepoint so every caller in the repo inherits  ║
// ║  it through the configured provider (screen recreation and media flows).   ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

/** Appended to every image prompt (works for BOTH Imagen 4 and gemini-*-image, which ignores negativePrompt). */
export const NO_TEXT_IMAGE_DIRECTIVE =
  'CRITICAL: render NO text of any kind. No letters, no words, no numbers, no typography, ' +
  'no captions, no labels, no signage, no UI text, no watermarks, no logos. ' +
  'Generate ONLY the visual scene (people, products, environment, lighting, mood, textures). ' +
  'Any text or branding will be added later as a separate overlay — leave clean space for it. ' +
  'Surfaces that would normally carry words (signs, screens, buttons, packaging) must stay blank or abstract.';

/** Default negativePrompt — only honored by imagen-* models, harmless (ignored) for gemini-image. */
export const NO_TEXT_NEGATIVE_PROMPT =
  'text, letters, words, numbers, typography, captions, labels, title, heading, signage, ' +
  'watermark, logo, UI text, caption box, subtitles, gibberish text, misspelled words';

/**
 * Appended to every image prompt (default): the image model must NEVER draw an app interface,
 * mockup or on-screen UI — Imagen/Gemini produce garbage fake UIs. Real product screens are
 * built afterwards in HTML/CSS. Any device in the photo keeps a BLANK screen.
 */
export const NO_UI_IMAGE_DIRECTIVE =
  'CRITICAL: do NOT render any app interface, software UI, dashboard, website or on-screen content. ' +
  'If a phone, tablet, laptop or monitor appears, its screen MUST be blank, off, or a soft abstract gradient — ' +
  'never a populated app screen, never charts, graphs, buttons, menus, lists or icons. ' +
  'Do NOT produce a "mockup", "screenshot" or "wireframe". The product UI is added afterwards as a separate ' +
  'HTML/CSS overlay — your job is ONLY the photographic scene (people, products, environment, light).';

/** Default negativePrompt for the no-UI policy — only honored by imagen-* models. */
export const NO_UI_NEGATIVE_PROMPT =
  'app interface, user interface, UI, dashboard, app screen, populated phone screen, screenshot, mockup, ' +
  'wireframe, website screen, charts, graphs, buttons, menus, app icons, grid of icons';

/**
 * Wrap a raw image prompt with the no-text directive (idempotent-ish: only appended once).
 * Exported so non-Vertex image clients (Muapi, OpenAI) can apply the SAME policy.
 */
export function enforceNoTextPrompt(prompt: string): string {
  const p = (prompt || '').trim();
  if (p.includes(NO_TEXT_IMAGE_DIRECTIVE)) return p;
  return `${p}\n\n${NO_TEXT_IMAGE_DIRECTIVE}`;
}

/**
 * Remove explicit app/UI/mockup *requests* from a raw image prompt so the image model never
 * tries to draw fake software UI. Conservative: strips clearly-UI nouns and
 * "<device> showing <app content>" clauses; innocent "phone" mentions keep working (the
 * no-UI directive forces their screens blank).
 */
export function stripUiFromPrompt(prompt: string): string {
  let p = prompt || '';
  // "<device> showing/displaying/with <app-ish content...>" → "<device> with a blank dark screen"
  p = p.replace(
    /\b(phone|smartphone|iphone|tablet|ipad|laptop|monitor|screen|display)\s+(showing|displaying|with|featuring)\s+(the\s+|a\s+|an\s+)?(app|application|software|ui|user interface|interface|dashboard|home\s*screen|feed|profile|chat|messages?|notifications?|posts?|stats?|analytics|listings?|offers?|screens?)\b[\w\s,'-]*/gi,
    '$1 with a blank dark screen',
  );
  // standalone UI nouns → neutral
  p = p.replace(/\b(app|application)\s+(home\s*)?screens?\b/gi, 'scene');
  p = p.replace(/\b(mock-?ups?|screenshots?|wireframes?|dashboards?)\b/gi, 'scene');
  p = p.replace(/\b(app|user)\s*(interface|ui)\b/gi, 'scene');
  return p.replace(/\s{2,}/g, ' ').trim();
}

/** Apply the no-UI policy to a prompt: strip UI requests + append the no-UI directive (idempotent-ish). */
export function enforceNoUiPrompt(prompt: string): string {
  const cleaned = stripUiFromPrompt(prompt);
  if (cleaned.includes(NO_UI_IMAGE_DIRECTIVE)) return cleaned;
  return `${cleaned}\n\n${NO_UI_IMAGE_DIRECTIVE}`;
}

/** Merge a caller-supplied negative prompt with the no-text negatives (de-duplicated, comma-joined). */
export function mergeNegativePrompt(existing?: string): string {
  const parts = [existing, NO_TEXT_NEGATIVE_PROMPT]
    .filter((s): s is string => !!s && !!s.trim())
    .join(', ');
  // de-dup terms while preserving order
  const seen = new Set<string>();
  return parts
    .split(',')
    .map((t) => t.trim())
    .filter((t) => t && !seen.has(t.toLowerCase()) && seen.add(t.toLowerCase()))
    .join(', ');
}

// Cache one client per (project + location) — Imagen 4 needs us-central1, Gemini-3-Image needs global.
const _vertexCache = new Map<string, GoogleGenAI>();

function vertexClient(location: string): GoogleGenAI {
  const raw = process.env.GOOGLE_CREDENTIALS_JSON;
  if (!raw) throw new Error('GOOGLE_CREDENTIALS_JSON not set');

  let credentials: Record<string, unknown>;
  try {
    credentials = JSON.parse(raw);
  } catch {
    throw new Error('GOOGLE_CREDENTIALS_JSON is not valid JSON');
  }

  // Project: explicit env var wins, else fall back to the service account's project_id.
  const project = process.env.VERTEX_AI_PROJECT_ID || (credentials.project_id as string | undefined);
  if (!project) throw new Error('No GCP project: set VERTEX_AI_PROJECT_ID or include project_id in GOOGLE_CREDENTIALS_JSON');

  const cacheKey = `${project}::${location}`;
  const cached = _vertexCache.get(cacheKey);
  if (cached) return cached;

  const client = new GoogleGenAI({
    vertexai: true,
    project,
    location,
    googleAuthOptions: { credentials },
  });
  _vertexCache.set(cacheKey, client);
  return client;
}

export interface ImagenOpts {
  /** "1:1" | "16:9" | "9:16" | "4:3" | "3:4" */
  aspectRatio?: '1:1' | '16:9' | '9:16' | '4:3' | '3:4';
  numberOfImages?: number;
  /** Override the Vertex model (e.g. 'imagen-4.0-ultra-generate-001'). */
  model?: string;
  /** Negative prompt — only honored by Imagen models, ignored by Gemini-Image. */
  negativePrompt?: string;
  /**
   * Reference images (base64, WITHOUT the data: prefix) fed to gemini-image so it
   * composes the real brand assets (logo, app screenshots) into the scene.
   * Only honored by the gemini-*-image path; ignored by imagen-* models.
   */
  references?: Array<{ data: string; mimeType: string }>;
  /**
   * Escape hatch: allow the model to render text in the image.
   * Default false → the no-text policy (NO_TEXT_IMAGE_DIRECTIVE + negative prompt)
   * is applied so text lives in the CSS overlay, never baked into pixels.
   * Only set true for the rare case where the literal artwork IS typography
   * AND you accept the model may garble it.
   */
  allowText?: boolean;
  /**
   * Escape hatch: allow the model to render app UI / device screens / a mockup in the image.
   * Default false → the no-UI policy (NO_UI_IMAGE_DIRECTIVE + negatives + prompt sanitizer)
   * is applied so app screens are built in HTML/CSS, never hallucinated by the image model.
   * Leave false for ~all marketing imagery; only set true for a deliberate device-frame asset.
   */
  allowUi?: boolean;
}

export interface ImagenResult {
  /** data:image/png;base64,... — drop directly into <img src> */
  dataUrl: string;
  mimeType: string;
}

/**
 * Generate one image using whichever Vertex model is configured.
 * Routes to the right API based on the model family:
 *  - `gemini-*-image-*` → generateContent + responseModalities: [IMAGE]
 *  - `imagen-*`         → generateImages
 */
export async function generateImagenImage(
  prompt: string,
  opts: ImagenOpts = {},
): Promise<ImagenResult> {
  const model = opts.model || process.env.GOOGLE_PREMIUM_IMAGE_MODEL || 'gemini-3-pro-image-preview';
  const isImagen = model.startsWith('imagen-');
  // Imagen 4 lives in a region (us-central1); gemini-*-image lives in 'global'.
  const location = isImagen
    ? (process.env.VERTEX_IMAGEN_LOCATION || 'us-central1')
    : (process.env.VERTEX_GOOGLE_IMAGE_LOCATION || 'global');
  const ai = vertexClient(location);

  // NO-TEXT POLICY (default): bake the prohibition into the prompt (the only thing
  // gemini-*-image honors) AND into the negative prompt (the only thing imagen-* honors).
  // Caller can opt out with { allowText: true } for the rare typographic-artwork case.
  let effectivePrompt = opts.allowText ? prompt : enforceNoTextPrompt(prompt);
  let effectiveOpts: ImagenOpts = opts.allowText
    ? opts
    : { ...opts, negativePrompt: mergeNegativePrompt(opts.negativePrompt) };

  // NO-UI POLICY (default): the image model must NEVER draw app UI / mockups — those are built
  // later in HTML/CSS (Imagen/Gemini hallucinate garbage UIs). Opt out with { allowUi: true }.
  if (!opts.allowUi) {
    effectivePrompt = enforceNoUiPrompt(effectivePrompt);
    const neg = [effectiveOpts.negativePrompt, NO_UI_NEGATIVE_PROMPT]
      .filter((s): s is string => !!s && !!s.trim())
      .join(', ');
    effectiveOpts = { ...effectiveOpts, negativePrompt: neg };
  }

  if (isImagen) {
    return generateViaImagen(ai, model, effectivePrompt, effectiveOpts);
  }
  return generateViaGeminiImage(ai, model, effectivePrompt, effectiveOpts);
}

/* ─── Path A: Gemini-image (Nano Banana, gemini-3-pro-image-preview, …) ────── */

async function generateViaGeminiImage(
  ai: GoogleGenAI,
  model: string,
  prompt: string,
  opts: ImagenOpts,
): Promise<ImagenResult> {
  const aspectHint = opts.aspectRatio ? ` Aspect ratio: ${opts.aspectRatio}.` : '';
  // Reference images first, then the text prompt — Nano Banana composes them in.
  const inputParts: Array<{ text: string } | { inlineData: { data: string; mimeType: string } }> = [];
  for (const ref of opts.references || []) {
    if (ref?.data) inputParts.push({ inlineData: { data: ref.data, mimeType: ref.mimeType || 'image/png' } });
  }
  inputParts.push({ text: prompt + aspectHint });
  const res = await ai.models.generateContent({
    model,
    contents: [{ role: 'user', parts: inputParts }],
    config: {
      responseModalities: [Modality.IMAGE],
      temperature: 0.7,
    },
  });

  const parts = res.candidates?.[0]?.content?.parts || [];
  for (const part of parts) {
    const inline = part.inlineData;
    if (inline?.data) {
      const mimeType = inline.mimeType || 'image/png';
      return { dataUrl: `data:${mimeType};base64,${inline.data}`, mimeType };
    }
  }
  throw new Error(`${model} returned no image data`);
}

/* ─── Path B: Imagen 4 (imagen-4.0-generate-001 etc.) ─────────────────────── */

async function generateViaImagen(
  ai: GoogleGenAI,
  model: string,
  prompt: string,
  opts: ImagenOpts,
): Promise<ImagenResult> {
  const res = await ai.models.generateImages({
    model,
    prompt,
    config: {
      numberOfImages: opts.numberOfImages ?? 1,
      aspectRatio: opts.aspectRatio ?? '1:1',
      negativePrompt: opts.negativePrompt,
    },
  });

  const img = res.generatedImages?.[0]?.image;
  if (!img?.imageBytes) throw new Error(`${model} returned no image bytes`);
  const mimeType = img.mimeType || 'image/png';
  return { dataUrl: `data:${mimeType};base64,${img.imageBytes}`, mimeType };
}

/**
 * Heuristic: phone/portrait → 9:16, landscape/banner → 16:9, otherwise square.
 */
export function inferAspectRatio(description: string): ImagenOpts['aspectRatio'] {
  const d = description.toLowerCase();
  if (/\b(portrait|phone|mobile|story|reel|9:16|vertical)\b/.test(d)) return '9:16';
  if (/\b(landscape|banner|wide|hero|16:9|youtube|thumbnail)\b/.test(d)) return '16:9';
  return '1:1';
}

/**
 * True when the env has enough config to call Vertex.
 * Cheap check used by callsites to decide whether to attempt image generation.
 */
export function hasVertexCredentials(): boolean {
  const raw = process.env.GOOGLE_CREDENTIALS_JSON;
  if (!raw) return false;
  if (process.env.VERTEX_AI_PROJECT_ID) return true;
  // The project_id lives inside the service-account JSON — derive it if needed.
  try { return !!(JSON.parse(raw) as { project_id?: string }).project_id; } catch { return false; }
}
