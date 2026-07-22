// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  FyDesign — Virality Predictor (Higgsfield parity)                         ║
// ║                                                                              ║
// ║  Scores short-form / paid-ads content against a strict rubric:              ║
// ║    • hook strength (0–100)                                                  ║
// ║    • message clarity (0–100)                                                ║
// ║    • shareability (0–100)                                                   ║
// ║    • overall virality index (0–100)                                         ║
// ║                                                                              ║
// ║  Powered by Opus via callAIJSON. No invented brand metrics.                 ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { callAIJSON } from './ai/deepseek-client';
import type { VideoBrandCtx } from './video/types';

// ─── Public Types ────────────────────────────────────────────────────────────

export interface ViralityScore {
  /** Overall virality index 0–100. */
  score: number;
  /** Hook strength 0–100 — how fast the opening earns attention. */
  hook: number;
  /** Message clarity 0–100 — how easily the viewer "gets it". */
  clarity: number;
  /** Shareability 0–100 — how likely the viewer is to pass it on. */
  shareability: number;
  /** Short bullet reasons explaining the scores (positive & negative). */
  reasons: string[];
  /** 3–5 concrete, actionable edits that would raise the score. */
  fixes: string[];
}

// ─── Internal rubric response shape (what we ask the LLM to return) ──────────

interface RubricResponse {
  score: number;
  hook: number;
  clarity: number;
  shareability: number;
  reasons: string[];
  fixes: string[];
}

// ─── System prompt ────────────────────────────────────────────────────────────

const RUBRIC_SYSTEM = `You are a senior short-form video and paid-ads strategist with 10+ years of experience \
running viral campaigns on TikTok, Instagram Reels, and YouTube Shorts. \
You score content using the following strict rubric:

HOOK STRENGTH (0–100)
- Does the first 1–3 seconds stop the scroll? Pattern-interrupt, bold claim, emotional trigger?
- 90–100: Irresistible — almost no one skips. 70–89: Good. 50–69: Average. Below 50: Weak.

MESSAGE CLARITY (0–100)
- Can a stranger understand the core offer in 5 seconds with the sound off?
- Is the call-to-action specific and visible?
- 90–100: Crystal clear. 70–89: Mostly clear. 50–69: Needs effort. Below 50: Confusing.

SHAREABILITY (0–100)
- Does it trigger emotion (humor, awe, empathy, surprise)? Does it feel native to the platform?
- Does it reward re-watching or tagging a friend?
- 90–100: People will share without being asked. Below 50: Purely functional, no pass-along.

OVERALL VIRALITY INDEX (0–100)
- Weighted composite: hook × 0.40 + clarity × 0.35 + shareability × 0.25.
- You may adjust ±5 points for exceptional holistic factors.

RULES
- Base every score ONLY on the concept/caption/hook text supplied. DO NOT invent brand attributes.
- reasons: 2–4 bullet strings explaining what drove each score (both strengths and weaknesses).
- fixes: exactly 3–5 concrete, copy-level edits the creator can make RIGHT NOW.
- Return ONLY the JSON object below, no markdown, no commentary.

REQUIRED JSON SCHEMA:
{
  "score": <number 0-100>,
  "hook": <number 0-100>,
  "clarity": <number 0-100>,
  "shareability": <number 0-100>,
  "reasons": ["<string>", ...],
  "fixes": ["<string>", "<string>", "<string>"]
}`;

// ─── Neutral fallback ────────────────────────────────────────────────────────

function neutralScore(note: string): ViralityScore {
  return {
    score: 50,
    hook: 50,
    clarity: 50,
    shareability: 50,
    reasons: [note],
    fixes: [
      'No se pudo evaluar el contenido — verifique la conexión con el modelo.',
      'Reintente con un concepto y caption más detallados.',
      'Asegúrese de que la API key o CLI de Claude estén configuradas.',
    ],
  };
}

// ─── scoreVirality ───────────────────────────────────────────────────────────

/**
 * Score a single piece of content against the virality rubric.
 *
 * @param ctx   Brand context — used only to confirm platform/tone alignment.
 * @param input The raw content: concept, caption, hook text, and optional platform.
 */
export async function scoreVirality(
  ctx: VideoBrandCtx,
  input: {
    concept?: string;
    caption?: string;
    hook?: string;
    platform?: string;
  },
): Promise<ViralityScore> {
  const platform = input.platform || 'TikTok / Instagram Reels';

  // Build the user prompt from whatever fields were supplied.
  const parts: string[] = [];
  if (input.concept) parts.push(`CONCEPT: ${input.concept}`);
  if (input.hook) parts.push(`HOOK (opening line / visual): ${input.hook}`);
  if (input.caption) parts.push(`CAPTION: ${input.caption}`);
  parts.push(`PLATFORM: ${platform}`);
  parts.push(`BRAND NAME (context only — do NOT invent metrics): ${ctx.name}`);

  const prompt = parts.join('\n');

  try {
    const result = await callAIJSON<RubricResponse>(prompt, {
      system: RUBRIC_SYSTEM,
      maxTokens: 1024,
      cacheSystem: true,
    });

    if (!result) {
      console.error('[virality] callAIJSON devolvió null — usando puntuación neutral');
      return neutralScore('El modelo no devolvió datos válidos.');
    }

    // Clamp all scores to [0, 100] and ensure required fields exist.
    const clamp = (n: unknown): number => {
      const num = typeof n === 'number' ? n : 50;
      return Math.max(0, Math.min(100, Math.round(num)));
    };

    const hook = clamp(result.hook);
    const clarity = clamp(result.clarity);
    const shareability = clamp(result.shareability);
    // Use model's overall score if provided; recompute as fallback.
    const score = typeof result.score === 'number'
      ? clamp(result.score)
      : clamp(hook * 0.4 + clarity * 0.35 + shareability * 0.25);

    const reasons = Array.isArray(result.reasons) && result.reasons.length > 0
      ? result.reasons.map(String)
      : ['Sin razones detalladas.'];

    const fixes = Array.isArray(result.fixes) && result.fixes.length > 0
      ? result.fixes.map(String).slice(0, 5)
      : ['Revise el hook, el caption y el CTA.'];

    console.error(`[virality] score=${score} hook=${hook} clarity=${clarity} shareability=${shareability} platform="${platform}"`);

    return { score, hook, clarity, shareability, reasons, fixes };

  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error(`[virality] Error al puntuar contenido: ${msg}`);
    return neutralScore(`Error al evaluar: ${msg}`);
  }
}

// ─── pickBest ────────────────────────────────────────────────────────────────

/**
 * Score each candidate and return the highest-scoring one plus the full ranked list.
 *
 * Gracefully handles partial failures: if scoring a candidate throws, it receives
 * a fallback score of 0 and the loop continues.
 *
 * @param ctx        Brand context passed through to scoreVirality.
 * @param candidates Array of objects with optional `concept` and/or `caption`.
 * @param opts       Optional platform hint.
 */
export async function pickBest<T extends { concept?: string; caption?: string }>(
  ctx: VideoBrandCtx,
  candidates: T[],
  opts?: { platform?: string },
): Promise<{ best: T; ranked: Array<{ item: T; score: number }> }> {
  if (candidates.length === 0) {
    throw new Error('[virality] pickBest: la lista de candidatos está vacía.');
  }

  if (candidates.length === 1) {
    // Fast path — no need to call the AI for a single candidate.
    return { best: candidates[0], ranked: [{ item: candidates[0], score: 50 }] };
  }

  const scored = await Promise.all(
    candidates.map(async (item) => {
      try {
        const result = await scoreVirality(ctx, {
          concept: item.concept,
          caption: item.caption,
          platform: opts?.platform,
        });
        return { item, score: result.score };
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        console.error(`[virality] Fallo al puntuar candidato, usando score=0: ${msg}`);
        return { item, score: 0 };
      }
    }),
  );

  // Sort descending by score.
  const ranked = [...scored].sort((a, b) => b.score - a.score);
  const best = ranked[0].item;

  console.error(`[virality] Mejor candidato: score=${ranked[0].score} de ${candidates.length} opciones`);

  return { best, ranked };
}
