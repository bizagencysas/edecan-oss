// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  fal.ai client — Flux, SDXL, ideogram, nano-banana… via fal.ai's REST API   ║
// ║                                                                            ║
// ║  Contract (verified against https://fal.ai/docs, jul-2026):                ║
// ║    Auth:     header  Authorization: Key $FAL_KEY                            ║
// ║    Sync:     POST https://fal.run/{model-id}              → result inline  ║
// ║    Queue:    POST https://queue.fal.run/{model-id}          → { request_id, ║
// ║               response_url, status_url, cancel_url }                        ║
// ║    Status:   GET  https://queue.fal.run/{model-id}/requests/{id}/status     ║
// ║               → { status: IN_QUEUE|IN_PROGRESS|COMPLETED, ... }             ║
// ║    Result:   GET  https://queue.fal.run/{model-id}/requests/{id}            ║
// ║               → model-specific body, images at result.images[].url         ║
// ║                                                                            ║
// ║  We always go through queue.fal.run (never the fal.run shortcut): it's the ║
// ║  same auth/model-id/body shape, but adds retries + works uniformly for     ║
// ║  slow (Flux Pro, ideogram) and fast (Flux schnell) models alike — the      ║
// ║  sync fal.run path explicitly does NOT retry on failure per fal's docs.    ║
// ║                                                                            ║
// ║  FAL_KEY is stored as "key_id:key_secret" (one string) — sent as-is after  ║
// ║  "Key ". Gated by hasFal(); absence is fine (provider stays unavailable).  ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

const QUEUE_BASE = 'https://queue.fal.run';

export function hasFal(): boolean {
  return !!process.env.FAL_KEY;
}

function apiKey(): string {
  const k = process.env.FAL_KEY;
  if (!k) throw new Error('FAL_KEY no está configurada (créala en fal.ai/dashboard/keys y ponla en .env.local)');
  return k;
}

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

interface FalSubmitResponse {
  request_id?: string;
  response_url?: string;
  status_url?: string;
  cancel_url?: string;
  queue_position?: number;
}
interface FalStatusResponse {
  status?: 'IN_QUEUE' | 'IN_PROGRESS' | 'COMPLETED';
  queue_position?: number;
  error?: string;
}
// fal.ai result bodies are model-specific but every image model returns an
// `images` array of { url, width?, height?, content_type? } (fal's shared
// image-output convention across Flux/SDXL/ideogram/nano-banana/etc).
interface FalResultResponse {
  images?: Array<{ url?: string; width?: number; height?: number }>;
  image?: { url?: string };
  detail?: string; // fal's error body shape on 4xx/5xx
}

export interface FalResult {
  outputs: string[];
  model: string;
  requestId?: string;
}

async function submit(modelId: string, body: Record<string, unknown>): Promise<FalSubmitResponse> {
  const res = await fetch(`${QUEUE_BASE}/${modelId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Key ${apiKey()}` },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(60_000),
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`fal.ai submit ${modelId} → ${res.status}: ${text.slice(0, 400)}`);
  try { return JSON.parse(text) as FalSubmitResponse; }
  catch { throw new Error(`fal.ai submit: respuesta no-JSON: ${text.slice(0, 200)}`); }
}

async function pollStatus(statusUrl: string, timeoutMs: number, intervalMs: number): Promise<void> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const res = await fetch(`${statusUrl}?logs=0`, {
      headers: { Authorization: `Key ${apiKey()}` },
      signal: AbortSignal.timeout(30_000),
    });
    const text = await res.text();
    if (res.ok) {
      let data: FalStatusResponse;
      try { data = JSON.parse(text); } catch { data = {}; }
      if (data.status === 'COMPLETED') return;
      // IN_QUEUE / IN_PROGRESS → keep polling.
    } else if (res.status !== 202) {
      throw new Error(`fal.ai status → ${res.status}: ${text.slice(0, 300)}`);
    }
    await sleep(intervalMs);
  }
  throw new Error(`fal.ai: timeout esperando el resultado (${Math.round(timeoutMs / 1000)}s)`);
}

async function fetchResult(responseUrl: string): Promise<FalResultResponse> {
  const res = await fetch(responseUrl, {
    headers: { Authorization: `Key ${apiKey()}` },
    signal: AbortSignal.timeout(30_000),
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`fal.ai result → ${res.status}: ${text.slice(0, 400)}`);
  try { return JSON.parse(text) as FalResultResponse; }
  catch { throw new Error(`fal.ai result: respuesta no-JSON: ${text.slice(0, 200)}`); }
}

/** Submit a generation job to fal.ai's queue and wait for the output URL(s). */
export async function falGenerate(
  modelId: string,
  body: Record<string, unknown>,
  opts: { timeoutMs?: number; intervalMs?: number } = {},
): Promise<FalResult> {
  const sub = await submit(modelId, body);
  if (!sub.request_id || !sub.status_url || !sub.response_url) {
    throw new Error(`fal.ai: respuesta de submit incompleta (${JSON.stringify(sub).slice(0, 200)})`);
  }
  await pollStatus(sub.status_url, opts.timeoutMs ?? 300_000, opts.intervalMs ?? 3_000);
  const result = await fetchResult(sub.response_url);
  const outputs = (result.images || []).map((i) => i.url).filter((u): u is string => !!u);
  if (!outputs.length && result.image?.url) outputs.push(result.image.url);
  if (!outputs.length) throw new Error(`fal.ai: sin imágenes en el resultado (${JSON.stringify(result).slice(0, 200)})`);
  return { outputs, model: modelId, requestId: sub.request_id };
}

/** Map our platform-agnostic aspect strings to each model family's accepted enum. */
function falImageSize(aspect: string): string {
  // fal's Flux family takes named `image_size` presets (not raw aspect strings).
  if (aspect === '16:9') return 'landscape_16_9';
  if (aspect === '9:16') return 'portrait_16_9';
  if (aspect === '4:3') return 'landscape_4_3';
  if (aspect === '3:4') return 'portrait_4_3';
  return 'square_hd';
}

/**
 * Text→image via fal.ai. Default model: flux/dev (good quality/cost balance).
 * Other useful ids: fal-ai/flux/schnell (fast/cheap), fal-ai/flux-pro/v1.1-ultra
 * (premium), fal-ai/ideogram/v3 (best text-in-image), fal-ai/nano-banana-pro,
 * fal-ai/recraft/v4/pro/text-to-image, fal-ai/qwen-image-2512.
 */
export async function generateFalImage(
  prompt: string,
  opts: { model?: string; aspectRatio?: string; imageUrl?: string; extra?: Record<string, unknown> } = {},
): Promise<FalResult> {
  const modelId = opts.model || process.env.FAL_IMAGE_MODEL || 'fal-ai/flux/dev';
  const body: Record<string, unknown> = {
    prompt,
    image_size: falImageSize(opts.aspectRatio || '1:1'),
    ...(opts.imageUrl ? { image_url: opts.imageUrl } : {}),
    ...(opts.extra || {}),
  };
  return falGenerate(modelId, body, { timeoutMs: 300_000, intervalMs: 3_000 });
}
