// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  model-router.ts — Multi-Model Hub Brain (FyDesign / Higgsfield parity)     ║
// ║                                                                              ║
// ║  Curated decision table: one Task → best-fit current-gen model.              ║
// ║  Opus auto-routes via autoRoute(); synchronous pickModel() for hot paths.    ║
// ║                                                                              ║
// ║  CAPABILITY_CHAINS + withFallback(): "always pick the best, fall back if it  ║
// ║  cannot" — iterates ranked chain, skips on any 4xx / plan-gate / job error.  ║
// ║                                                                              ║
// ║  Model IDs mirror src/lib/video/models.ts — kept in sync.                   ║
// ║  All endpoint names verified against live Muapi catalog (June 2026, Pro).    ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { callAIJSON } from './ai/deepseek-client';
import type { VideoBrandCtx } from './video/types';

// ─── Task Taxonomy ────────────────────────────────────────────────────────────

export type Task =
  | 'image'
  | 'image-edit'
  | 'video-i2v'
  | 'video-t2v'
  | 'upscale-image'
  | 'upscale-video'
  | 'tts'
  | 'music'
  | 'lipsync'
  | 'reference-video'
  | 'animate';

// ─── Capability type + chains ─────────────────────────────────────────────────

/**
 * Fine-grained capability identifier used for fallback chains.
 * Extends beyond Task to include face-swap, bg-remove, etc.
 * Maps 1-to-1 with CAPABILITY_CHAINS keys.
 */
export type Capability =
  | 'image-edit'
  | 'i2v'
  | 't2v'
  | 'tts'
  | 'music'
  | 'lipsync'
  | 'upscale-image'
  | 'upscale-video'
  | 'reference-video'
  | 'animate'
  | 'face-swap'
  | 'bg-remove';

/**
 * CAPABILITY_CHAINS — ranked best→fallback lists of CURRENT-GEN Muapi endpoints
 * (all verified 200 on Pro plan, June 2026).
 *
 * withFallback() walks this list in order and skips to the next on any error.
 */
export const CAPABILITY_CHAINS: Record<Capability, string[]> = {
  /** Image → Video: identity-preserving, multi-reference, native audio → fallback ladder */
  i2v: [
    'gemini-omni-image-to-video',    // best: identity + native audio (Pro verified)
    'kling-v3.0-pro-image-to-video', // flagship Kling v3
    'kling-v2.5-turbo-pro-i2v',      // fast balanced
    'seedance-v1.5-pro-i2v-fast',    // cheapest fallback
  ],

  /** Text → Video: no input image */
  t2v: [
    'kling-v3.0-pro-text-to-video',  // flagship
    'kling-v2.5-turbo-pro-t2v',      // fast balanced
    'seedance-v1.5-pro-t2v-fast',    // cheapest fallback
  ],

  /** Reference / identity-driven video */
  'reference-video': [
    'veo3.1-reference-to-video',     // cinematic, highest quality
    'kling-o1-reference-to-video',   // balanced
    'seedance-lite-reference-video', // lighter
    'wan2.1-reference-video',        // WAN fallback
  ],

  /** Lipsync / talking-head / avatar */
  lipsync: [
    'kling-v2-avatar-pro',           // best quality avatar
    'kling-v2-avatar-standard',      // standard avatar
    'infinitetalk-image-to-video',   // still + audio → talk
    'sync-lipsync',                  // cheapest / fastest
  ],

  /** Image upscaling */
  'upscale-image': [
    'topaz-image-upscale',           // Topaz — industry standard
    'seedvr2-image-upscale',         // SeedVR2 alternative
    'ai-image-upscaler',             // generic fallback
  ],

  /** Video upscaling */
  'upscale-video': [
    'topaz-video-upscale',           // Topaz — best quality
    'ai-video-upscaler-pro',         // pro alternative
    'ai-video-upscaler',             // standard fallback
  ],

  /** Image editing / inpaint / relight / expand */
  'image-edit': [
    'nano-banana-edit',              // Gemini-image powered, brand refs
    'bytedance-seedream-v5.0-edit',  // Seedream 5 edit
    'flux-2-pro-edit',               // Flux edit fallback
  ],

  /** Animate a still (WAN motion transfer) */
  animate: [
    'wan2.2-animate',                // WAN 2.2 — motion transfer
  ],

  /** Face swap */
  'face-swap': [
    'ai-image-face-swap',
  ],

  /** Background removal */
  'bg-remove': [
    'ai-background-remover',
  ],

  /** Music generation */
  music: [
    'suno-create-music',
  ],

  /** Text-to-Speech / voiceover */
  tts: [
    'minimax-speech-2.6-hd',        // HD realistic voiceover
    'minimax-speech-2.6-turbo',     // turbo fallback
  ],
};

/** Return the ranked model chain for a capability (defensive copy). */
export function chainFor(capability: Capability): string[] {
  return [...CAPABILITY_CHAINS[capability]];
}

// ─── withFallback ─────────────────────────────────────────────────────────────

/**
 * "Always pick the best, fall back if it cannot."
 *
 * Walks CAPABILITY_CHAINS[capability] in order (best → fallback).
 * On any thrown error (4xx plan-gate, network, runtime 'failed' job) logs the
 * model + reason and tries the NEXT model.  If every model fails throws an
 * aggregated Error listing every attempt.
 *
 * @param capability  - which capability chain to walk
 * @param fn          - async function that receives the chosen model ID and
 *                      executes the actual API call; must throw on failure
 * @param opts.skip   - model IDs to skip entirely (already tried, known bad)
 */
export async function withFallback<T>(
  capability: Capability,
  fn: (model: string) => Promise<T>,
  opts?: { skip?: string[] },
): Promise<T> {
  const chain = CAPABILITY_CHAINS[capability];
  const skipSet = new Set(opts?.skip ?? []);
  const attempts: string[] = [];

  for (const model of chain) {
    if (skipSet.has(model)) {
      console.error(`[model-router] Saltando modelo en lista de omisión: capability='${capability}' model='${model}'`);
      continue;
    }

    try {
      console.error(`[model-router] Intentando capability='${capability}' model='${model}'`);
      const result = await fn(model);
      console.error(`[model-router] Éxito con capability='${capability}' model='${model}'`);
      return result;
    } catch (err) {
      const reason = err instanceof Error ? err.message : String(err);
      console.error(
        `[model-router] Falló capability='${capability}' model='${model}' razón='${reason}' — intentando siguiente`,
      );
      attempts.push(`${model}: ${reason}`);
    }
  }

  throw new Error(
    `[model-router] Todos los modelos fallaron para capability='${capability}'.\n` +
      attempts.map((a, i) => `  ${i + 1}. ${a}`).join('\n'),
  );
}

// ─── Routing Table ────────────────────────────────────────────────────────────

export interface ModelOption {
  model: string;
  bestFor: string;
  costUsd: number;
}

export interface TaskRoute {
  default: string;
  options: ModelOption[];
}

/**
 * Curated decision table — current-gen only (June 2026).
 * Mirrors VIDEO_I2V / IMAGE_MODEL / TTS_HD / MUSIC_MODEL / TALKING_AVATAR
 * constants from src/lib/video/models.ts.
 */
export const ROUTING: Record<Task, TaskRoute> = {
  // ── Image generation ──────────────────────────────────────────────────────
  image: {
    default: 'nano-banana-pro',
    options: [
      {
        model: 'nano-banana-pro',
        bestFor: 'brand refs multi-subject product placement exact composition Gemini-image',
        costUsd: 0.12,
      },
      {
        model: 'bytedance-seedream-v5.0',
        bestFor: 'fashion text portrait lifestyle multilingual curved-text web-search grounding',
        costUsd: 0.0325,
      },
      {
        model: 'flux-2-pro',
        bestFor: 'exact HEX color brand-color multilingual object-counting two-person identity chained edits',
        costUsd: 0.032,
      },
    ],
  },

  // ── Image editing / inpaint / relight / expand ────────────────────────────
  'image-edit': {
    default: 'nano-banana-edit',
    options: [
      {
        model: 'nano-banana-edit',
        bestFor: 'inpaint brush mask product placement relight expand outpaint background swap outfit brand refs Gemini-image',
        costUsd: 0.12,
      },
      {
        model: 'bytedance-seedream-v5.0-edit',
        bestFor: 'seedream edit fashion text portrait lifestyle restyle',
        costUsd: 0.0325,
      },
      {
        model: 'flux-2-pro-edit',
        bestFor: 'chained edits exact HEX color restyle transfer style transfer',
        costUsd: 0.032,
      },
    ],
  },

  // ── Image-to-Video ────────────────────────────────────────────────────────
  'video-i2v': {
    default: 'gemini-omni-image-to-video',
    options: [
      {
        model: 'gemini-omni-image-to-video',
        bestFor: 'identity preserving character consistent native audio persona talking-head voice best ultra Pro',
        costUsd: 1.50,
      },
      {
        model: 'kling-v3.0-pro-image-to-video',
        bestFor: 'flagship max quality kling professional premium 4K cinematic',
        costUsd: 0.72,
      },
      {
        model: 'kling-v2.5-turbo-pro-i2v',
        bestFor: 'fast turbo balanced quality cost product ads social creative',
        costUsd: 0.45,
      },
      {
        model: 'seedance-v1.5-pro-i2v-fast',
        bestFor: 'fast cheap physics-aware motion quick preview low budget',
        costUsd: 0.26,
      },
      {
        model: 'veo3.1-image-to-video',
        bestFor: 'cinematic film quality narrative drama high-fidelity cinema studio veo',
        costUsd: 2.50,
      },
      {
        model: 'openai-sora-2-image-to-video',
        bestFor: 'sora storyboard cinematic narrative complex scene',
        costUsd: 0.80,
      },
    ],
  },

  // ── Text-to-Video ─────────────────────────────────────────────────────────
  'video-t2v': {
    default: 'kling-v2.5-turbo-pro-t2v',
    options: [
      {
        model: 'kling-v2.5-turbo-pro-t2v',
        bestFor: 'fast turbo balanced quality cost text prompt only no image',
        costUsd: 0.45,
      },
      {
        model: 'veo3.1-text-to-video',
        bestFor: 'cinematic narrative top quality text prompt dramatic',
        costUsd: 2.50,
      },
      {
        model: 'seedance-v1.5-pro-t2v-fast',
        bestFor: 'fast cheap preview low budget quick generation',
        costUsd: 0.26,
      },
      {
        model: 'kling-v3.0-pro-text-to-video',
        bestFor: 'premium flagship text-only high quality 4K',
        costUsd: 0.72,
      },
    ],
  },

  // ── Image Upscaling (Topaz) ───────────────────────────────────────────────
  'upscale-image': {
    default: 'topaz-image-upscale',
    options: [
      {
        model: 'topaz-image-upscale',
        bestFor: 'upscale 2x 4x 8x photo portrait product standard high fidelity export',
        costUsd: 0.05,
      },
    ],
  },

  // ── Video Upscaling (Topaz) ───────────────────────────────────────────────
  'upscale-video': {
    default: 'topaz-video-upscale',
    options: [
      {
        model: 'topaz-video-upscale',
        bestFor: 'video upscale 4K portrait stylized fine detail smooth',
        costUsd: 0.10,
      },
    ],
  },

  // ── Text-to-Speech / Voiceover ────────────────────────────────────────────
  tts: {
    default: 'minimax-speech-2.6-hd',
    options: [
      {
        model: 'minimax-speech-2.6-hd',
        bestFor: 'realistic voiceover HD speech narration brand ambassador spokesperson',
        costUsd: 0.65,
      },
      {
        model: 'minimax-speech-2.6-turbo',
        bestFor: 'fast turbo quick preview voiceover draft',
        costUsd: 0.30,
      },
    ],
  },

  // ── Music generation ──────────────────────────────────────────────────────
  music: {
    default: 'suno-create-music',
    options: [
      {
        model: 'suno-create-music',
        bestFor: 'background music jingle mood cinematic ad social soundtrack',
        costUsd: 0.09,
      },
    ],
  },

  // ── Lipsync / Talking-head / Avatar ──────────────────────────────────────
  lipsync: {
    default: 'kling-v2-avatar-pro',
    options: [
      {
        model: 'kling-v2-avatar-pro',
        bestFor: 'lipsync talking head avatar pro high quality realistic portrait best',
        costUsd: 0.75,
      },
      {
        model: 'kling-v2-avatar-standard',
        bestFor: 'lipsync talking head avatar standard spokesperson portrait audio driven',
        costUsd: 0.35,
      },
      {
        model: 'infinitetalk-image-to-video',
        bestFor: 'still portrait talk image driven lipsync cheap fast',
        costUsd: 0.20,
      },
      {
        model: 'sync-lipsync',
        bestFor: 'fast cheap quick lipsync draft preview',
        costUsd: 0.04,
      },
    ],
  },

  // ── Reference / Identity-driven Video ────────────────────────────────────
  'reference-video': {
    default: 'veo3.1-reference-to-video',
    options: [
      {
        model: 'veo3.1-reference-to-video',
        bestFor: 'cinematic reference driven veo identity preserve scene quality narrative',
        costUsd: 2.50,
      },
      {
        model: 'kling-o1-reference-to-video',
        bestFor: 'kling reference style driven character consistent balanced cost',
        costUsd: 0.90,
      },
      {
        model: 'gemini-omni-image-to-video',
        bestFor: 'multi-reference identity audio character consistent persona',
        costUsd: 1.50,
      },
      {
        model: 'seedance-lite-reference-video',
        bestFor: 'seedance reference video cheap fast bytedance fallback low cost budget',
        costUsd: 0.10,
      },
      {
        model: 'wan2.1-reference-video',
        bestFor: 'wan reference video cheap fallback multi-image object character animate',
        costUsd: 0.10,
      },
    ],
  },

  // ── Animate a still (motion transfer / WAN-Animate) ──────────────────────
  animate: {
    default: 'wan2.2-animate',
    options: [
      {
        model: 'wan2.2-animate',
        bestFor: 'animate still character motion transfer drive expression body pose reference video',
        costUsd: 0.50,
      },
      {
        model: 'kling-v3.0-pro-image-to-video',
        bestFor: 'animate still image kling premium cinematic motion',
        costUsd: 0.72,
      },
      {
        model: 'seedance-v1.5-pro-i2v-fast',
        bestFor: 'animate still fast cheap quick preview physics',
        costUsd: 0.26,
      },
    ],
  },
};

// ─── pickModel ────────────────────────────────────────────────────────────────

/**
 * Return the Muapi/Vertex model ID best matching `hint` keywords for `task`.
 * Falls back to ROUTING[task].default when no option scores.
 *
 * @param task   - the Task category
 * @param hint   - free-text hint (e.g. "cinematic", "cheap", "fast", "brand refs")
 */
export function pickModel(task: Task, hint?: string): string {
  const route = ROUTING[task];
  if (!hint) return route.default;

  const lc = hint.toLowerCase();
  // Split on whitespace only — preserves version numbers like v1.5, v3.0, v2.6
  const words = lc.split(/\s+/).filter(Boolean);

  let bestScore = 0;
  let bestModel = route.default;

  for (const opt of route.options) {
    const bf = opt.bestFor.toLowerCase();
    const score = words.reduce((acc, w) => acc + (bf.includes(w) ? 1 : 0), 0);
    if (score > bestScore) {
      bestScore = score;
      bestModel = opt.model;
    }
  }

  return bestModel;
}

// ─── autoRoute ────────────────────────────────────────────────────────────────

export interface AutoRouteResult {
  task: Task;
  model: string;
  why: string;
}

/** Compact menu of options serialised for Opus. */
function buildRoutingMenu(): string {
  return (Object.entries(ROUTING) as [Task, TaskRoute][])
    .map(([task, route]) => {
      const opts = route.options
        .map((o) => `      • ${o.model} — ${o.bestFor} (~$${o.costUsd})`)
        .join('\n');
      return `  ${task} (default: ${route.default}):\n${opts}`;
    })
    .join('\n');
}

/** Simple keyword heuristic used when Opus is unavailable. */
function heuristicRoute(brief: string): AutoRouteResult {
  const lc = brief.toLowerCase();

  let task: Task;
  if (/\b(tts|voz|narrac|voiceover|speak|habla|texto.a.voz)\b/.test(lc)) {
    task = 'tts';
  } else if (/\b(m[uú]sica|music|jingle|soundtrack|audio|song)\b/.test(lc)) {
    task = 'music';
  } else if (/\b(lipsync|lip.sync|avatar|hablando|talking.head)\b/.test(lc)) {
    task = 'lipsync';
  } else if (/\b(animar|animate|motion.transfer|wan.animat)\b/.test(lc)) {
    task = 'animate';
  } else if (/\b(reference.video|kling.o1|veo.*referenc|identit.*video)\b/.test(lc)) {
    task = 'reference-video';
  } else if (/\b(upscale.*video|video.*upscal|topaz.*video)\b/.test(lc)) {
    task = 'upscale-video';
  } else if (/\b(upscale|ampliar|mejorar.*resoluci|4k.*imagen|topaz)\b/.test(lc)) {
    task = 'upscale-image';
  } else if (/\b(video.*texto|texto.*video|text.to.video|t2v)\b/.test(lc)) {
    task = 'video-t2v';
  } else if (/\b(video|anunc[io]|clip|animaci[oó]n|cine|ad|reel|short)\b/.test(lc)) {
    task = 'video-i2v';
  } else if (/\b(editar|edit|inpaint|retocar|relight|expandir|recortar|swap)\b/.test(lc)) {
    task = 'image-edit';
  } else {
    task = 'image';
  }

  const model = pickModel(task, brief);
  return { task, model, why: `[heurística] tarea='${task}' detectada por palabras clave` };
}

/**
 * Ask Opus to classify `brief` into a Task and pick the best model from ROUTING.
 * Falls back to keyword heuristic if Opus call fails or returns unexpected shape.
 *
 * @param brief - natural-language creative brief (any language)
 * @param ctx   - brand context (name, colors, assets) for richer routing decisions
 */
export async function autoRoute(brief: string, ctx: VideoBrandCtx): Promise<AutoRouteResult> {
  const menu = buildRoutingMenu();
  const brandSnippet = [
    ctx.name ? `Marca: ${ctx.name}` : '',
    ctx.brandColors ? `Colores de marca: ${ctx.brandColors}` : '',
    ctx.info ? `Info: ${ctx.info.slice(0, 200)}` : '',
  ]
    .filter(Boolean)
    .join('\n');

  const prompt = `Eres el director de producción de FyDesign, un motor de creatividad publicitaria con acceso a los mejores modelos de IA generativa de 2026 (Muapi, Vertex, Opus).

Contexto de marca:
${brandSnippet}

Brief del usuario:
"${brief}"

Tabla de routing disponible (task → modelos disponibles):
${menu}

Tu tarea:
1. Clasifica el brief en la Task más apropiada: image | image-edit | video-i2v | video-t2v | upscale-image | upscale-video | tts | music | lipsync | reference-video | animate
2. Elige el model_id más adecuado de las opciones de esa Task según el brief y el contexto.
3. Explica brevemente (1-2 frases) por qué ese modelo es la mejor elección.

Responde SOLO con JSON válido con esta forma exacta:
{"task": "<Task>", "model": "<model_id>", "why": "<explicación breve>"}`;

  let result: AutoRouteResult | null = null;

  try {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const raw = await callAIJSON<any>(prompt, { maxTokens: 256 });
    if (
      raw &&
      typeof raw === 'object' &&
      typeof raw.task === 'string' &&
      typeof raw.model === 'string' &&
      typeof raw.why === 'string' &&
      raw.task in ROUTING
    ) {
      result = { task: raw.task as Task, model: raw.model as string, why: raw.why as string };
      console.error(`[model-router] Opus eligió task='${result.task}' model='${result.model}'`);
    } else {
      console.error('[model-router] Respuesta de Opus no tiene la forma esperada, usando heurística');
    }
  } catch (e) {
    console.error('[model-router] Error llamando a Opus, usando heurística:', e instanceof Error ? e.message : e);
  }

  if (!result) {
    result = heuristicRoute(brief);
    console.error(`[model-router] Heurística: task='${result.task}' model='${result.model}'`);
  }

  // Safety: validate the chosen model exists in our routing table for the task
  const route = ROUTING[result.task];
  const knownModels = route.options.map((o) => o.model);
  if (!knownModels.includes(result.model) && result.model !== route.default) {
    console.error(
      `[model-router] Modelo desconocido '${result.model}' para task='${result.task}', usando default '${route.default}'`,
    );
    result = { ...result, model: route.default };
  }

  return result;
}
