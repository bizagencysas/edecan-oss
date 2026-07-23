// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  OpenAI image client — GPT Image builder                                    ║
// ║                                                                            ║
// ║  Contract:                                                                  ║
// ║    Auth: header  Authorization: Bearer $OPENAI_API_KEY                      ║
// ║    POST https://api.openai.com/v1/images/generations                        ║
// ║         body { model:$OPENAI_IMAGE_MODEL, prompt, size, n:1 }                ║
// ║         → { data: [{ b64_json }] }  →  data:image/png;base64,<b64>          ║
// ║                                                                            ║
// ║  Gated by hasOpenAI() — OPENAI_API_KEY is optional; absence is fine.        ║
// ║  RELATIVE import only (tsx does not resolve "@/").                          ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { enforceNoTextPrompt } from './imagen-client';

/** True iff an OpenAI API key is configured in the environment. */
export function hasOpenAI(): boolean {
  return !!process.env.OPENAI_API_KEY;
}

/** Alias actual por defecto, reemplazable sin tocar código para proveedores compatibles. */
export function getOpenAIImageModel(): string {
  return process.env.OPENAI_IMAGE_MODEL?.trim() || 'gpt-image-2';
}

interface OpenAIImageResponse {
  data?: Array<{ b64_json?: string; url?: string }>;
  error?: { message?: string; type?: string };
}

/**
 * Generate one image via OpenAI GPT Image and return it as a base64 data URL.
 *
 * @param prompt  The image description.
 * @param opts.size  Output size (default "1024x1024"; also "1536x1024", "1024x1536", "auto").
 * @returns       `{ dataUrl }` where dataUrl is `data:image/png;base64,<b64>`.
 * @throws        On missing key or any non-OK / malformed response.
 */
export async function generateGptImage(
  prompt: string,
  opts: { size?: string; allowText?: boolean } = {},
): Promise<{ dataUrl: string; model: string }> {
  const key = process.env.OPENAI_API_KEY;
  if (!key) {
    throw new Error('OPENAI_API_KEY is not set (add it to .env.local to enable provider="openai" images).');
  }

  // NO-TEXT POLICY: raster text can still be imperfect. Text belongs
  // in the CSS overlay. Opt out with { allowText: true }.
  const finalPrompt = opts.allowText ? prompt : enforceNoTextPrompt(prompt);

  const model = getOpenAIImageModel();
  const res = await fetch('https://api.openai.com/v1/images/generations', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${key}`,
    },
    body: JSON.stringify({
      model,
      prompt: finalPrompt,
      size: opts.size || '1024x1024',
      n: 1,
    }),
    signal: AbortSignal.timeout(300_000),
  });

  const text = await res.text();
  if (!res.ok) {
    let detail = text.slice(0, 400);
    try {
      const parsed = JSON.parse(text) as OpenAIImageResponse;
      if (parsed.error?.message) detail = parsed.error.message;
    } catch {
      /* keep raw text */
    }
    throw new Error(`OpenAI image error ${res.status}: ${detail}`);
  }

  let data: OpenAIImageResponse;
  try {
    data = JSON.parse(text) as OpenAIImageResponse;
  } catch {
    throw new Error(`OpenAI image: non-JSON response: ${text.slice(0, 200)}`);
  }

  const b64 = data.data?.[0]?.b64_json;
  if (!b64) {
    throw new Error(`OpenAI image: response missing data[0].b64_json (${JSON.stringify(data).slice(0, 200)})`);
  }

  return { dataUrl: `data:image/png;base64,${b64}`, model };
}
