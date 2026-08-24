// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Gemini Cost Optimizer (C1)                                                ║
// ║  Before each generation, decides which Claude model to use based on        ║
// ║  prompt complexity, brand size, and design count. Saves 60-80% on simple   ║
// ║  generations by routing to Haiku/Sonnet instead of Opus.                   ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { callGeminiJSON, GEMINI_PRO } from './gemini-client';

export interface CostStrategy {
  model: 'claude-haiku-4-5' | 'claude-sonnet-4-6' | 'claude-opus-4-7';
  reasoning: string;
  estimatedCostUsd: number;
}

/**
 * Ask Gemini to pick the cheapest Claude model that can handle a generation
 * without quality loss. Respects user overrides.
 *
 * Returns Sonnet as the safe fallback on failure.
 */
export async function decideCostStrategy(input: {
  userPrompt: string;
  mode?: string;
  brandSize: number;
  designCount: number;
  forcedModel?: string;
}): Promise<CostStrategy> {
  // If user explicitly picked a model, respect it immediately
  if (input.forcedModel) {
    return {
      model: input.forcedModel as CostStrategy['model'],
      reasoning: 'user override',
      estimatedCostUsd: 0,
    };
  }

  const prompt = `Decide the cheapest Claude model that can handle this design generation without quality loss.

USER PROMPT: "${input.userPrompt}"
MODE: ${input.mode || 'auto'}
BRAND TOKENS SIZE: ${input.brandSize} chars
DESIGN COUNT: ${input.designCount}

Models:
- claude-haiku-4-5 ($1/$5 per M tokens): simple, fast, OK for single posts, simple ads, basic variations
- claude-sonnet-4-6 ($3/$15): balanced, great for most generations, default workhorse
- claude-opus-4-7 ($15/$75): best taste, slow, expensive, reserve for high-stakes pitch decks, landing pages, complex compositions

Heuristics:
- "ad cuadrado simple" + 1 design → Haiku
- "carrusel de 5" + medium brand → Sonnet
- "pitch deck investor", "landing page" + complex brand → Opus
- "diseño innovador y memorable" + god mode → Opus
- Multiple distinct surfaces (landing + email + social) → Opus
- Simple text change or variation → Haiku

Return JSON: { "model": "claude-sonnet-4-6", "reasoning": "carousel of 5 with medium complexity", "estimatedCostUsd": 0.12 }`;

  try {
    console.log(`[CostOptimizer] Analyzing prompt complexity for model selection...`);
    const result = await callGeminiJSON<CostStrategy>(prompt, {
      model: GEMINI_PRO,
      temperature: 0,
      maxTokens: 500,
    });

    if (result?.model) {
      console.log(`[CostOptimizer] Decision: ${result.model} — ${result.reasoning} (~$${result.estimatedCostUsd?.toFixed(3) || '?'})`);
      return result;
    }
  } catch (e) {
    console.warn('[CostOptimizer] Failed:', e instanceof Error ? e.message : e);
  }

  // Fallback: Sonnet is the safe middle ground
  return {
    model: 'claude-sonnet-4-6',
    reasoning: 'fallback',
    estimatedCostUsd: 0.30,
  };
}
