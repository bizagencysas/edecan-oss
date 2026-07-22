// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Gemini Vision Critic (B1)                                                 ║
// ║  Renders HTML → PNG → sends to Gemini Pro Vision for pixel-level analysis. ║
// ║  Catches visual issues that text-based review misses: text cutoff,         ║
// ║  low contrast, broken layouts.                                             ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { callGemini, GEMINI_PRO } from './gemini-client';
import { renderHtmlToPNG } from '@/lib/screenshot-renderer';

export interface VisionCritique {
  score: number; // 0-10
  issues: string[];
  improvements: string[];
  pixelLevel: {
    textCutOff: boolean;
    lowContrast: boolean;
    brokenLayout: boolean;
  };
}

/**
 * Render an HTML design to PNG and ask Gemini Pro Vision to critique it visually.
 * Returns null on render/API failure (callers should fall back to regex checks).
 *
 * May consume the configured vision provider. Only used in highest-quality mode
 * or when regex pre-checks flag issues.
 */
export async function critiqueVisually(input: {
  html: string;
  width: number;
  height: number;
  userPrompt: string;
  brandName?: string;
}): Promise<VisionCritique | null> {
  // 1. Render to PNG
  let buffer: Buffer;
  try {
    buffer = await renderHtmlToPNG(input.html, input.width, input.height);
  } catch (e) {
    console.warn('[VisionCritic] Render failed:', e instanceof Error ? e.message : e);
    return null;
  }

  // 2. Send to Gemini Vision
  const base64 = buffer.toString('base64');
  const prompt = `You are looking at a ${input.width}x${input.height}px design generated for "${input.brandName || 'a brand'}".

USER REQUESTED: "${input.userPrompt}"

Look at the IMAGE. Identify CONCRETE visual problems a designer would fix:
- Is any text cut off, overlapping, or escaping the canvas?
- Is the contrast bad anywhere (light text on light bg, etc.)?
- Is the layout broken (empty zones, weird alignment)?
- Does it look polished and on-brand or amateur?

Return ONLY JSON:
{ "score": <0-10>, "issues": ["concrete issue 1"], "improvements": ["specific change 1"], "pixelLevel": { "textCutOff": <bool>, "lowContrast": <bool>, "brokenLayout": <bool> } }

Be concrete. If everything looks great, empty arrays and score 9+.`;

  try {
    console.log(`[VisionCritic] Calling Gemini Pro Vision for ${input.width}x${input.height} design...`);
    const raw = await callGemini(prompt, {
      model: GEMINI_PRO,
      temperature: 0.2,
      maxTokens: 2000,
      json: true,
      image: { mimeType: 'image/png', data: base64 },
    });

    const parsed = JSON.parse(raw) as VisionCritique;

    // Validate and normalize
    if (typeof parsed.score !== 'number') {
      console.warn('[VisionCritic] Invalid response — no score');
      return null;
    }

    parsed.score = Math.max(0, Math.min(10, parsed.score));
    parsed.issues = Array.isArray(parsed.issues) ? parsed.issues : [];
    parsed.improvements = Array.isArray(parsed.improvements) ? parsed.improvements : [];
    parsed.pixelLevel = parsed.pixelLevel || { textCutOff: false, lowContrast: false, brokenLayout: false };

    console.log(`[VisionCritic] Score: ${parsed.score}/10 — cutoff=${parsed.pixelLevel.textCutOff}, contrast=${parsed.pixelLevel.lowContrast}, layout=${parsed.pixelLevel.brokenLayout}`);
    return parsed;
  } catch (e) {
    console.warn('[VisionCritic] Failed:', e instanceof Error ? e.message : e);
    return null;
  }
}
