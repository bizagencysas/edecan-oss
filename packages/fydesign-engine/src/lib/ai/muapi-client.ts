// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Muapi client — one API, 50+ image & 40+ video models (Flux, Kling, Veo…)   ║
// ║                                                                            ║
// ║  Contract (https://muapi.ai/docs):                                         ║
// ║    Auth:    header  x-api-key: $MUAPI_API_KEY                              ║
// ║    Submit:  POST https://api.muapi.ai/api/v1/{model}  → { request_id }      ║
// ║    Poll:    GET  https://api.muapi.ai/api/v1/predictions/{id}/result        ║
// ║             → { status, outputs: [url] }  (queued|processing|completed|failed)║
// ║    Sandbox: body { is_test: true } → provider test response                 ║
// ║                                                                            ║
// ║  Env:                                                                       ║
// ║    MUAPI_API_KEY      your key (muapi.ai → API keys)                        ║
// ║    MUAPI_IMAGE_MODEL  default image endpoint (default: flux-2-pro)          ║
// ║    MUAPI_VIDEO_MODEL  default video endpoint (default: kling-v2.5-turbo-pro) ║
// ║    MUAPI_SANDBOX      '1' to send is_test (provider mock, for wiring tests)  ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { enforceNoTextPrompt, NO_TEXT_NEGATIVE_PROMPT } from './imagen-client';

const BASE = 'https://api.muapi.ai/api/v1';

export function hasMuapi(): boolean {
  return !!process.env.MUAPI_API_KEY || !!process.env.MUAPI_API_KEY2;
}

const sandbox = (): boolean => /^(1|true|yes)$/i.test(process.env.MUAPI_SANDBOX || '');

function apiKey(): string {
  // In sandbox mode prefer the dedicated test key (MUAPI_API_KEY2) so real
  // credits are never touched while wiring is verified.
  const k = (sandbox() && process.env.MUAPI_API_KEY2) ? process.env.MUAPI_API_KEY2 : process.env.MUAPI_API_KEY;
  if (!k) throw new Error('MUAPI_API_KEY no está configurada (créala en muapi.ai y ponla en .env.local)');
  return k;
}
const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

interface SubmitResponse {
  request_id?: string;
  status?: string;
  outputs?: string[];
  /** gemini-omni-audio returns the created voice profile id directly. */
  audio_id?: string;
  cost?: { amount_usd?: number; amount_credits?: number };
}
interface ResultResponse {
  id?: string;
  status?: string;
  outputs?: string[];
  cost?: { amount_usd?: number; amount_credits?: number };
  error?: string;
}
export interface MuapiResult {
  outputs: string[];
  cost?: { amount_usd?: number; amount_credits?: number };
  requestId?: string;
  model: string;
}

async function submit(endpoint: string, body: Record<string, unknown>): Promise<SubmitResponse> {
  const payload = sandbox() ? { ...body, is_test: true } : body;
  const res = await fetch(`${BASE}/${endpoint}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'x-api-key': apiKey() },
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(60_000),
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`Muapi submit ${endpoint} → ${res.status}: ${text.slice(0, 400)}`);
  let data: SubmitResponse;
  try { data = JSON.parse(text); } catch { throw new Error(`Muapi submit: respuesta no-JSON: ${text.slice(0, 200)}`); }
  return data;
}

async function poll(requestId: string, timeoutMs: number, intervalMs: number): Promise<ResultResponse> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const res = await fetch(`${BASE}/predictions/${requestId}/result`, {
      headers: { 'x-api-key': apiKey() },
      signal: AbortSignal.timeout(30_000),
    });
    const text = await res.text();
    if (res.ok) {
      let data: ResultResponse;
      try { data = JSON.parse(text); } catch { data = {}; }
      const status = (data.status || '').toLowerCase();
      if (status === 'completed') return data;
      if (status === 'failed' || status === 'cancelled') {
        throw new Error(`Muapi job ${status}: ${data.error || JSON.stringify(data).slice(0, 300)}`);
      }
    } else if (res.status !== 404 && res.status !== 202) {
      throw new Error(`Muapi poll → ${res.status}: ${text.slice(0, 300)}`);
    }
    await sleep(intervalMs);
  }
  throw new Error(`Muapi: timeout esperando el resultado (${Math.round(timeoutMs / 1000)}s)`);
}

/** Submit a generation job and wait for the output URL(s). */
export async function muapiGenerate(
  endpoint: string,
  body: Record<string, unknown>,
  opts: { timeoutMs?: number; intervalMs?: number } = {},
): Promise<MuapiResult> {
  const sub = await submit(endpoint, body);
  // Some models (and sandbox) may return the result immediately.
  if ((sub.status || '').toLowerCase() === 'completed' && Array.isArray(sub.outputs)) {
    return { outputs: sub.outputs, cost: sub.cost, requestId: sub.request_id, model: endpoint };
  }
  if (!sub.request_id) throw new Error(`Muapi: sin request_id (${JSON.stringify(sub).slice(0, 200)})`);
  const result = await poll(sub.request_id, opts.timeoutMs ?? 300_000, opts.intervalMs ?? 3_000);
  return { outputs: result.outputs || [], cost: result.cost || sub.cost, requestId: sub.request_id, model: endpoint };
}

/** Text→image (or image→image with imageUrl). Default model: flux-dev. */
export async function generateImage(
  prompt: string,
  opts: { model?: string; aspectRatio?: string; imageUrl?: string; extra?: Record<string, unknown>; allowText?: boolean } = {},
): Promise<MuapiResult> {
  const endpoint = opts.model || process.env.MUAPI_IMAGE_MODEL || 'flux-2-pro';
  // NO-TEXT POLICY: same rule as the Vertex chokepoint — image models hallucinate
  // garbage text. Bake the prohibition into the prompt + pass a negative_prompt
  // (most Muapi image models accept one). Opt out with { allowText: true }.
  const finalPrompt = opts.allowText ? prompt : enforceNoTextPrompt(prompt);
  const negativeExtra = opts.allowText ? {} : { negative_prompt: NO_TEXT_NEGATIVE_PROMPT };
  return muapiGenerate(endpoint, {
    prompt: finalPrompt,
    aspect_ratio: opts.aspectRatio || '1:1',
    ...negativeExtra,
    ...(opts.imageUrl ? { image_url: opts.imageUrl } : {}),
    ...(opts.extra || {}),
  });
}

/**
 * GPT Image 2 (OpenAI, via Muapi) — text→image, or image→image when refUrls are given.
 * Verified live schema: gpt-image-2-text-to-image needs { prompt }; gpt-image-2-image-to-image
 * needs { prompt, images_list:[http url] }. Both accept aspect_ratio, resolution (1K|2K|4K),
 * quality (low|medium|high). Result envelope returns outputs:[image_url] like every model.
 */
export async function generateGptImage2(
  prompt: string,
  opts: { refUrls?: string[]; aspect?: string; resolution?: '1K' | '2K' | '4K'; quality?: 'low' | 'medium' | 'high'; allowText?: boolean } = {},
): Promise<MuapiResult> {
  const refs = (opts.refUrls || []).filter((u) => /^https?:\/\//i.test(u));
  const endpoint = refs.length ? 'gpt-image-2-image-to-image' : 'gpt-image-2-text-to-image';
  const body: Record<string, unknown> = {
    prompt: opts.allowText ? prompt : enforceNoTextPrompt(prompt),
    aspect_ratio: ['auto', '1:1', '16:9', '9:16', '4:3', '3:4'].includes(opts.aspect ?? '') ? opts.aspect : 'auto',
    resolution: ['1K', '2K', '4K'].includes(opts.resolution ?? '') ? opts.resolution : '2K',
    quality: ['low', 'medium', 'high'].includes(opts.quality ?? '') ? opts.quality : 'high',
  };
  if (refs.length) body.images_list = refs.slice(0, 6);
  return muapiGenerate(endpoint, body, { timeoutMs: 300_000, intervalMs: 4_000 });
}

/** Snap a requested duration to the nearest value a given model accepts. */
function snapVideoDuration(endpoint: string, sec?: number): number {
  const want = sec ?? 5;
  // Allowed-duration sets per current-gen model family.
  const allowed = /veo/.test(endpoint) ? [8]          // Veo 3.1/4 only accept 8s
    : /gemini-omni/.test(endpoint) ? [4, 6, 8, 10]
    : /kling|sora/.test(endpoint) ? [5, 10]
    : [4, 5, 6, 8, 10]; // seedance / hailuo / wan / ltx are flexible
  return allowed.reduce((best, v) => (Math.abs(v - want) < Math.abs(best - want) ? v : best), allowed[0]);
}

/**
 * Text→video (or image→video with imageUrl). Default: Kling v2.5 Turbo Pro (current gen).
 * Model-aware: Gemini Omni takes an `image_urls` array + `resolution`/`aspect_ratio`;
 * other i2v models take `image_url` and derive aspect from the source still.
 */
export async function generateVideo(
  prompt: string,
  opts: { model?: string; duration?: number; imageUrl?: string; imageUrls?: string[]; extra?: Record<string, unknown> } = {},
): Promise<MuapiResult> {
  // Gemini Omni accepts 1–7 reference images (identity), so prefer imageUrls when given.
  const imgs = opts.imageUrls?.length ? opts.imageUrls : (opts.imageUrl ? [opts.imageUrl] : []);
  const endpoint = opts.model || process.env.MUAPI_VIDEO_MODEL
    || (imgs.length ? 'kling-v2.5-turbo-pro-i2v' : 'kling-v2.5-turbo-pro-t2v');
  const isOmni = /gemini-omni/.test(endpoint);
  const aspect = (opts.extra?.aspect_ratio as string | undefined);

  // Gemini Omni is a remote Google service that can ONLY fetch http(s) image_urls —
  // a data: URL (hostStillForMuapi's fallback when GCS is unconfigured) is rejected
  // SERVER-SIDE after the job is charged. Fail fast BEFORE spending money.
  if (isOmni && imgs.length && imgs.some((u) => !/^https?:\/\//i.test(u))) {
    throw new Error(
      'Gemini Omni requiere URLs http(s) para image_urls, no data URLs. ' +
      'Configura GCS (o un CDN público) para subir las fotos de referencia antes de generar.',
    );
  }

  // Gemini Omni only accepts resolution in [720p, 1080p, 4k]; anything else 422s
  // server-side AFTER the job is charged. Clamp to a valid value.
  const OMNI_RES = ['720p', '1080p', '4k'];
  const clampOmniRes = (r: unknown) => (typeof r === 'string' && OMNI_RES.includes(r) ? r : '1080p');
  const body: Record<string, unknown> = { prompt, duration: snapVideoDuration(endpoint, opts.duration) };
  if (imgs.length) {
    if (isOmni) { body.image_urls = imgs; body.resolution = clampOmniRes(opts.extra?.resolution); }
    else body.image_url = imgs[0];
  }
  // Gemini Omni honors aspect_ratio; most i2v models derive it from the still, so
  // only forward extras to Omni to avoid 400s elsewhere. Omni ONLY accepts
  // '16:9' or '9:16' (square/other → 422), so clamp and set it LAST so the raw
  // aspect_ratio in `extra` can't override the clamped value. resolution is clamped
  // the same way (an invalid value in `extra` would otherwise survive Object.assign).
  if (isOmni) {
    const omniAspect = /16:9|4:3|landscape/i.test(aspect || '') ? '16:9' : '9:16';
    const extra = { ...(opts.extra || {}) };
    delete (extra as Record<string, unknown>).aspect_ratio;
    if ('resolution' in extra) delete (extra as Record<string, unknown>).resolution;
    Object.assign(body, extra);
    body.aspect_ratio = omniAspect;
    body.resolution = clampOmniRes(body.resolution);
  }
  return muapiGenerate(endpoint, body, { timeoutMs: 600_000, intervalMs: 5_000 });
}

/**
 * DIRECT mode — ONE prompt (+ optional reference images like a logo) straight to a SINGLE
 * video model that generates the whole thing end-to-end. No Opus director, no keyframe→
 * animate stitch, no manual compositing. The model integrates the reference itself (e.g.
 * a logo onto a phone) — the way Gemini/Kling/Seedance natively work.
 *
 * model: 'seedance' (Seedance 2.0) | 'omni' (Gemini Omni) | 'kling' (Kling v3).
 * With refs → image/reference-to-video; without → text-to-video.
 */
export async function generateVideoDirect(
  prompt: string,
  opts: { model?: 'seedance' | 'omni' | 'kling'; refUrls?: string[]; aspect?: string; duration?: number; resolution?: string } = {},
): Promise<MuapiResult> {
  const model = opts.model || 'seedance';
  const rawRefs = opts.refUrls || [];
  // Gemini Omni can ONLY fetch http(s) image_urls (a data: URL is rejected server-side).
  // Fail LOUD instead of silently dropping the ref and charging for text-to-video.
  if (model === 'omni' && rawRefs.length && rawRefs.some((u) => !/^https?:\/\//i.test(u))) {
    throw new Error('Gemini Omni requiere URLs http(s) para image_urls (no data URLs). Configura GCS o un CDN público para hostear la referencia.');
  }
  const refs = rawRefs.filter((u) => /^https?:\/\//i.test(u));
  const hasRef = refs.length > 0;
  const aspect = opts.aspect || '9:16';
  const snap = (sec: number | undefined, set: number[], def: number) =>
    (set.includes(sec ?? -1) ? (sec as number) : (set.reduce((b, v) => (Math.abs(v - (sec ?? def)) < Math.abs(b - (sec ?? def)) ? v : b), set[0])));

  let endpoint: string;
  const body: Record<string, unknown> = { prompt };

  if (model === 'omni') {
    endpoint = hasRef ? 'gemini-omni-image-to-video' : 'gemini-omni-text-to-video';
    body.aspect_ratio = /16:9|landscape/i.test(aspect) ? '16:9' : '9:16';
    // Omni only accepts [720p, 1080p, 4k]; an invalid value 422s after the charge.
    body.resolution = ['720p', '1080p', '4k'].includes(opts.resolution ?? '') ? opts.resolution : '1080p';
    body.duration = snap(opts.duration, [4, 6, 8, 10], 8);
    if (hasRef) body.image_urls = refs.slice(0, 7);
  } else if (model === 'kling') {
    endpoint = hasRef ? 'kling-o1-reference-to-video' : 'kling-v3.0-pro-text-to-video';
    body.aspect_ratio = /16:9/.test(aspect) ? '16:9' : /1:1/.test(aspect) ? '1:1' : '9:16';
    body.duration = snap(opts.duration, [5, 10], 5);
    if (hasRef) body.images_list = refs.slice(0, 7);
  } else {
    // Seedance 2.0 (endpoint name is seedance-v2.0-*, NOT sd-2-*)
    endpoint = hasRef ? 'seedance-v2.0-i2v' : 'seedance-v2.0-t2v';
    body.aspect_ratio = /16:9/.test(aspect) ? '16:9' : /4:3/.test(aspect) ? '4:3' : /3:4/.test(aspect) ? '3:4' : '9:16';
    body.duration = snap(opts.duration, [5, 10, 15], 5);
    body.quality = 'high';
    // Seedance 2.0 i2v accepts up to 4 reference images (matches the Seedance reference family);
    // extra refs are intentionally dropped.
    if (hasRef) body.images_list = refs.slice(0, 4);
  }
  return muapiGenerate(endpoint, body, { timeoutMs: 600_000, intervalMs: 5_000 });
}

/**
 * IDENTITY → NEW SCENE: take the user's real face (faceUrl) and GENERATE a brand-new
 * still of that SAME person in a new scene described by `prompt` (PuLID face-injection /
 * character-reference). This is Stage 1 of the talking pipeline — the user wants their
 * face used to CREATE a new video, not their exact selfie puppeted. Returns the new
 * image URL in outputs[0]. Per-model extras handled (ideogram needs style/render_speed).
 */
export async function generateIdentityImage(opts: {
  faceUrl: string;
  prompt: string;
  aspect?: string;
  model?: string;
}): Promise<MuapiResult> {
  const model = opts.model || 'flux-pulid';
  const body: Record<string, unknown> = {
    prompt: opts.prompt,
    image_url: opts.faceUrl,
    aspect_ratio: opts.aspect || '9:16',
  };
  if (/ideogram-character/.test(model)) { body.style = 'Realistic'; body.render_speed = 'Quality'; }
  return muapiGenerate(model, body, { timeoutMs: 180_000, intervalMs: 3_000 });
}

/**
 * Talking-avatar / lip-sync: animate a REAL portrait photo (image_url) to speak an
 * audio clip (audio_url), lip-synced. This is the path for "make my real uploaded
 * person talk" — these models (LTX/Kling-avatar/Wan/InfiniteTalk) have NO Google
 * "prominent people" filter (unlike Gemini Omni, which rejects real faces).
 * resolution is forwarded only to models that accept it, clamped to each model's max.
 */
export async function generateTalkingVideo(opts: {
  model: string;
  imageUrl: string;
  audioUrl: string;
  prompt?: string;
  resolution?: string;
}): Promise<MuapiResult> {
  const body: Record<string, unknown> = { image_url: opts.imageUrl, audio_url: opts.audioUrl };
  if (opts.prompt) body.prompt = opts.prompt;
  if (opts.resolution && /ltx|wan|infinitetalk/.test(opts.model)) {
    // Validate against each model's enum: only ltx-2.x accepts 1080p; wan/infinitetalk
    // cap at 720p. Any unsupported value (4k, 2160p, …) falls back to a safe 720p so we
    // never 400/422 on a paid job.
    const supported = /ltx-2/.test(opts.model) ? ['480p', '720p', '1080p'] : ['480p', '720p'];
    body.resolution = supported.includes(opts.resolution) ? opts.resolution : '720p';
  }
  // kling-v*-avatar-* take no resolution param — omit it (an extra field can 400).
  return muapiGenerate(opts.model, body, { timeoutMs: 600_000, intervalMs: 5_000 });
}

/**
 * Create a reusable Gemini Omni voice profile (timbre + style + language) and return
 * its `audio_id` for use in `audio_ids` of compatible video generations.
 *
 * NOTE: the gemini-omni-audio result has NO `status` field — it returns `{ audio_id }`
 * directly — so we poll for the presence of `audio_id` rather than a "completed" status
 * (which is why muapiGenerate can't be reused here). Returns null on any failure so the
 * caller can fall back to a known-good voice.
 */
export async function createOmniVoiceProfile(opts: {
  baseVoice?: string;
  name: string;
  description: string;
  example?: string;
}): Promise<string | null> {
  try {
    const sub = await submit('gemini-omni-audio', {
      audio_id: opts.baseVoice || 'aoede',
      name: opts.name.slice(0, 200),
      voice_description: opts.description.slice(0, 20_000),
      ...(opts.example ? { example_dialogue: opts.example.slice(0, 120) } : {}),
    });
    if (sub.audio_id) return String(sub.audio_id);
    if (!sub.request_id) return null;

    const start = Date.now();
    while (Date.now() - start < 60_000) {
      const res = await fetch(`${BASE}/predictions/${sub.request_id}/result`, {
        headers: { 'x-api-key': apiKey() },
        signal: AbortSignal.timeout(30_000),
      });
      if (res.ok) {
        const data = (await res.json().catch(() => ({}))) as { audio_id?: string; status?: string };
        if (data.audio_id) return String(data.audio_id);
        if ((data.status || '').toLowerCase() === 'failed') return null;
      } else if (res.status !== 404 && res.status !== 202) {
        // Permanent error (401/429/5xx): fail fast → caller uses the fallback voice
        // instead of stalling the whole 60s window on a hopeless poll.
        await res.text().catch(() => '');
        return null;
      }
      await sleep(2_000);
    }
    return null;
  } catch {
    return null;
  }
}
