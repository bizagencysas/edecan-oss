// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  FyDesign VIDEO — MOTION / IDENTITY TOOLS (Higgsfield WAN-Animate parity)    ║
// ║                                                                              ║
// ║  Functions:                                                                  ║
// ║    animateStill      — still → motion video (WAN-Animate or image→video)     ║
// ║    recastCharacter   — swap a character into an existing video               ║
// ║    referenceToVideo  — persona-consistent text+ref → video                   ║
// ║    startEndFrame     — interpolate between two keyframe stills               ║
// ║                                                                              ║
// ║  All functions return the standard media result shape:                       ║
// ║    { url?, dataUrl?, file?, model, cost? }                                   ║
// ║                                                                              ║
// ║  Env overrides:                                                              ║
// ║    ANIMATE_MODEL              override default for animateStill              ║
// ║    RECAST_MODEL               override default for recastCharacter           ║
// ║    REFERENCE_VIDEO_MODEL      override default for referenceToVideo          ║
// ║    START_END_FRAME_MODEL      override default for startEndFrame             ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { muapiGenerate, generateVideo } from '../ai/muapi-client';
import { hostStillForMuapi } from '../ai/brand-image';
import { withFallback, chainFor } from '../model-router';
import applyMotion from '../presets/catalog';

// ── Shared result type ────────────────────────────────────────────────────────

export interface AnimateResult {
  url?: string;
  dataUrl?: string;
  file?: string;
  model: string;
  cost?: { amount_usd?: number } | null;
}

// ── Default model IDs (env-overridable) ──────────────────────────────────────

const DEFAULT_ANIMATE_MODEL        = 'wan2.2-animate';
const DEFAULT_RECAST_MODEL         = 'ai-video-face-swap';
const DEFAULT_REFERENCE_VIDEO_MODEL = 'veo3.1-reference-to-video';
const DEFAULT_START_END_FRAME_MODEL = 'sd-2-first-last-frame-fast';

function animateModel(override?: string): string {
  return override || process.env.ANIMATE_MODEL || DEFAULT_ANIMATE_MODEL;
}
function recastModel(override?: string): string {
  return override || process.env.RECAST_MODEL || DEFAULT_RECAST_MODEL;
}
function referenceVideoModel(override?: string): string {
  return override || process.env.REFERENCE_VIDEO_MODEL || DEFAULT_REFERENCE_VIDEO_MODEL;
}
function startEndModel(override?: string): string {
  return override || process.env.START_END_FRAME_MODEL || DEFAULT_START_END_FRAME_MODEL;
}

// ── Helper: normalise Muapi result to AnimateResult ──────────────────────────

function toResult(
  outputs: string[],
  model: string,
  cost?: { amount_usd?: number; amount_credits?: number } | null,
): AnimateResult {
  return { url: outputs[0] ?? undefined, model, cost: cost ?? null };
}

// ─────────────────────────────────────────────────────────────────────────────
// animateStill
// Animate a still image into a video clip.
//
// Paths:
//   A) drivingVideoUrl provided → WAN-Animate motion transfer (pose/motion from video)
//   B) motionKey only            → image→video via generateVideo with motion preset
//   C) neither                   → image→video with no motion hint
//
// stillSrc: data URL, file path, or HTTPS URL of the source still.
// ─────────────────────────────────────────────────────────────────────────────

export async function animateStill(
  stillSrc: string,
  opts: {
    motionKey?: string;
    drivingVideoUrl?: string;
    model?: string;
  } = {},
): Promise<AnimateResult> {
  const { motionKey, drivingVideoUrl, model } = opts;
  const endpoint = animateModel(model);

  try {
    const imageUrl = await hostStillForMuapi(stillSrc);

    // Path A: motion transfer — drive motion from a reference video (WAN-Animate).
    // wan2.2-animate requires: image_url (source character) + video_url (motion driver).
    if (drivingVideoUrl) {
      const result = await muapiGenerate(
        endpoint,
        {
          image_url: imageUrl,
          video_url: drivingVideoUrl,
        },
        { timeoutMs: 600_000, intervalMs: 5_000 },
      );
      return toResult(result.outputs, result.model, result.cost);
    }

    // Path B/C: image→video with optional motion preset.
    // applyMotion enriches the prompt with a camera motion clause if motionKey is set.
    const basePrompt = 'Cinematic animation of the still image, natural motion, photorealistic.';
    const motionPrompt = applyMotion(basePrompt, motionKey);

    // Pass through opts.model so the caller's explicit model choice (or env override
    // already resolved in `endpoint`) is honoured.  Previously this nulled the model
    // when endpoint === DEFAULT_ANIMATE_MODEL, causing generateVideo to silently
    // discard a legitimately configured model.
    const result = await generateVideo(motionPrompt, {
      imageUrl,
      model: opts.model || process.env.ANIMATE_MODEL || undefined,
    });

    return toResult(result.outputs, result.model, result.cost);
  } catch (err) {
    console.error('[animate] animateStill falló:', err instanceof Error ? err.message : String(err));
    throw err;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// recastCharacter
// Swap a character into an existing video (Higgsfield Recast / full-body swap).
//
// videoUrl:        HTTPS URL of the source video.
// characterRefUrl: HTTPS URL (or data URL) of the character reference image.
//                  Becomes image_url sent to the face-swap model.
// ─────────────────────────────────────────────────────────────────────────────

export async function recastCharacter(
  videoUrl: string,
  characterRefUrl: string,
  opts: {
    model?: string;
  } = {},
): Promise<AnimateResult> {
  const endpoint = recastModel(opts.model);

  // ai-video-face-swap fetches the video directly — local paths can't be reached.
  if (!videoUrl.startsWith('http://') && !videoUrl.startsWith('https://')) {
    throw new Error(
      '[animate] recastCharacter requiere una URL HTTP(S) pública para videoUrl. ' +
      'Los archivos locales no son accesibles por Muapi. ' +
      `Recibido: "${videoUrl.slice(0, 120)}"`
    );
  }

  try {
    // Resolve the character reference to a URL the model can fetch.
    // ai-video-face-swap requires: image_url (swap source) + video_url (target video).
    const imageUrl = await hostStillForMuapi(characterRefUrl);

    const result = await muapiGenerate(
      endpoint,
      {
        image_url: imageUrl,
        video_url: videoUrl,
      },
      { timeoutMs: 600_000, intervalMs: 5_000 },
    );

    return toResult(result.outputs, result.model, result.cost);
  } catch (err) {
    console.error('[animate] recastCharacter falló:', err instanceof Error ? err.message : String(err));
    throw err;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// referenceToVideo
// Generate a persona-consistent video from a reference image + text prompt.
// Analogous to Higgsfield's Soul ID / Veo reference-to-video flow.
//
// refImageSrc: data URL, file path, or HTTPS URL of the persona reference.
// prompt:      text describing the desired scene/action.
//
// Schema notes (verified 2026-06-14 via Muapi API):
//
//   veo3.1-reference-to-video
//     required: prompt, images_list (max 3)
//     optional: resolution ("720p"|"1080p"|"4k", default "720p")
//               duration  (enum [8], default 8)
//               generate_audio (boolean, default true)
//     NOTE: generate_audio defaults to true; set to false to avoid audio-pipeline
//     runtime failures ("Unknown error") when the veo audio backend is unavailable.
//
//   kling-o1-reference-to-video
//     required: prompt only
//     optional: images_list (max 7), aspect_ratio ("16:9"|"9:16"|"1:1", default "16:9")
//               duration (3–10, default 5), keep_original_sound
//
//   seedance-lite-reference-video
//     required: prompt, images_list (max 4)
//     optional: resolution ("480p"|"720p", default "480p")
//               duration (3–12, default 5)
//
//   wan2.1-reference-video
//     required: prompt, images_list (max 5)
//     optional: resolution ("480p"|"720p", default "480p")
//               aspect_ratio ("16:9"|"9:16", default "16:9")
//               duration (5 or 10, default 5)
//
// Strategy: withFallback over chainFor('reference-video') (CAPABILITY_CHAINS) —
// tries veo3.1-reference-to-video first, then kling-o1-reference-to-video,
// then seedance-lite-reference-video, then wan2.1-reference-video.
// Each model receives its correct body via buildRefVideoBody (schemas differ).
// If opts.model is set, that model is tried first (prepended to chain).
// ─────────────────────────────────────────────────────────────────────────────

/** Build the correct request body for a given reference-video model. */
function buildRefVideoBody(
  model: string,
  prompt: string,
  imageUrl: string,
  duration?: number,
): Record<string, unknown> {
  if (/veo/.test(model)) {
    // veo3.1-reference-to-video: duration must be 8 (only valid enum value).
    // Explicitly set resolution to avoid any defaults that might trigger edge cases.
    // Set generate_audio: false to avoid audio-pipeline runtime failures.
    return {
      prompt,
      images_list: [imageUrl],
      resolution: '720p',
      duration: 8,
      generate_audio: false,
    };
  }

  if (/kling-o1/.test(model)) {
    // kling-o1-reference-to-video: prompt is the only required field; images_list is optional.
    // Clamp duration to [3, 10] range (default 5).
    const dur = Math.min(10, Math.max(3, duration ?? 5));
    return {
      prompt,
      images_list: [imageUrl],
      aspect_ratio: '16:9',
      duration: dur,
    };
  }

  if (/seedance.*ref|seedance.*lite.*ref/.test(model)) {
    // seedance-lite-reference-video: required prompt + images_list.
    // Duration range [3, 12], resolution default "480p" (use 720p for better quality).
    const dur = Math.min(12, Math.max(3, duration ?? 5));
    return {
      prompt,
      images_list: [imageUrl],
      resolution: '720p',
      duration: dur,
    };
  }

  if (/wan2\.1.*ref|wan.*ref/.test(model)) {
    // wan2.1-reference-video: required prompt + images_list.
    // Duration must be 5 or 10 (step 5); resolution "480p"|"720p".
    const dur = (duration ?? 5) >= 8 ? 10 : 5;
    return {
      prompt,
      images_list: [imageUrl],
      resolution: '720p',
      aspect_ratio: '16:9',
      duration: dur,
    };
  }

  if (/gemini-omni/.test(model)) {
    // gemini-omni-image-to-video: uses image_urls array + resolution + aspect_ratio.
    // Duration snapped to nearest allowed [4, 6, 8, 10].
    const allowed = [4, 6, 8, 10];
    const want = duration ?? 8;
    const dur = allowed.reduce((b, v) => (Math.abs(v - want) < Math.abs(b - want) ? v : b), 8);
    return {
      prompt,
      image_urls: [imageUrl],
      resolution: '1080p',
      aspect_ratio: '16:9',
      duration: dur,
    };
  }

  // Generic fallback body for any unknown reference-video model
  return {
    prompt,
    images_list: [imageUrl],
    duration: duration ?? 5,
  };
}

export async function referenceToVideo(
  refImageSrc: string,
  prompt: string,
  opts: {
    model?: string;
    duration?: number;
  } = {},
): Promise<AnimateResult> {
  let imageUrl: string;
  try {
    imageUrl = await hostStillForMuapi(refImageSrc);
  } catch (err) {
    console.error('[animate] referenceToVideo: error al subir imagen de referencia:', err instanceof Error ? err.message : String(err));
    throw err;
  }

  // ── Build the display chain for logging ────────────────────────────────────
  // chainFor('reference-video') uses CAPABILITY_CHAINS (canonical order):
  //   veo3.1-reference-to-video → kling-o1-reference-to-video →
  //   seedance-lite-reference-video → wan2.1-reference-video
  // If the caller supplied opts.model, it is tried first (prepended to chain).
  const canonicalChain = chainFor('reference-video');
  const callerModel = opts.model ? referenceVideoModel(opts.model) : undefined;

  const displayChain: string[] = callerModel
    ? [callerModel, ...canonicalChain.filter((m) => m !== callerModel)]
    : canonicalChain;
  console.error(`[animate] referenceToVideo: cadena de fallback = [${displayChain.join(', ')}]`);

  // ── Helper: invoke muapiGenerate with the correct per-model body ────────────
  async function tryModel(model: string): Promise<import('../ai/muapi-client').MuapiResult> {
    const body = buildRefVideoBody(model, prompt, imageUrl, opts.duration);
    console.error(`[animate] referenceToVideo: intentando model='${model}' body=${JSON.stringify(body)}`);
    return muapiGenerate(model, body, { timeoutMs: 600_000, intervalMs: 5_000 });
  }

  // ── withFallback drives the retry loop ─────────────────────────────────────
  // If the caller specified a model, try it first (isolated withFallback call
  // with the rest of the chain skipped), then fall through to the full chain.
  // If no caller model, withFallback iterates the full canonical chain once.
  let result: import('../ai/muapi-client').MuapiResult;

  if (callerModel) {
    // Attempt callerModel first.
    // If callerModel is in the canonical chain, use withFallback with the rest skipped
    // so only that single model is tried in the first pass.
    // If it is NOT in the chain (custom override), try it directly, then fall through.
    const callerInChain = canonicalChain.includes(callerModel);
    try {
      if (callerInChain) {
        result = await withFallback('reference-video', tryModel, {
          skip: canonicalChain.filter((m) => m !== callerModel),
        });
      } else {
        // Custom model not in CAPABILITY_CHAINS — call directly.
        result = await tryModel(callerModel);
      }
    } catch {
      console.error(`[animate] referenceToVideo: modelo del caller '${callerModel}' falló, probando cadena canónica…`);
      // Fallback to the rest of the canonical chain (always skip callerModel to avoid retry).
      result = await withFallback('reference-video', tryModel, { skip: [callerModel] });
    }
  } else {
    // No caller model — iterate the full canonical chain via withFallback.
    result = await withFallback('reference-video', tryModel);
  }

  console.error(`[animate] referenceToVideo: éxito con model='${result.model}'`);
  return toResult(result.outputs, result.model, result.cost);
}

// ─────────────────────────────────────────────────────────────────────────────
// startEndFrame
// Interpolate between two keyframe stills with a prompt guiding the transition.
// Analogous to Higgsfield's Cinema Studio start/end-frame keyframing.
//
// startSrc: data URL, file path, or HTTPS URL of the first keyframe.
// endSrc:   data URL, file path, or HTTPS URL of the last keyframe.
// prompt:   text describing the interpolated motion / transition.
//
// sd-2-first-last-frame-fast schema: required [prompt, images_list].
// images_list = [firstUrl, lastUrl] — exactly two elements for start + end frame.
// ─────────────────────────────────────────────────────────────────────────────

export async function startEndFrame(
  startSrc: string,
  endSrc: string,
  prompt: string,
  opts: {
    model?: string;
    duration?: number;
  } = {},
): Promise<AnimateResult> {
  const endpoint = startEndModel(opts.model);
  const duration = opts.duration ?? 5;

  try {
    // Host both images in parallel for speed.
    const [startUrl, endUrl] = await Promise.all([
      hostStillForMuapi(startSrc),
      hostStillForMuapi(endSrc),
    ]);

    // sd-2-first-last-frame-fast requires: prompt (string) + images_list (array).
    // images_list: [firstUrl] = first frame only; [firstUrl, lastUrl] = first + last frame.
    const result = await muapiGenerate(
      endpoint,
      {
        prompt,
        images_list: [startUrl, endUrl],
        duration,
      },
      { timeoutMs: 600_000, intervalMs: 5_000 },
    );

    return toResult(result.outputs, result.model, result.cost);
  } catch (err) {
    console.error('[animate] startEndFrame falló:', err instanceof Error ? err.message : String(err));
    throw err;
  }
}
