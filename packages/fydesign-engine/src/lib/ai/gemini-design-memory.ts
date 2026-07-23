// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Gemini Design Memory                                                       ║
// ║  Enables "change the landing page from Tuesday" — detects references to     ║
// ║  past designs in natural language and resolves them against DB candidates.   ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { callGeminiJSON, GEMINI_FLASH } from './gemini-client';
import type { VariantSearchResult } from '@/lib/projects/project-store';

// ── Types ─────────────────────────────────────────────────────────────────

export interface DesignReference {
  isReference: boolean;
  searchTerms: string[];
  dateHint: string | null;
  /** ISO date string for the lower bound of the date filter */
  dateFrom: string | null;
  /** ISO date string for the upper bound of the date filter */
  dateTo: string | null;
  confidence: number;
}

export interface ResolvedDesign {
  matchedVariantId: string;
  refinementPrompt: string;
  reasoning: string;
  confidence: number;
}

// ── 1. Detect if user is referencing a past design ────────────────────────

/**
 * Analyze a user message to determine if they're referencing a past design.
 * Uses Gemini Flash for speed — this runs on every message in refine mode.
 *
 * Examples that should match:
 * - "Cambia el landing que hice el martes"
 * - "Modifica el carrusel de la semana pasada"
 * - "El post de Instagram que creamos para Nike, hazle el texto más grande"
 * - "Necesito que hagas un cambio a X diseño que hiciste tal día"
 *
 * Examples that should NOT match:
 * - "Crea un nuevo landing page"
 * - "Diseña un carrusel de 5 slides"
 */
export async function detectDesignReference(
  userMessage: string,
  currentDate: string = new Date().toISOString(),
): Promise<DesignReference> {
  const prompt = `You analyze user messages to detect references to PREVIOUSLY CREATED designs.

CURRENT DATE/TIME: ${currentDate}

USER MESSAGE: "${userMessage}"

Determine:
1. Is the user referencing an EXISTING design they want to MODIFY (not create a NEW one)?
2. Extract search terms: design type keywords (landing, carousel, post, email, banner, deck, etc.)
3. Extract date hint: any temporal reference ("el martes", "hace 3 días", "la semana pasada", "ayer", "el lunes")
4. Convert the date hint to ISO date ranges (dateFrom/dateTo) relative to CURRENT DATE

IMPORTANT:
- "Crea un nuevo..." → NOT a reference (isReference=false)
- "Cambia el..." / "Modifica..." / "Actualiza..." / "El X que hiciste..." → IS a reference
- "El landing del martes" → reference with dateHint
- "Hazle un cambio al carrusel" → reference without specific date (search recent)

Return JSON:
{
  "isReference": true,
  "searchTerms": ["landing", "page"],
  "dateHint": "el martes",
  "dateFrom": "2026-05-19T00:00:00Z",
  "dateTo": "2026-05-19T23:59:59Z",
  "confidence": 0.95
}

If NOT a reference: { "isReference": false, "searchTerms": [], "dateHint": null, "dateFrom": null, "dateTo": null, "confidence": 0 }`;

  try {
    const result = await callGeminiJSON<DesignReference>(prompt, {
      model: GEMINI_FLASH,
      temperature: 0,
      maxTokens: 500,
    });

    if (result && typeof result.isReference === 'boolean') {
      console.log(`[DesignMemory] Reference detected: ${result.isReference} (${result.confidence}) terms=[${result.searchTerms?.join(',')}] date=${result.dateHint || 'none'}`);
      return result;
    }
  } catch (e) {
    console.warn('[DesignMemory] detectDesignReference failed:', e instanceof Error ? e.message : e);
  }

  return {
    isReference: false,
    searchTerms: [],
    dateHint: null,
    dateFrom: null,
    dateTo: null,
    confidence: 0,
  };
}

// ── 2. Resolve: pick best match from DB candidates ────────────────────────

/**
 * Given a user message and a list of candidate variants from the DB,
 * Gemini picks the best match and extracts the refinement instruction.
 *
 * Only lightweight metadata is sent — NO HTML.
 */
export async function resolveDesignFromCandidates(
  userMessage: string,
  candidates: VariantSearchResult[],
): Promise<ResolvedDesign | null> {
  if (candidates.length === 0) return null;

  // If only one candidate, skip LLM call
  if (candidates.length === 1) {
    return {
      matchedVariantId: candidates[0].id,
      refinementPrompt: userMessage,
      reasoning: 'single candidate — auto-selected',
      confidence: 0.8,
    };
  }

  const candidateList = candidates.map((c, i) =>
    `  ${i}. id="${c.id}" label="${c.label}" folder="${c.folder || 'none'}" ` +
    `dims=${c.width}x${c.height} created="${c.createdAt}" ` +
    `desc="${(c.description || '').slice(0, 100)}"`
  ).join('\n');

  const prompt = `You are resolving a user's reference to a past design.

USER MESSAGE: "${userMessage}"

CANDIDATE DESIGNS:
${candidateList}

Tasks:
1. Pick the candidate that BEST matches what the user is referring to
2. Extract the REFINEMENT INSTRUCTION (what they want changed) — separate from the reference
3. Explain your reasoning briefly

For example, if user says "Hazle el texto más grande al landing del martes":
- Match: the landing page variant created on Tuesday
- Refinement: "Hazle el texto más grande" (make text bigger)

Return JSON:
{
  "matchedVariantId": "the id of the best match",
  "refinementPrompt": "the extracted change request",
  "reasoning": "why this candidate matches",
  "confidence": 0.9
}

If NO candidate is a good match, return: { "matchedVariantId": "", "refinementPrompt": "", "reasoning": "no match found", "confidence": 0 }`;

  try {
    const result = await callGeminiJSON<ResolvedDesign>(prompt, {
      model: GEMINI_FLASH,
      temperature: 0,
      maxTokens: 800,
    });

    if (result?.matchedVariantId) {
      // Validate the ID exists in candidates
      const valid = candidates.some(c => c.id === result.matchedVariantId);
      if (valid) {
        console.log(`[DesignMemory] Resolved: "${result.matchedVariantId}" — ${result.reasoning}`);
        return result;
      }
      console.warn(`[DesignMemory] Gemini returned invalid variant ID: ${result.matchedVariantId}`);
    }
  } catch (e) {
    console.warn('[DesignMemory] resolveDesignFromCandidates failed:', e instanceof Error ? e.message : e);
  }

  // Fallback: pick the most recent candidate
  return {
    matchedVariantId: candidates[0].id,
    refinementPrompt: userMessage,
    reasoning: 'fallback — most recent variant',
    confidence: 0.5,
  };
}
