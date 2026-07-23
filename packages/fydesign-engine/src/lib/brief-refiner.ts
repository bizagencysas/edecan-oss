// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Opus BRIEF REFINER — the creative director that interrogates a rough brief    ║
// ║                                                                              ║
// ║  The configured text provider acts as creative director and brand strategist.  ║
// ║  strategist. It reads a user's rough brief plus the brand's REAL facts and:    ║
// ║    1. decides DYNAMICALLY how many clarifying questions are genuinely needed   ║
// ║       (0–15) — sharp, never filler, never re-asking what's already clear;      ║
// ║    2. rewrites the brief into a sharper, more on-brand, BETTER version;        ║
// ║    3. surfaces its assumptions + a one-line rationale.                         ║
// ║                                                                              ║
// ║  When the caller folds prior answers back in (opts.answers), Opus absorbs       ║
// ║  them, improves the brief further, and asks only the REMAINING high-value      ║
// ║  questions (often zero). It NEVER invents stats, prices or facts not in         ║
// ║  ctx.info.                                                                     ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { callAIJSON } from './ai/deepseek-client';
import type { VideoBrandCtx } from './video/types';

/** One clarifying question Opus wants answered before it commits to the creative. */
export interface BriefQuestion {
  /** The question, asked in the brief's language, in one crisp line. */
  q: string;
  /** One-line reason this matters — why answering it materially improves the asset. */
  why: string;
  /** 3–6 concrete suggested answers tailored to THIS brand + asset (user may free-type). */
  options: string[];
}

/** The full output of a refinement pass. */
export interface RefinedBrief {
  /** Remaining high-value questions (0–15). Empty when the brief is already complete. */
  questions: BriefQuestion[];
  /** A sharper, more specific, on-brand brief — better than the user's own. */
  refinedBrief: string;
  /** What Opus assumed to fill the gaps (so the user can correct it). */
  assumptions: string[];
  /** One line on WHY it asked this many questions (or none). */
  rationale: string;
}

const REFINER_BRAIN = `You are a world-class CREATIVE DIRECTOR and BRAND STRATEGIST — the kind who has shipped award-winning campaigns for the most demanding brands. A client has handed you a rough brief. Your single obsession: make the FINAL ASSET PERFECT. To do that you (1) ask the SMARTEST clarifying questions, and (2) rewrite the brief into something sharper and more on-brand than the client could have written themselves.

HOW MANY QUESTIONS — THIS IS A JUDGEMENT CALL, NOT A QUOTA:
- You DECIDE dynamically how many clarifying questions are genuinely needed. The range is 1 to 15, and it is explicitly DEPENDS: sometimes 1, sometimes 7, sometimes 15. A crisp, already-detailed brief may need just ONE — or even ZERO. A vague, high-stakes or ambitious brief may justify up to 15.
- Ask EVERY question that would MATERIALLY change or improve the creative: audience, objective / desired action (CTA), the single key message or offer, the emotional angle, tone & voice, format / platform nuances, references or mood, what to AVOID, talent / persona, setting, pacing, length, and so on.
- But NEVER pad with filler. NEVER ask anything already clear in the brief or already answered. Prefer a few SHARP questions over many shallow ones. If the brief is already crisp, ask few (or none) and say so in the rationale.

EVERY QUESTION MUST HAVE:
- "q": the question itself, one crisp line, in the brief's language.
- "why": one short line on why it matters to the final asset.
- "options": 3 to 6 CONCRETE, specific suggested answers tailored to THIS brand and THIS asset kind (the client can still free-type their own). Make the options real and pickable, not generic placeholders.

THE REFINED BRIEF ("refinedBrief"):
- Rewrite the brief into a sharper, more specific, on-brand version that is genuinely BETTER than the client's — incorporating the brand's REAL facts (the BRAND INFO given), its palette, its fonts, and creative best practices for the asset KIND.
- Apply craft for the kind: e.g. a video ad needs a scroll-stopping hook in the first ~1.5s and a clear single CTA; an image needs strong composition, a focal subject and deliberate negative space for copy; a post needs a hook line + a reason to act.
- Be concrete and directive (subject, mood, composition, message, CTA) — write it as a brief a producer could execute, not as vague aspiration.
- ABSOLUTELY NO INVENTED FACTS: never fabricate statistics, prices, numbers, percentages, follower/user counts, claims, awards or testimonials. Use ONLY real facts present in the BRAND INFO. Where a fact is missing, stay qualitative and emotional rather than making one up.

IF PRIOR ANSWERS ARE PROVIDED:
- Fold every answer into the brief. Make the refinedBrief markedly better and more specific because of them.
- Then ask ONLY the remaining high-value questions — possibly NONE. Return an empty "questions" array when the brief is now complete.

ALSO RETURN:
- "assumptions": the things you assumed to fill gaps (so the client can correct them). Keep each short.
- "rationale": ONE line explaining why you asked this many questions (or why you asked none).

Return STRICT JSON only — no markdown, no commentary.`;

/** Raw question shape as returned by Opus, before normalization. */
interface RawQuestion {
  q?: unknown;
  why?: unknown;
  options?: unknown;
}

/** Raw refinement shape as returned by Opus, before normalization. */
interface RawRefined {
  questions?: unknown;
  refinedBrief?: unknown;
  assumptions?: unknown;
  rationale?: unknown;
}

/** Coerce an unknown into a trimmed string ('' when not a usable string). */
function asStr(v: unknown): string {
  return typeof v === 'string' ? v.trim() : '';
}

/** Coerce an unknown into an array of trimmed, non-empty strings. */
function asStrArray(v: unknown): string[] {
  if (!Array.isArray(v)) return [];
  return v.map((x) => asStr(x)).filter((s) => s.length > 0);
}

/**
 * Opus reads a rough brief + the brand's real facts and returns smart clarifying
 * questions plus a sharper, on-brand refined brief. On any failure it degrades
 * gracefully to the original prompt with no questions.
 */
export async function refineBrief(
  ctx: VideoBrandCtx,
  prompt: string,
  opts: { kind?: string; answers?: Array<{ q: string; a: string }> } = {},
): Promise<RefinedBrief> {
  const fallback: RefinedBrief = { questions: [], refinedBrief: prompt, assumptions: [], rationale: '' };

  try {
    const kind = asStr(opts.kind) || 'asset';
    const answers = Array.isArray(opts.answers) ? opts.answers : [];

    const priorAnswers = answers.length
      ? answers
          .map((a) => `- Q: ${asStr(a?.q) || '(question)'}\n  A: ${asStr(a?.a) || '(no answer)'}`)
          .join('\n')
      : '(none yet — this is the first pass)';

    const ask = `BRAND: ${ctx.name}
PALETTE: ${(ctx.colors || []).join(', ')} ${ctx.brandColors || ''}
FONTS: ${ctx.fonts || 'modern sans-serif'}
BRAND INFO (the REAL facts — use ONLY these, never invent others): ${ctx.info || '(infer conservatively from the brand name; do not fabricate specifics)'}

ASSET KIND: ${kind}

RAW BRIEF FROM THE CLIENT:
${prompt || '(empty — the client gave almost nothing; ask the questions that unlock the asset)'}

PRIOR ANSWERS (fold these in, then ask only what remains):
${priorAnswers}

Decide how many clarifying questions are genuinely needed (1–15, or 0 if already complete), each with a one-line "why" and 3–6 concrete options tailored to this brand and asset. Then write a refinedBrief that is sharper and more on-brand than the client's, plus your assumptions and a one-line rationale.

Return JSON:
{
  "questions": [
    { "q": "the question, one crisp line", "why": "why it matters to the final asset", "options": ["concrete option 1", "concrete option 2", "concrete option 3"] }
  ],
  "refinedBrief": "a sharper, more specific, on-brand brief — better than the client's own",
  "assumptions": ["what you assumed to fill the gaps"],
  "rationale": "one line on why you asked this many questions"
}`;

    const raw = await callAIJSON<RawRefined>(ask, {
      system: REFINER_BRAIN,
      maxTokens: 4500,
      model: process.env.CLAUDE_CLI_MODEL || undefined,
    });

    if (!raw) return fallback;

    const questions: BriefQuestion[] = (Array.isArray(raw.questions) ? raw.questions : [])
      .slice(0, 15)
      .map((rq): BriefQuestion => {
        const q = rq as RawQuestion;
        return {
          q: asStr(q?.q),
          why: asStr(q?.why),
          options: asStrArray(q?.options).slice(0, 6),
        };
      })
      .filter((bq) => bq.q.length > 0);

    const refinedBrief = asStr(raw.refinedBrief) || prompt;
    const assumptions = asStrArray(raw.assumptions);
    const rationale = asStr(raw.rationale);

    return { questions, refinedBrief, assumptions, rationale };
  } catch (e) {
    console.warn('[brief-refiner] refineBrief failed:', e instanceof Error ? e.message : e);
    return fallback;
  }
}
