// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Opus Director Engine — "Opus thinks. DeepSeek builds."                      ║
// ║                                                                              ║
// ║  Opus 4.7 acts as: artistic director, taste engine, composition critic,      ║
// ║  visual strategist. It does NOT generate code — it generates TASTE.          ║
// ║                                                                              ║
// ║  The builder model consumes Opus directives as executable guidance.          ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { callAI } from '@/lib/ai/deepseek-client';
import { loadDirectivesForDomain, getAllLatestDirectives, getUniversalRestraint, getUniversalAntiPatterns } from '@/lib/corpus/opus-directives';
import type { CreativeDirective } from '@/lib/corpus/opus-directives';
import type { AssembledContext, DirectiveContext } from './context-builder';

// ── Opus Artistic Guidance ──────────────────────────────────────────────────

export interface OpusArtisticGuidance {
  /** When this guidance was generated */
  generatedAt: string;
  /** Model used (always claude-opus-4-7) */
  model: string;
  /** One-line artistic summary */
  artisticSummary: string;
  /** Composition strategy (2-4 specific rules for THIS generation) */
  compositionStrategy: string[];
  /** Typography hierarchy rules */
  typographyGuidance: string[];
  /** Spacing & rhythm philosophy */
  spacingPhilosophy: string[];
  /** Color usage strategy */
  colorStrategy: string[];
  /** Visual restraint rules (what to hold back) */
  restraintRules: string[];
  /** Explicit anti-patterns to avoid for THIS design */
  antiPatterns: string[];
  /** Emotional/experiential direction */
  emotionalDirection: string;
  /** The single dominant visual move Opus recommends */
  dominantVisualMove: string;
  /** What NOT to do (specific to this prompt/context) */
  forbiddenMoves: string[];
  /** Reference designers/brands to channel */
  tasteReferences: string[];
  /** Raw Opus response for debugging */
  _raw: string;
}

// ── Fallback guidance (no API call needed) ──────────────────────────────────

function buildFallbackGuidance(directives: CreativeDirective[]): OpusArtisticGuidance {
  const allHeuristics = directives.flatMap((d) => d.heuristics);
  const allAntiPatterns = directives.flatMap((d) => d.antiPatterns);
  const universalRestraint = getUniversalRestraint();
  const universalAnti = getUniversalAntiPatterns();

  return {
    generatedAt: new Date().toISOString(),
    model: 'built-in-directives (fallback)',
    artisticSummary: directives[0]?.directiveText || 'Premium design with editorial restraint',
    compositionStrategy: [
      'One clear focal point per view — the eye must know where to land',
      'Asymmetric balance: heavier element balanced by negative space',
      'Generous negative space between sections (120-200px) defines rhythm',
    ],
    typographyGuidance: [
      'Clear size jump between heading levels (h1=2.5x body, h2=1.8x, h3=1.3x)',
      'Limit to 5-7 type sizes — more is noise',
      'Use weight and size for hierarchy before reaching for color',
    ],
    spacingPhilosophy: [
      'Whitespace is a luxury material — spend it generously',
      'Every element must earn its place on the screen',
      'Consistent spacing rhythm throughout',
    ],
    colorStrategy: [
      'Color is seasoning, not the main ingredient',
      'One accent used max twice per composition',
      'Prefer neutral foundations with strategic color moments',
    ],
    restraintRules: universalRestraint.slice(0, 4),
    antiPatterns: [...allAntiPatterns.slice(0, 5), ...universalAnti.slice(0, 3)],
    emotionalDirection: 'Calm authority with editorial refinement',
    dominantVisualMove: allHeuristics[0] || 'Typography-first composition with generous whitespace',
    forbiddenMoves: [
      'Startup gradient fog (purple-to-blue)',
      'Three equal feature cards above the fold',
      'Over-decorated cards within cards',
    ],
    tasteReferences: ['Linear', 'Stripe', 'Apple', 'Vercel'],
    _raw: '',
  };
}

// ── Opus 4.7 Prompt ────────────────────────────────────────────────────────

function buildOpusPrompt(
  userPrompt: string,
  mode: string,
  designPlan: Array<{ label: string; description: string; artDirection?: string; visualMove?: string }>,
  directives: CreativeDirective[],
  brandContext: { name: string; blurb: string; industry?: string; colors: string[] },
): string {
  const dos = directives.flatMap((d) => d.heuristics);
  const donts = directives.flatMap((d) => d.antiPatterns);
  const universalRestraint = getUniversalRestraint();
  const universalAnti = getUniversalAntiPatterns();

  const designBriefs = designPlan.map((d, i) =>
    `Design ${i + 1}: "${d.label}" — ${d.description}\n  Art Direction: ${d.artDirection || 'TBD'}\n  Visual Move: ${d.visualMove || 'TBD'}`,
  ).join('\n\n');

  return `You are OPUS — the artistic director and taste engine of fydesign, a design intelligence OS. You do NOT write code. You define TASTE. A separate execution model (DeepSeek) will implement your guidance.

Your role:
- Artistic Director: define the visual strategy
- Taste Engine: decide what feels premium vs. template
- Composition Critic: establish spatial rules
- Visual Strategist: pick the dominant move, forbid the clichés

## BRAND CONTEXT
${brandContext.name}${brandContext.industry ? ` — ${brandContext.industry}` : ''}
${brandContext.blurb || ''}
${brandContext.colors.length > 0 ? `Brand colors: ${brandContext.colors.join(', ')}` : ''}

## USER REQUEST
"${userPrompt}"

## DESIGN PLAN (${designPlan.length} designs)
${designBriefs}

## EXISTING DIRECTIVES (domain heuristics)
DO:
${dos.slice(0, 8).map((h) => `- ${h}`).join('\n')}

DON'T:
${donts.slice(0, 8).map((h) => `- ${h}`).join('\n')}

## UNIVERSAL RESTRAINT PRINCIPLES
${universalRestraint.map((r) => `- ${r}`).join('\n')}

## UNIVERSAL ANTI-PATTERNS
${universalAnti.map((a) => `- ${a}`).join('\n')}

## YOUR TASK
Study the brand, the user's request, the design plan, and the existing directives. Then produce refined, SPECIFIC artistic guidance for THIS generation. Your output will be injected directly into the execution model's system prompt.

Return a JSON object with these fields:

{
  "artisticSummary": "One sentence capturing the artistic direction",
  "compositionStrategy": ["3-4 specific composition rules for THIS design"],
  "typographyGuidance": ["2-3 typography rules specific to this context"],
  "spacingPhilosophy": ["2-3 spacing/rhythm rules"],
  "colorStrategy": ["2-3 color usage rules"],
  "restraintRules": ["3-4 things to hold back or do less of"],
  "antiPatterns": ["3-4 specific forbidden moves for THIS design"],
  "emotionalDirection": "How the design should FEEL — one evocative sentence",
  "dominantVisualMove": "The ONE most important visual move. Be specific.",
  "forbiddenMoves": ["2-3 things the execution model must NOT do"],
  "tasteReferences": ["2-3 designers/brands/products to channel"]
}

RULES:
- Be SPECIFIC to this prompt and brand. Not generic advice.
- The dominant visual move must be concrete enough for an execution model to implement.
- Anti-patterns must be specific to what could go wrong with THIS design.
- Don't repeat what's already in the existing directives — REFINE and CONTEXTUALIZE them.
- Think like a creative director who just studied the brief for 30 minutes.
- Every piece of guidance must be ACTIONABLE — an AI must be able to follow it precisely.

Return ONLY valid JSON. No markdown fences. No explanations.`;
}

// ── Main Opus Director Call ─────────────────────────────────────────────────

export async function getOpusArtisticGuidance(input: {
  userPrompt: string;
  mode: string;
  designPlan: Array<{ label: string; description: string; artDirection?: string; visualMove?: string }>;
  brandContext: { name: string; blurb: string; industry?: string; colors: string[] };
  domain?: string;
}): Promise<OpusArtisticGuidance> {
  // Load all relevant directives
  const [domainDirectives, allDirectives] = await Promise.all([
    loadDirectivesForDomain(input.domain || input.mode || 'general'),
    getAllLatestDirectives().catch(() => [] as CreativeDirective[]),
  ]);

  // Merge: domain-specific first, then all others, deduplicated
  const mergedDirectives = [domainDirectives];
  for (const d of allDirectives) {
    if (!mergedDirectives.some((m) => m.id === d.id)) {
      mergedDirectives.push(d);
    }
  }

  const prompt = buildOpusPrompt(
    input.userPrompt,
    input.mode,
    input.designPlan,
    mergedDirectives,
    input.brandContext,
  );

  try {
    console.log('[OpusDirector] Calling Opus 4.7 for artistic guidance...');
    const raw = await callAI(prompt, {
      model: 'claude-opus-4-7',
      temperature: 0.8,
      maxTokens: 3000,
    });

    // Extract JSON from response
    let jsonStr = raw.trim();
    const jsonMatch = jsonStr.match(/```(?:json)?\s*([\s\S]*?)```/);
    if (jsonMatch) jsonStr = jsonMatch[1].trim();
    const braceStart = jsonStr.indexOf('{');
    if (braceStart > 0) jsonStr = jsonStr.slice(braceStart);

    const parsed = JSON.parse(jsonStr) as Partial<OpusArtisticGuidance>;

    if (parsed.artisticSummary) {
      console.log(`[OpusDirector] Got artistic guidance: "${parsed.artisticSummary.slice(0, 80)}..."`);
      return {
        generatedAt: new Date().toISOString(),
        model: 'claude-opus-4-7',
        artisticSummary: parsed.artisticSummary || '',
        compositionStrategy: parsed.compositionStrategy || [],
        typographyGuidance: parsed.typographyGuidance || [],
        spacingPhilosophy: parsed.spacingPhilosophy || [],
        colorStrategy: parsed.colorStrategy || [],
        restraintRules: parsed.restraintRules || [],
        antiPatterns: parsed.antiPatterns || [],
        emotionalDirection: parsed.emotionalDirection || '',
        dominantVisualMove: parsed.dominantVisualMove || '',
        forbiddenMoves: parsed.forbiddenMoves || [],
        tasteReferences: parsed.tasteReferences || [],
        _raw: raw,
      };
    }

    console.warn('[OpusDirector] Opus returned incomplete guidance, using fallback');
  } catch (error) {
    console.warn('[OpusDirector] Opus call failed, using fallback:', error instanceof Error ? error.message : error);
  }

  return buildFallbackGuidance(mergedDirectives);
}

// ── Format Opus Guidance for DeepSeek system prompt ─────────────────────────

export function formatOpusGuidanceForPrompt(guidance: OpusArtisticGuidance): string {
  const lines: string[] = [];

  lines.push('## OPUS ARTISTIC DIRECTION');
  lines.push(`Artistic direction: ${guidance.artisticSummary}`);
  lines.push(`Emotional direction: ${guidance.emotionalDirection}`);
  lines.push(`Dominant visual move: ${guidance.dominantVisualMove}`);

  if (guidance.tasteReferences.length > 0) {
    lines.push(`Taste references: ${guidance.tasteReferences.join(', ')}`);
  }

  if (guidance.compositionStrategy.length > 0) {
    lines.push(`\nComposition strategy:\n${guidance.compositionStrategy.map((s) => `- ${s}`).join('\n')}`);
  }

  if (guidance.typographyGuidance.length > 0) {
    lines.push(`\nTypography:\n${guidance.typographyGuidance.map((s) => `- ${s}`).join('\n')}`);
  }

  if (guidance.spacingPhilosophy.length > 0) {
    lines.push(`\nSpacing philosophy:\n${guidance.spacingPhilosophy.map((s) => `- ${s}`).join('\n')}`);
  }

  if (guidance.colorStrategy.length > 0) {
    lines.push(`\nColor strategy:\n${guidance.colorStrategy.map((s) => `- ${s}`).join('\n')}`);
  }

  if (guidance.restraintRules.length > 0) {
    lines.push(`\nRestraint:\n${guidance.restraintRules.map((s) => `- ${s}`).join('\n')}`);
  }

  if (guidance.antiPatterns.length > 0) {
    lines.push(`\nAVOID:\n${guidance.antiPatterns.map((s) => `- ❌ ${s}`).join('\n')}`);
  }

  if (guidance.forbiddenMoves.length > 0) {
    lines.push(`\nFORBIDDEN:\n${guidance.forbiddenMoves.map((s) => `- 🚫 ${s}`).join('\n')}`);
  }

  lines.push('\nThe artistic direction above comes from Opus, the taste layer. Follow it precisely. It is not optional.');

  return lines.join('\n');
}

// ── Merge Opus guidance into existing directive context ─────────────────────

export function mergeOpusIntoDirectives(
  existingDirectives: DirectiveContext[],
  opusGuidance: OpusArtisticGuidance,
): DirectiveContext[] {
  // Create an Opus directive context that sits alongside domain directives
  const opusDirective: DirectiveContext = {
    id: 'dir-opus-runtime',
    domain: 'opus-artistic-direction',
    directives: [
      opusGuidance.artisticSummary,
      opusGuidance.dominantVisualMove,
      ...opusGuidance.compositionStrategy,
      ...opusGuidance.typographyGuidance,
      ...opusGuidance.spacingPhilosophy,
      ...opusGuidance.colorStrategy,
      ...opusGuidance.restraintRules,
    ].filter(Boolean),
    antiPatterns: [
      ...opusGuidance.antiPatterns,
      ...opusGuidance.forbiddenMoves,
    ],
    scoringRubric: {},
    examples: {
      good: opusGuidance.tasteReferences,
      bad: opusGuidance.forbiddenMoves,
    },
  };

  return [opusDirective, ...existingDirectives];
}
