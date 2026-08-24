// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  upscale.ts — final-step quality boost via Muapi (Topaz / Real-ESRGAN)      ║
// ║                                                                              ║
// ║  upscaleImage: 2×/4× image upscale (Topaz Gigapixel or fallback ESRGAN)     ║
// ║  upscaleVideo: video upscale (Topaz Video AI or fallback model)              ║
// ║                                                                              ║
// ║  Env:                                                                        ║
// ║    MUAPI_IMAGE_UPSCALE  image endpoint (default: topaz-image-upscale)        ║
// ║    MUAPI_VIDEO_UPSCALE  video endpoint (default: topaz-video-upscale)        ║
// ║                                                                              ║
// ║  Blueprint reference: Higgsfield "Image Upscaler" feature                   ║
// ║    — Topaz Gigapixel partnership, 2x/4x/8x/16x, Standard/HiFi/Generative   ║
// ║    — Replicate in FyDesign via Muapi topaz-image-upscale or ESRGAN fallback  ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { muapiGenerate } from './muapi-client';
import { hostStillForMuapi } from './brand-image';

/** Standard media result shape shared across FyDesign AI modules. */
export interface UpscaleResult {
  url?: string;
  dataUrl?: string;
  file?: string;
  /** Detected file extension (e.g. 'png', 'mp4') derived from the Content-Type header. */
  ext?: string;
  model: string;
  cost?: { amount_usd?: number } | null;
}

// ─── Shared helpers ───────────────────────────────────────────────────────────

/** Derive a file extension from a MIME type string. Returns '' if unknown. */
function mimeToExt(mime: string): string {
  const m = mime.toLowerCase().split(';')[0].trim();
  const map: Record<string, string> = {
    'image/png':  'png',
    'image/jpeg': 'jpg',
    'image/jpg':  'jpg',
    'image/webp': 'webp',
    'image/gif':  'gif',
    'image/avif': 'avif',
    'video/mp4':  'mp4',
    'video/webm': 'webm',
    'video/quicktime': 'mov',
    'video/x-msvideo': 'avi',
  };
  if (map[m]) return map[m];
  // Fallback: parse "image/X" → "X"
  const slash = m.indexOf('/');
  return slash !== -1 ? m.slice(slash + 1).split('+')[0] : '';
}

/**
 * Fetch a CDN URL, return its MIME type and a base-64 data URL.
 * Best-effort — logs to stderr and returns null on any failure.
 */
async function fetchAsDataUrl(
  url: string,
): Promise<{ mime: string; dataUrl: string } | null> {
  try {
    const res = await fetch(url);
    if (!res.ok) {
      console.error(`[upscale] No se pudo descargar el resultado (HTTP ${res.status}): ${url}`);
      return null;
    }
    const mime = (res.headers.get('content-type') || '').split(';')[0].trim() || 'application/octet-stream';
    const buf  = await res.arrayBuffer();
    const b64  = Buffer.from(buf).toString('base64');
    return { mime, dataUrl: `data:${mime};base64,${b64}` };
  } catch (err) {
    console.error('[upscale] Error al descargar el resultado como dataUrl:', err);
    return null;
  }
}

/**
 * Probe a URL's Content-Type via a HEAD request (cheap — no body download).
 * Best-effort — returns '' on any failure.
 */
async function probeContentType(url: string): Promise<string> {
  try {
    const res = await fetch(url, { method: 'HEAD' });
    return (res.headers.get('content-type') || '').split(';')[0].trim();
  } catch {
    return '';
  }
}

// ─── Image upscale ────────────────────────────────────────────────────────────

export interface UpscaleImageOpts {
  /** Upscale multiplier: 2, 4, 8, or 16. Default: 2. */
  scale?: number;
  /** Override the Muapi endpoint (defaults to MUAPI_IMAGE_UPSCALE env or 'topaz-image-upscale'). */
  model?: string;
}

/**
 * Upscale an image (data URL, http URL, file path, or Buffer) via Muapi.
 *
 * Primary model: topaz-image-upscale (Topaz Gigapixel — Higgsfield partnership).
 * Fallback model: ai-image-upscaler (Real-ESRGAN-grade, broadly available on Muapi).
 *
 * Never throws — on any failure logs to stderr and returns { url: '', model, cost: null }
 * so the caller can skip the upscale step gracefully.
 */
export async function upscaleImage(
  imageSrc: string | Buffer,
  opts: UpscaleImageOpts = {},
): Promise<UpscaleResult> {
  const primaryModel = opts.model || process.env.MUAPI_IMAGE_UPSCALE || 'topaz-image-upscale';
  const fallbackModel = 'ai-image-upscaler';
  const scale = opts.scale ?? 2;

  let image_url: string;
  try {
    image_url = await hostStillForMuapi(imageSrc);
  } catch (err) {
    console.error('[upscale] Error al preparar la URL de la imagen:', err);
    return { url: '', model: primaryModel, cost: null };
  }

  // Try primary model first, then fall back.
  for (const model of [primaryModel, fallbackModel]) {
    try {
      // topaz-image-upscale: props [image_url, upscale_factor]
      // ai-image-upscaler:   props [image_url] ONLY — no scale field accepted
      const body =
        model === fallbackModel
          ? { image_url }
          : { image_url, upscale_factor: scale };
      const result = await muapiGenerate(model, body);
      const url = result.outputs?.[0] ?? '';
      if (!url) {
        console.error(`[upscale] Modelo ${model} no devolvió outputs. Intentando siguiente.`);
        continue;
      }
      // Download the result so callers can save with the correct extension.
      const fetched = await fetchAsDataUrl(url);
      const dataUrl = fetched?.dataUrl;
      const ext     = fetched ? mimeToExt(fetched.mime) : undefined;
      return { url, dataUrl, ext, model, cost: result.cost ?? null };
    } catch (err) {
      console.error(`[upscale] Error con modelo "${model}":`, err);
      if (model === primaryModel) {
        console.error(`[upscale] Usando modelo de respaldo "${fallbackModel}"...`);
      }
    }
  }

  // Both models failed.
  console.error('[upscale] Todos los modelos de imagen fallaron. Devolviendo resultado vacío.');
  return { url: '', model: fallbackModel, cost: null };
}

// ─── Video upscale ────────────────────────────────────────────────────────────

export interface UpscaleVideoOpts {
  /**
   * Override the Muapi endpoint (defaults to MUAPI_VIDEO_UPSCALE env or 'topaz-video-upscale').
   */
  model?: string;
}

/**
 * Upscale a video via Muapi.
 *
 * NOTE: The video_url must be a publicly fetchable HTTP URL — local file paths
 * cannot be served directly to Muapi. If you have a local video file, upload it
 * to GCS (or another hosting service) first and pass the resulting URL here.
 * Passing a local path will result in a graceful error (logged, url: '').
 *
 * Primary model: topaz-video-upscale (Topaz Video AI).
 * Fallback model: ai-video-upscaler.
 *
 * Never throws — on any failure logs to stderr and returns { url: '', model, cost: null }.
 */
export async function upscaleVideo(
  fileOrUrl: string,
  opts: UpscaleVideoOpts = {},
): Promise<UpscaleResult> {
  const primaryModel = opts.model || process.env.MUAPI_VIDEO_UPSCALE || 'topaz-video-upscale';
  const fallbackModel = 'ai-video-upscaler';

  // Validate that we have a real URL (Muapi cannot reach local paths).
  if (!fileOrUrl.startsWith('http://') && !fileOrUrl.startsWith('https://')) {
    console.error(
      '[upscale] upscaleVideo requiere una URL HTTP(S) pública. ' +
      'Los archivos locales deben subirse a GCS u otro hosting antes de llamar a esta función. ' +
      `Recibido: "${fileOrUrl.slice(0, 120)}"`
    );
    return { url: '', model: primaryModel, cost: null };
  }

  const video_url = fileOrUrl;

  // Try primary model first, then fall back.
  for (const model of [primaryModel, fallbackModel]) {
    try {
      const result = await muapiGenerate(
        model,
        { video_url },
        { timeoutMs: 600_000, intervalMs: 5_000 },
      );
      const url = result.outputs?.[0] ?? '';
      if (!url) {
        console.error(`[upscale] Modelo ${model} no devolvió outputs de video. Intentando siguiente.`);
        continue;
      }
      // Probe extension via HEAD (no body download — videos can be large).
      const mime = await probeContentType(url);
      const ext  = mime ? mimeToExt(mime) : undefined;
      return { url, ext, model, cost: result.cost ?? null };
    } catch (err) {
      console.error(`[upscale] Error con modelo de video "${model}":`, err);
      if (model === primaryModel) {
        console.error(`[upscale] Usando modelo de respaldo "${fallbackModel}"...`);
      }
    }
  }

  // Both models failed.
  console.error('[upscale] Todos los modelos de video fallaron. Devolviendo resultado vacío.');
  return { url: '', model: fallbackModel, cost: null };
}
