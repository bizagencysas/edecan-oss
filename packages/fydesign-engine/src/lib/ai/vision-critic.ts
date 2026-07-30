// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Opus Vision Critic — "Opus with EYES"                                       ║
// ║                                                                              ║
// ║  The final gate of the design pipeline. After HTML → PNG, we load the PNG    ║
// ║  and hand it to Opus (multimodal, base64) to LOOK at the actual rendered     ║
// ║  pixels and answer the questions text-based review can't:                    ║
// ║    • Is there illegible / invented / GARBAGE text baked into an AI image     ║
// ║      ("HEGADLE MOPFLARD", "YOUR TEXT")?                                       ║
// ║    • Are there invented data/prices/numbers ("$300.000 COP") with no source? ║
// ║    • Is an app mockup EMPTY (pink/blank phone screen, no real UI)?           ║
// ║    • Is the composition premium, or amateur/templated?                       ║
// ║                                                                              ║
// ║  Returns a structured verdict. The orchestrator decides whether to          ║
// ║  REGENERATE the image (no text) and/or self-heal the HTML, then re-check.    ║
// ║                                                                              ║
// ║  Uses the SAME Claude entry point as the rest of the repo (callAI in         ║
// ║  deepseek-client) with an inline image — so it inherits the Anthropic/Vertex ║
// ║  auth, retries and cost logging already in place.                            ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { callAI, AI_MODEL, type InlineImage } from './deepseek-client';
import { renderHtmlToPng } from '@/lib/render-png';

/** Opus 4.x vision model. Defaults to the repo's Claude model; override to claude-opus-4-8 via env. */
export const VISION_MODEL = process.env.CLAUDE_VISION_MODEL || AI_MODEL;

export interface VisionVerdict {
  /** 0-10 overall premium-quality score. 10 = top agency work. */
  score: number;
  /** true when the design is clean enough to ship (no blocking problems). */
  pass: boolean;
  /** Garbage / illegible / hallucinated text baked INTO an image (the Imagen text bug). */
  garbageText: boolean;
  /** Numbers, prices, stats, or claims that look invented / unsourced. */
  inventedData: boolean;
  /** A phone/app mockup with a blank or placeholder screen (no real UI). */
  emptyMockup: boolean;
  /** Concrete, human-readable problems found in the pixels. */
  issues: string[];
  /** Exact instructions a builder model could apply to fix the HTML. */
  improvements: string[];
  /** What to do next: nothing, regenerate the image without text, or heal the HTML, or both. */
  action: VisionAction;
  /** Raw model text, for debugging. */
  _raw?: string;
}

export type VisionAction = 'ok' | 'regenerate-image' | 'heal-html' | 'both';

export interface VisionCriticInput {
  /** The final rendered PNG bytes. */
  png: Buffer;
  /** Canvas size, for context. */
  width: number;
  height: number;
  /** The user's original request — so Opus knows the intent. */
  userPrompt?: string;
  /** Brand name, for on-brand judgement. */
  brandName?: string;
  /** Authoritative real facts (prices/claims). Anything NOT here that looks like data = invented. */
  brandFacts?: string;
  /** Whether this design embeds AI-generated imagery (enables stricter garbage-text checks). */
  hasAiImagery?: boolean;
  /** Whether this design shows an app/phone mockup (enables empty-mockup checks). */
  hasMockup?: boolean;
}

const SYSTEM_PROMPT = `You are Opus, the FINAL visual critic ("the eyes") of fydesign, a premium design engine. You are shown the ACTUAL rendered PNG of a finished design. Judge the PIXELS you see — not code, not intentions.

You are hunting for four specific, common failures of AI design pipelines:

1. GARBAGE TEXT IN IMAGERY — AI image models bake fake, misspelled, or nonsense text into photos/illustrations (e.g. "HEGADLE MOPFLARD", "GLASEMOPHUE", "YOUR TEXT", scrambled letters on signs/screens/products). Real CSS headings are SHARP and correctly spelled — those are GOOD and NOT garbage. Only flag text that is misspelled, nonsensical, warped, or obviously model-hallucinated.

2. INVENTED DATA — prices, percentages, follower/member counts, ratings, or factual claims that appear made-up (especially specific numbers like "$300.000 COP", "2.500 creadores", "4.9★") when no real source backs them. If authoritative brand facts are provided, anything contradicting or absent from them that is stated as fact = invented.

3. EMPTY / FAKE MOCKUP — a phone or app mockup whose screen is blank, a flat color (e.g. solid pink), a placeholder, or generic filler instead of a real, detailed app UI.

4. NOT PREMIUM — amateur or templated look: clashing colors, broken alignment, text overflowing or clipped at the canvas edge, empty dead zones, low contrast, cheap gradients, emoji-as-UI.

Be fair: a crisp CSS title like "Llegó Acme Studio" is exactly what we want — never flag correctly-rendered CSS typography as garbage.

Return ONLY valid JSON (no markdown, no preamble):
{
  "score": <0-10 premium quality>,
  "pass": <true if shippable: no garbage text, no invented data, no empty mockup, score >= 8>,
  "garbageText": <true if any illegible/hallucinated text is baked into imagery>,
  "inventedData": <true if any number/price/claim looks invented/unsourced>,
  "emptyMockup": <true if a phone/app mockup screen is blank/placeholder>,
  "issues": ["concrete problem you can SEE", "..."],
  "improvements": ["exact fix instruction (selector/value or 'regenerate image X without text')", "..."]
}
Be terse. Each string <= 160 chars. Empty arrays + pass:true + score 9+ when it genuinely looks great.`;

function clamp(n: unknown, lo: number, hi: number, dflt: number): number {
  const v = typeof n === 'number' ? n : Number(n);
  if (!Number.isFinite(v)) return dflt;
  return Math.max(lo, Math.min(hi, v));
}

function decideAction(v: {
  garbageText: boolean;
  inventedData: boolean;
  emptyMockup: boolean;
  score: number;
}): VisionAction {
  // Garbage text is ALWAYS an image problem → regenerate the offending image without text.
  const needsImage = v.garbageText;
  // Invented data, empty mockup, or sub-par composition are HTML problems → self-heal the HTML.
  const needsHtml = v.inventedData || v.emptyMockup || v.score < 8;
  if (needsImage && needsHtml) return 'both';
  if (needsImage) return 'regenerate-image';
  if (needsHtml) return 'heal-html';
  return 'ok';
}

/**
 * Show Opus the final PNG and get a structured verdict.
 * Returns null on render-less input or total API failure (caller falls back to shipping as-is).
 */
export async function critiqueRenderedPng(input: VisionCriticInput): Promise<VisionVerdict | null> {
  if (!input.png || input.png.length === 0) return null;

  const image: InlineImage = { mimeType: 'image/png', data: input.png.toString('base64') };

  const ctxLines = [
    `Canvas: ${input.width}x${input.height}px${input.brandName ? ` · Brand: ${input.brandName}` : ''}`,
    input.userPrompt ? `User asked for: "${input.userPrompt}"` : '',
    input.hasAiImagery ? 'This design embeds AI-generated imagery — scrutinize it for baked-in garbage text.' : '',
    input.hasMockup ? 'This design shows an app/phone mockup — verify the screen has real, detailed UI (not blank/placeholder).' : '',
    input.brandFacts
      ? `AUTHORITATIVE BRAND FACTS (the ONLY real data; treat any other stated number/price/claim as invented):\n${input.brandFacts}`
      : 'No brand facts provided — any specific price/stat/claim that looks made-up should be flagged as invented.',
  ].filter(Boolean);

  const userMsg = `${ctxLines.join('\n')}\n\nLook at the attached PNG and return your JSON verdict.`;

  let raw: string;
  try {
    console.log(`[VisionCritic] Opus eyes → ${VISION_MODEL} (${input.width}x${input.height})`);
    raw = await callAI(userMsg, {
      model: VISION_MODEL,
      system: SYSTEM_PROMPT,
      image,
      maxTokens: 1500,
    });
  } catch (e) {
    console.warn('[VisionCritic] Opus vision call failed:', e instanceof Error ? e.message : e);
    return null;
  }

  // Parse JSON (tolerate fences/preamble — mirrors callAIJSON's extraction).
  let jsonStr = raw.trim();
  const fence = jsonStr.match(/```(?:json)?\s*([\s\S]*?)```/);
  if (fence) jsonStr = fence[1].trim();
  const brace = jsonStr.indexOf('{');
  if (brace > 0) jsonStr = jsonStr.slice(brace);

  let parsed: Partial<VisionVerdict>;
  try {
    parsed = JSON.parse(jsonStr) as Partial<VisionVerdict>;
  } catch (e) {
    console.warn('[VisionCritic] JSON parse failed:', e instanceof Error ? e.message : e, '| raw:', raw.slice(0, 200));
    return null;
  }

  const score = clamp(parsed.score, 0, 10, 7);
  const garbageText = parsed.garbageText === true;
  const inventedData = parsed.inventedData === true;
  const emptyMockup = parsed.emptyMockup === true;
  const issues = Array.isArray(parsed.issues) ? parsed.issues.map(String) : [];
  const improvements = Array.isArray(parsed.improvements) ? parsed.improvements.map(String) : [];
  // Don't trust the model's `pass` blindly — derive it from the hard signals.
  const pass = !garbageText && !inventedData && !emptyMockup && score >= 8;
  const action = decideAction({ garbageText, inventedData, emptyMockup, score });

  console.log(
    `[VisionCritic] score=${score}/10 pass=${pass} garbage=${garbageText} invented=${inventedData} emptyMockup=${emptyMockup} action=${action}`,
  );

  return { score, pass, garbageText, inventedData, emptyMockup, issues, improvements, action, _raw: raw };
}

// ── Raw-image text gate (pre-overlay) ────────────────────────────────────────

/** Split a `data:<mime>;base64,<payload>` URL into an InlineImage, or null. */
function dataUrlToInline(dataUrl: string): InlineImage | null {
  const m = /^data:([^;]+);base64,([\s\S]+)$/.exec(dataUrl || '');
  return m ? { mimeType: m[1], data: m[2] } : null;
}

/**
 * Focused, cheap check on a FRESHLY GENERATED image (before any CSS overlay):
 * does the image have hallucinated/garbage text baked in? Used to regenerate the
 * image before we waste the overlay+render step on a poisoned base.
 *
 * Accepts a data URL (what generateImagenImage returns) or a Buffer.
 * Returns true when the image looks CLEAN (no baked text). Fails OPEN (true) so a
 * vision outage never blocks generation — the final critique is the real gate.
 */
export async function imageIsTextFree(image: string | Buffer): Promise<boolean> {
  const inline = typeof image === 'string'
    ? dataUrlToInline(image)
    : { mimeType: 'image/png', data: image.toString('base64') };
  if (!inline) return true;

  try {
    const raw = await callAI(
      'Look at this AI-generated image. Does it contain ANY readable text, letters, words, ' +
        'numbers, captions, labels, signage, or watermarks baked into the pixels? ' +
        'Misspelled/scrambled/gibberish text counts as YES. ' +
        'Reply with ONLY JSON: {"hasText": true or false}.',
      { model: VISION_MODEL, image: inline, maxTokens: 80 },
    );
    let s = raw.trim();
    const fence = s.match(/```(?:json)?\s*([\s\S]*?)```/);
    if (fence) s = fence[1].trim();
    const b = s.indexOf('{');
    if (b > 0) s = s.slice(b);
    const j = JSON.parse(s) as { hasText?: boolean };
    return j.hasText !== true;
  } catch (e) {
    console.warn('[VisionCritic] imageIsTextFree failed (failing open):', e instanceof Error ? e.message : e);
    return true;
  }
}

// ── HTML → PNG → Opus eyes → heal → repeat (the orchestrator gate) ───────────

export interface VisionHealResult {
  /** Final HTML after any healing. */
  html: string;
  /** The last verdict Opus produced (null if rendering/critique never succeeded). */
  verdict: VisionVerdict | null;
  /** How many critique passes ran. */
  passes: number;
  /** Whether the HTML was changed by healing. */
  healed: boolean;
}

/**
 * The production gate for HTML designs: render the FINAL HTML to PNG, let Opus LOOK at
 * it, and if it flags invented data / empty mockup / weak composition, ask the provided
 * `heal` callback to fix the HTML, then re-render and re-check — up to `maxPasses` times.
 *
 * Garbage-text-in-imagery can't be fixed by editing HTML (the text is inside a raster
 * image), so when that's the only issue we surface it via the verdict for the image
 * pipeline to regenerate; HTML healing handles everything else.
 *
 * Fails safe: any render/critique error returns the input HTML unchanged.
 */
export async function visionGateHtml(
  html: string,
  opts: {
    width: number;
    height: number;
    userPrompt?: string;
    brandName?: string;
    brandFacts?: string;
    hasAiImagery?: boolean;
    hasMockup?: boolean;
    maxPasses?: number;
    /** Given (html, verdict) return healed HTML, or null to stop. */
    heal: (html: string, verdict: VisionVerdict) => Promise<string | null>;
  },
): Promise<VisionHealResult> {
  const maxPasses = Math.max(1, opts.maxPasses ?? 2);
  let current = html;
  let lastVerdict: VisionVerdict | null = null;
  let passes = 0;
  let healed = false;

  for (let i = 0; i < maxPasses; i++) {
    passes++;
    let png: Buffer;
    try {
      png = await renderHtmlToPng(current, opts.width, opts.height, `vision-gate p${i + 1}`);
    } catch (e) {
      console.warn('[VisionCritic] gate render failed:', e instanceof Error ? e.message : e);
      break;
    }

    const verdict = await critiqueRenderedPng({
      png,
      width: opts.width,
      height: opts.height,
      userPrompt: opts.userPrompt,
      brandName: opts.brandName,
      brandFacts: opts.brandFacts,
      hasAiImagery: opts.hasAiImagery,
      hasMockup: opts.hasMockup,
    });
    lastVerdict = verdict;
    if (!verdict) break; // critique unavailable → ship as-is

    if (verdict.pass || verdict.action === 'ok' || verdict.action === 'regenerate-image') {
      // 'regenerate-image' can't be solved by editing HTML — stop and let the caller act on the verdict.
      break;
    }

    // action is 'heal-html' or 'both' → try to repair the HTML, then loop to re-check.
    let next: string | null = null;
    try {
      next = await opts.heal(current, verdict);
    } catch (e) {
      console.warn('[VisionCritic] heal callback failed:', e instanceof Error ? e.message : e);
    }
    if (!next || next.trim().length < current.length * 0.5) break; // no usable fix → stop
    current = next;
    healed = true;
  }

  return { html: current, verdict: lastVerdict, passes, healed };
}

/**
 * Generate an image and, if it has garbage text baked in, regenerate up to `tries`
 * times with an ever-stronger no-text instruction. The no-text policy is already
 * enforced inside generateImagenImage; this adds a VISION verification loop on top.
 *
 * `gen(strictnessSuffix)` must produce a data URL for the given extra prompt suffix.
 * Returns the cleanest data URL we got (the last attempt if all had text).
 */
export async function generateTextFreeImage(
  gen: (extraNoTextSuffix: string) => Promise<string>,
  opts: { tries?: number; verify?: boolean } = {},
): Promise<{ dataUrl: string; attempts: number; clean: boolean }> {
  const tries = Math.max(1, opts.tries ?? 2);
  const suffixes = [
    '',
    ' Absolutely no text, letters, numbers or symbols anywhere — blank/abstract surfaces only.',
    ' ZERO text. If any surface would show writing, render it empty, blurred, or turned away from camera.',
  ];
  let last = '';
  for (let i = 0; i < tries; i++) {
    last = await gen(suffixes[Math.min(i, suffixes.length - 1)]);
    if (opts.verify === false) return { dataUrl: last, attempts: i + 1, clean: true };
    const clean = await imageIsTextFree(last);
    if (clean) return { dataUrl: last, attempts: i + 1, clean: true };
    console.warn(`[VisionCritic] generated image had baked text (attempt ${i + 1}/${tries}) — regenerating without text`);
  }
  return { dataUrl: last, attempts: tries, clean: false };
}
