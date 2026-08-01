// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Gemini Smart Refine Router (B3)                                           ║
// ║  Decides whether a user's refine request needs a cheap micro-edit (Haiku)  ║
// ║  or a full regeneration (Opus). Saves ~70% on simple refines.              ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { callGeminiJSON, GEMINI_PRO } from './gemini-client';

export interface RefineRouting {
  strategy: 'micro-edit' | 'full-regen';
  model: 'claude-haiku-4-5' | 'claude-sonnet-4-6' | 'claude-opus-4-7';
  reasoning: string;
  estimatedTokens: number;
}

/**
 * Ask Gemini to decide the cheapest model that can handle a refine request.
 * Returns a sensible fallback (Opus) on failure.
 */
export async function decideRefineStrategy(input: {
  userInstruction: string;
  currentHtmlSize: number;
}): Promise<RefineRouting> {
  const prompt = `User wants to refine an HTML design. Decide strategy.

USER SAID: "${input.userInstruction}"
CURRENT HTML: ${input.currentHtmlSize} chars

Strategies:
- micro-edit (Haiku 4.5, cheap): only when the change is local and small — color swap, text edit, font weight, single class change, swap an image URL.
- full-regen (Opus 4.7, expensive): when the change touches layout, structure, or anything that ripples ("change the whole vibe", "make it more premium", "restructure", "add a new section").

Return JSON:
{ "strategy": "micro-edit", "model": "claude-haiku-4-5", "reasoning": "just a color change", "estimatedTokens": 1500 }`;

  try {
    console.log(`[RefineRouter] Routing: "${input.userInstruction.slice(0, 60)}..." (${input.currentHtmlSize} chars HTML)`);
    const result = await callGeminiJSON<RefineRouting>(prompt, {
      model: GEMINI_PRO,
      temperature: 0,
      maxTokens: 500,
    });

    if (result?.strategy && result.model) {
      console.log(`[RefineRouter] Decision: ${result.strategy} → ${result.model} (${result.reasoning})`);
      return result;
    }
  } catch (e) {
    console.warn('[RefineRouter] Failed:', e instanceof Error ? e.message : e);
  }

  // Fallback: always safe with Opus
  return {
    strategy: 'full-regen',
    model: 'claude-opus-4-7',
    reasoning: 'fallback',
    estimatedTokens: 8000,
  };
}
