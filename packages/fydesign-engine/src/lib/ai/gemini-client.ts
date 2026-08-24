// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Unified Gemini Client                                                     ║
// ║  - Pro:   Creative Director (planner) + Visual Critic (vision)             ║
// ║  - Flash: HTML/code generation (parallel, fast, cheap)                     ║
// ║                                                                            ║
// ║  Auth resolution order (first match wins):                                 ║
// ║    1. Vertex AI (service account) — if VERTEX_AI_PROJECT_ID +              ║
// ║       GOOGLE_CREDENTIALS_JSON are set. Unified billing on GCP.             ║
// ║    2. AI Studio (api key) — if GEMINI_API_KEY is set. Cheaper SKU.         ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { GoogleGenAI, type Content, type GenerateContentConfig } from '@google/genai';

export const GEMINI_PRO = 'gemini-2.5-pro';
export const GEMINI_FLASH = 'gemini-2.5-flash';

let _client: GoogleGenAI | null = null;
let _mode: 'vertex' | 'aistudio' | null = null;

function client(): GoogleGenAI {
  if (_client) return _client;

  const project = process.env.VERTEX_AI_PROJECT_ID;
  const rawCreds = process.env.GOOGLE_CREDENTIALS_JSON;

  // Prefer Vertex when configured — unified billing + same SDK calls.
  if (project && rawCreds) {
    let credentials: Record<string, unknown>;
    try {
      credentials = JSON.parse(rawCreds);
    } catch {
      throw new Error('GOOGLE_CREDENTIALS_JSON is not valid JSON');
    }
    _client = new GoogleGenAI({
      vertexai: true,
      project,
      location: process.env.VERTEX_AI_LOCATION || 'us-central1',
      googleAuthOptions: { credentials },
    });
    _mode = 'vertex';
    return _client;
  }

  // Fallback: AI Studio API key.
  const apiKey = process.env.GEMINI_API_KEY;
  if (apiKey) {
    _client = new GoogleGenAI({ apiKey });
    _mode = 'aistudio';
    return _client;
  }

  throw new Error(
    'No Gemini auth configured. Set either VERTEX_AI_PROJECT_ID + GOOGLE_CREDENTIALS_JSON (Vertex) or GEMINI_API_KEY (AI Studio).',
  );
}

export function geminiAuthMode(): 'vertex' | 'aistudio' | 'none' {
  if (_mode) return _mode;
  if (process.env.VERTEX_AI_PROJECT_ID && process.env.GOOGLE_CREDENTIALS_JSON) return 'vertex';
  if (process.env.GEMINI_API_KEY) return 'aistudio';
  return 'none';
}

export function hasGeminiAuth(): boolean {
  return geminiAuthMode() !== 'none';
}

export interface GeminiCallOpts {
  model?: string;
  system?: string;
  temperature?: number;
  maxTokens?: number;
  json?: boolean;
  /** Optional inline image (base64, no data: prefix) for vision calls */
  image?: { mimeType: string; data: string };
  /** Optional multiple images for multi-image vision calls (logo + screenshots) */
  images?: { mimeType: string; data: string }[];
}

/**
 * Plain text call. Returns the raw string response.
 * Throws on API error so callers can decide retry/fallback policy.
 */
export async function callGemini(prompt: string, opts: GeminiCallOpts = {}): Promise<string> {
  const ai = client();
  const model = opts.model ?? GEMINI_FLASH;

  const userParts: Content['parts'] = [{ text: prompt }];
  // Add single image if provided
  if (opts.image) {
    userParts.unshift({ inlineData: { mimeType: opts.image.mimeType, data: opts.image.data } });
  }
  // Add multiple images if provided (logo, screenshots, etc.)
  if (opts.images && opts.images.length > 0) {
    for (const img of opts.images) {
      userParts.unshift({ inlineData: { mimeType: img.mimeType, data: img.data } });
    }
  }

  const config: GenerateContentConfig = {
    temperature: opts.temperature ?? 1.0,
    maxOutputTokens: opts.maxTokens ?? 65000,
  };
  if (opts.system) config.systemInstruction = opts.system;
  if (opts.json) config.responseMimeType = 'application/json';

  const res = await ai.models.generateContent({
    model,
    contents: [{ role: 'user', parts: userParts }],
    config,
  });

  const text = res.text;
  if (!text) throw new Error(`Gemini ${model} returned empty response`);

  const finishReason = res.candidates?.[0]?.finishReason;
  if (finishReason && finishReason !== 'STOP') {
    console.warn(`[Gemini] ${model} finishReason=${finishReason} — output may be incomplete`);
  }
  return text;
}

/**
 * JSON-mode call. Parses the response. Returns null on parse failure (caller decides fallback).
 */
export async function callGeminiJSON<T = unknown>(
  prompt: string,
  opts: GeminiCallOpts = {},
): Promise<T | null> {
  let raw: string;
  try {
    raw = await callGemini(prompt, { ...opts, json: true });
  } catch (e) {
    console.warn('[Gemini] API call failed:', e instanceof Error ? e.message : e);
    return null;
  }
  let parsed: T | null = null;
  try {
    parsed = JSON.parse(raw) as T;
  } catch (e) {
    console.warn('[Gemini] JSON parse failed:', e instanceof Error ? e.message : e, 'raw:', raw.slice(0, 200));
    return null;
  }
  return parsed;
}

/**
 * Vision call — pass a screenshot + prompt, get back a critique/description.
 * Defaults to Pro because vision reasoning quality matters more than speed here.
 */
export async function callGeminiVision(
  prompt: string,
  image: { mimeType: string; data: string },
  opts: Omit<GeminiCallOpts, 'image'> = {},
): Promise<string> {
  return callGemini(prompt, { model: GEMINI_PRO, ...opts, image });
}
