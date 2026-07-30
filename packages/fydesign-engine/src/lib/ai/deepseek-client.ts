// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Provider-neutral model client — local CLI · Anthropic API · Vertex AI    ║
// ║                                                                            ║
// ║  Priority order (first match wins):                                        ║
// ║    1. Edecán bridge or explicitly configured local Claude CLI               ║
// ║    2. ANTHROPIC_API_KEY → direct Anthropic API (spends balance)            ║
// ║    3. GOOGLE_CREDENTIALS_JSON → Vertex AI (uses GCP credits)               ║
// ║                                                                            ║
// ║  Env vars:                                                                 ║
// ║    ANTHROPIC_API_KEY        — direct Anthropic (preferred)                 ║
// ║    GOOGLE_CREDENTIALS_JSON  — Vertex AI fallback                           ║
// ║    VERTEX_AI_PROJECT_ID     — GCP project ID (for Vertex)                  ║
// ║    VERTEX_AI_LOCATION       — region (default: us-east5, for Vertex)       ║
// ║    CLAUDE_MODEL             — override model (default: claude-opus-4-7)   ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { GoogleAuth } from 'google-auth-library';
import { execFile } from 'node:child_process';
import { existsSync } from 'node:fs';
import os from 'node:os';
import { claudeTextOnlyArgs, safeClaudeCliEnv } from '../runtime-env';

export const AI_MODEL = process.env.CLAUDE_MODEL || 'claude-opus-4-7';
const MAX_RETRIES = 3;

// ─── Auth Detection ──────────────────────────────────────────────────────────

type AuthMode = 'bridge' | 'cli' | 'anthropic' | 'vertex';

function bridgeEnabled(): boolean {
  return Boolean(process.env.FYDESIGN_LLM_BRIDGE_URL && process.env.FYDESIGN_LLM_BRIDGE_TOKEN);
}

// Resolve an explicitly enabled local `claude` CLI binary.
function resolveClaudeCli(): string | null {
  const explicit = process.env.CLAUDE_CLI_PATH;
  if (explicit && existsSync(explicit)) return explicit;
  for (const c of [
    `${os.homedir()}/.local/bin/claude`,
    '/opt/homebrew/bin/claude',
    '/usr/local/bin/claude',
    '/usr/bin/claude',
  ]) {
    if (existsSync(c)) return c;
  }
  return null;
}

// Opt-in: CLAUDE_USE_MAX=1 enables the user's already-authorized local CLI.
function maxEnabled(): boolean {
  const flag = (process.env.CLAUDE_USE_MAX || '').toLowerCase();
  return (flag === '1' || flag === 'true' || flag === 'yes') && !!resolveClaudeCli();
}

function fallbackMode(): Exclude<AuthMode, 'bridge' | 'cli'> | null {
  if (process.env.ANTHROPIC_API_KEY) return 'anthropic';
  if (process.env.GOOGLE_CREDENTIALS_JSON && process.env.VERTEX_AI_PROJECT_ID) return 'vertex';
  return null;
}

function detectAuthMode(): AuthMode {
  if (bridgeEnabled()) return 'bridge';
  if (maxEnabled()) return 'cli';
  const fb = fallbackMode();
  if (fb) return fb;
  throw new Error('No hay un modelo configurado. Conecta el router de Edecán, un CLI local autorizado, Anthropic API o Vertex AI.');
}

export function hasAuth(): boolean {
  return bridgeEnabled() || maxEnabled() || fallbackMode() !== null;
}

export const hasDeepSeekAuth = hasAuth;

// ─── Explicit local Claude CLI route ───────────────────────────────────────────
// Spawn `claude -p` with a deliberately tiny environment. Provider, storage,
// database and Edecán master secrets never cross into the CLI subprocess.
function callViaCLI(prompt: string, opts: CallOpts): Promise<string> {
  const bin = resolveClaudeCli();
  if (!bin) return Promise.reject(new Error('claude CLI not found (set CLAUDE_CLI_PATH or install Claude Code)'));

  const fullPrompt = opts.system ? `${opts.system}\n\n${prompt}` : prompt;
  const args = claudeTextOnlyArgs();
  if (process.env.CLAUDE_CLI_MODEL) args.push('--model', process.env.CLAUDE_CLI_MODEL);
  console.error(`[AI] Local Claude CLI → ${process.env.CLAUDE_CLI_MODEL || 'configured default model'}`);

  return new Promise<string>((resolve, reject) => {
    const child = execFile(
      bin, args,
      {
        env: safeClaudeCliEnv(),
        cwd: os.tmpdir(),
        timeout: 300_000,
        maxBuffer: 96 * 1024 * 1024,
      },
      (err, stdout, stderr) => {
        if (err) return reject(new Error(`claude CLI failed: ${err.message} ${(stderr || '').slice(0, 300)}`));
        const out = (stdout || '').trim();
        if (!out) return reject(new Error(`claude CLI returned empty output ${(stderr || '').slice(0, 300)}`));
        resolve(out);
      },
    );
    child.stdin?.end(fullPrompt);
  });
}

// ─── Vertex AI Auth ──────────────────────────────────────────────────────────

let _auth: GoogleAuth | null = null;

function getGoogleAuth(): GoogleAuth {
  if (_auth) return _auth;
  const rawCreds = process.env.GOOGLE_CREDENTIALS_JSON;
  if (!rawCreds) throw new Error('GOOGLE_CREDENTIALS_JSON is not set');
  let credentials: Record<string, unknown>;
  try { credentials = JSON.parse(rawCreds); } catch { throw new Error('GOOGLE_CREDENTIALS_JSON is not valid JSON'); }
  _auth = new GoogleAuth({ credentials, scopes: ['https://www.googleapis.com/auth/cloud-platform'] });
  return _auth;
}

async function getAccessToken(): Promise<string> {
  const authClient = await getGoogleAuth().getClient();
  const tokenPromise = authClient.getAccessToken();
  const timeoutPromise = new Promise<never>((_, reject) =>
    setTimeout(() => reject(new Error('GCP access token request timed out after 15s')), 15_000),
  );
  const tokenResponse = await Promise.race([tokenPromise, timeoutPromise]);
  const token = typeof tokenResponse === 'string' ? tokenResponse : tokenResponse?.token;
  if (!token) throw new Error('Failed to get GCP access token');
  return token;
}

// ─── Types ───────────────────────────────────────────────────────────────────

/** One inline image for a multimodal (vision) call. `data` is base64 WITHOUT the data: prefix. */
export interface InlineImage {
  /** e.g. 'image/png' | 'image/jpeg' | 'image/webp' */
  mimeType: string;
  /** base64 payload, no `data:...;base64,` prefix */
  data: string;
}

export interface CallOpts {
  system?: string;
  temperature?: number;
  maxTokens?: number;
  json?: boolean;
  model?: string; // override default AI_MODEL (e.g., 'claude-opus-4-7' for artistic direction)
  thinking?: number; // extended thinking budget in tokens (enables MAX mode for Sonnet 4.6)
  cacheSystem?: boolean; // mark the system prompt as cacheable (ephemeral, 5-min TTL).
  // First call writes (+25%), subsequent within TTL reads at -90%. Use for static/repeated context.
  /** Single inline image for vision (Opus eyes). Forces the API/Vertex path (CLI has no inline images). */
  image?: InlineImage;
  /** Multiple inline images for vision (e.g. final PNG + reference). Forces the API/Vertex path. */
  images?: InlineImage[];
}
export type DeepSeekCallOpts = CallOpts;

async function callViaBridge(
  prompt: string,
  opts: CallOpts,
  images: InlineImage[],
): Promise<string> {
  const url = process.env.FYDESIGN_LLM_BRIDGE_URL;
  const token = process.env.FYDESIGN_LLM_BRIDGE_TOKEN;
  if (!url || !token) throw new Error('Edecán LLM bridge is not configured');
  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({
      prompt,
      system: opts.system || '',
      temperature: opts.temperature,
      maxTokens: opts.maxTokens,
      json: opts.json,
      images,
    }),
    signal: AbortSignal.timeout(300_000),
  });
  if (!response.ok) {
    throw new Error(`Edecán LLM bridge rejected the request (${response.status})`);
  }
  const payload = await response.json() as { text?: unknown };
  if (typeof payload.text !== 'string' || !payload.text.trim()) {
    throw new Error('Edecán LLM bridge returned empty output');
  }
  return payload.text;
}

function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// ─── Build URL + Headers per auth mode ───────────────────────────────────────

async function buildRequest(mode: AuthMode): Promise<{ url: string; headers: Record<string, string>; versionField: string }> {
  if (mode === 'anthropic') {
    return {
      url: 'https://api.anthropic.com/v1/messages',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': process.env.ANTHROPIC_API_KEY!,
        'anthropic-version': '2023-06-01',
      },
      versionField: '', // not needed in body for direct API
    };
  }

  // Vertex AI
  const projectId = process.env.VERTEX_AI_PROJECT_ID!;
  const location = process.env.VERTEX_AI_LOCATION || 'us-east5';
  const token = await getAccessToken();

  return {
    url: `https://${location}-aiplatform.googleapis.com/v1/projects/${projectId}/locations/${location}/publishers/anthropic/models/${AI_MODEL}:rawPredict`,
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`,
    },
    versionField: 'vertex-2023-10-16',
  };
}

// ─── Main API Call ───────────────────────────────────────────────────────────

export async function callAI(prompt: string, opts: CallOpts = {}): Promise<string> {
  let mode = detectAuthMode();

  // Vision (inline images) requires the structured messages API — the `claude -p`
  // CLI takes only a text prompt over stdin. So when images are present we MUST use
  // the Anthropic/Vertex HTTP path; fail clearly if neither is configured.
  const images: InlineImage[] = [
    ...(opts.image ? [opts.image] : []),
    ...(opts.images || []),
  ].filter((im) => im && im.data && im.mimeType);
  const hasImages = images.length > 0;

  if (mode === 'bridge') {
    return callViaBridge(prompt, opts, images);
  }

  if (hasImages && mode === 'cli') {
    const fb = fallbackMode();
    if (!fb) {
      throw new Error(
        'Vision (image input) needs the Edecán bridge or Anthropic/Vertex API, not the text-only CLI. ' +
          'Set ANTHROPIC_API_KEY or GOOGLE_CREDENTIALS_JSON + VERTEX_AI_PROJECT_ID.',
      );
    }
    mode = fb;
  }

  // Local CLI route. On failure, use an explicitly configured API/Vertex fallback.
  if (mode === 'cli') {
    try {
      return await callViaCLI(prompt, opts);
    } catch (e) {
      const fb = fallbackMode();
      if (!fb) throw e;
      console.warn(`[AI] Max/CLI failed (${e instanceof Error ? e.message : e}) — falling back to ${fb}`);
      mode = fb;
    }
  }

  // Build the user content. Text-only stays a plain string (cheap path); vision uses
  // the Anthropic content-block array: image blocks first, then the text prompt.
  type ImageBlock = { type: 'image'; source: { type: 'base64'; media_type: string; data: string } };
  type TextBlock = { type: 'text'; text: string };
  const userContent: string | Array<ImageBlock | TextBlock> = hasImages
    ? [
        ...images.map<ImageBlock>((im) => ({
          type: 'image',
          source: { type: 'base64', media_type: im.mimeType, data: im.data },
        })),
        { type: 'text', text: prompt },
      ]
    : prompt;

  const messages: Array<{ role: string; content: typeof userContent }> = [
    { role: 'user', content: userContent },
  ];

  const model = opts.model || AI_MODEL;
  const body: Record<string, unknown> = {
    model,
    max_tokens: opts.maxTokens ?? 4096,
    messages,
  };
  // Opus 4.7 / newer Anthropic models reject `temperature` — only include when safe
  if (opts.temperature !== undefined && !model.startsWith('claude-opus-4-7')) {
    body.temperature = opts.temperature;
  }

  // Extended thinking (MAX mode) — usable with Sonnet 4.6, Opus 4.7
  if (opts.thinking && opts.thinking > 0) {
    const isOpus = model.includes('opus');
    if (isOpus) {
      // Opus 4.7: adaptive thinking only, no budget_tokens, no temperature
      body.thinking = { type: 'adaptive' };
      body.output_config = { effort: 'high' };
      // Opus 4.7 does NOT accept temperature — ensure it's removed
      delete body.temperature;
      console.error(`[AI] Extended thinking adaptive (effort=high)`);
    } else {
      // Sonnet 4.6 uses enabled thinking with budget
      body.thinking = {
        type: 'enabled',
        budget_tokens: opts.thinking,
      };
      // Temperature must be 1 when thinking is enabled (Anthropic requirement)
      body.temperature = 1;
      console.error(`[AI] Extended thinking enabled (${opts.thinking} token budget)`);
    }
  }

  if (opts.system) {
    // When cacheSystem is enabled, send system as a content-block array with cache_control.
    // Anthropic charges +25% on write and -90% on cache hits (5-min TTL).
    // Skip caching when payload is too small (< ~1024 tokens ≈ 4000 chars) — overhead not worth it.
    if (opts.cacheSystem && opts.system.length > 4000) {
      body.system = [
        { type: 'text', text: opts.system, cache_control: { type: 'ephemeral' } },
      ];
    } else {
      body.system = opts.system;
    }
  }

  // Vertex AI needs anthropic_version in body, direct API uses header
  if (mode === 'vertex') {
    body.anthropic_version = 'vertex-2023-10-16';
    delete body.model; // Vertex includes model in URL
  }

  for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
    try {
      const { url, headers } = await buildRequest(mode);

      console.error(`[AI] ${mode === 'anthropic' ? 'Direct' : 'Vertex'} → ${model} (attempt ${attempt + 1})`);

      const res = await fetch(url, {
        method: 'POST',
        headers,
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(300_000),
      });

      if (res.status === 429) {
        const errorBody = await res.text().catch(() => '');
        const retryMatch = errorBody.match(/try again in (\d+(?:\.\d+)?)/i);
        const waitSec = retryMatch ? Math.ceil(parseFloat(retryMatch[1])) + 1 : (attempt + 1) * 8;
        console.warn(`[AI] Rate limited (429). Waiting ${waitSec}s before retry ${attempt + 1}/${MAX_RETRIES}...`);
        await sleep(waitSec * 1000);
        continue;
      }

      if (res.status === 529 || res.status === 503 || res.status === 502) {
        const waitSec = (attempt + 1) * 10;
        console.warn(`[AI] Overloaded (${res.status}). Waiting ${waitSec}s before retry ${attempt + 1}/${MAX_RETRIES}...`);
        await sleep(waitSec * 1000);
        continue;
      }

      if (!res.ok) {
        const errorText = await res.text().catch(() => 'Unknown error');
        throw new Error(`Claude API error ${res.status}: ${errorText}`);
      }

      const data = await res.json() as {
        content?: Array<{ type: string; text?: string; thinking?: string }>;
        stop_reason?: string;
        usage?: { input_tokens?: number; output_tokens?: number };
      };

      if (data.usage) {
        const isSonnet = model.includes('sonnet');
        const isOpus = model.includes('opus');
        const isHaiku = model.includes('haiku');
        const inputRate = isOpus ? 5 : isHaiku ? 1 : isSonnet ? 3 : 3;
        const outputRate = isOpus ? 25 : isHaiku ? 5 : isSonnet ? 15 : 15;
        const cost = ((data.usage.input_tokens || 0) / 1e6) * inputRate + ((data.usage.output_tokens || 0) / 1e6) * outputRate;
        console.error(`[AI] ${model} — ${data.usage.input_tokens} in / ${data.usage.output_tokens} out — ~$${cost.toFixed(4)}`);
      }

      // Find the text content block (skip thinking blocks when extended thinking is on)
      const textBlock = data.content?.find(c => c.type === 'text');
      const text = textBlock?.text;
      if (opts.thinking) {
        const thinkingBlock = data.content?.find(c => c.type === 'thinking');
        if (thinkingBlock) {
          console.error(`[AI] Extended thinking used — text output: ${text?.length || 0} chars`);
        }
      }
      if (!text) throw new Error('Claude returned empty response');

      if (data.stop_reason && data.stop_reason !== 'end_turn') {
        console.warn(`[AI] stop_reason=${data.stop_reason} — output may be incomplete`);
      }

      return text;

    } catch (e) {
      if (attempt < MAX_RETRIES && e instanceof Error &&
          (e.message.includes('429') || e.message.includes('503') || e.message.includes('ECONNRESET'))) {
        const waitSec = (attempt + 1) * 8;
        console.warn(`[AI] Retry ${attempt + 1}/${MAX_RETRIES} after ${waitSec}s...`);
        await sleep(waitSec * 1000);
        continue;
      }
      throw e;
    }
  }

  throw new Error('Claude API: max retries exceeded');
}

export const callDeepSeek = callAI;

// ─── JSON Mode ───────────────────────────────────────────────────────────────

export async function callAIJSON<T = unknown>(
  prompt: string,
  opts: CallOpts = {},
): Promise<T | null> {
  let raw: string;
  try {
    const jsonPrompt = `${prompt}\n\nIMPORTANT: Respond with ONLY valid JSON. No markdown fences, no explanations.`;
    raw = await callAI(jsonPrompt, opts);
  } catch (e) {
    console.warn('[AI] API call failed:', e instanceof Error ? e.message : e);
    return null;
  }

  let jsonStr = raw.trim();
  const jsonMatch = jsonStr.match(/```(?:json)?\s*([\s\S]*?)```/);
  if (jsonMatch) jsonStr = jsonMatch[1].trim();
  // Trim any preamble before the JSON — handle BOTH object ({…}) and array ([…]) responses,
  // slicing from whichever opening delimiter comes first (a bare '{' search corrupts arrays).
  const braceStart = jsonStr.indexOf('{');
  const bracketStart = jsonStr.indexOf('[');
  const start = (bracketStart >= 0 && (braceStart < 0 || bracketStart < braceStart)) ? bracketStart : braceStart;
  if (start > 0) jsonStr = jsonStr.slice(start);

  let parsed: T | null = null;
  try {
    parsed = JSON.parse(jsonStr) as T;
  } catch (e) {
    console.warn('[AI] JSON parse failed:', e instanceof Error ? e.message : e, 'raw:', raw.slice(0, 200));
    return null;
  }
  return parsed;
}

export const callDeepSeekJSON = callAIJSON;
