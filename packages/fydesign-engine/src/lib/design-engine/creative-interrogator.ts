// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Creative Interrogator — Strategic questioning engine                         ║
// ║  Infers design traits from assets, only asks about true ambiguity             ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { loadFullBrandIdentity, loadAssetContext } from '@/lib/corpus/asset-registry';
import { loadDirectivesForDomain } from '@/lib/corpus/opus-directives';
import type { HeuristicPattern } from '@/lib/corpus/heuristic-extractor';

function safeJSONParse(val: unknown): Record<string, unknown> {
  if (typeof val === 'object' && val !== null) return val as Record<string, unknown>;
  if (typeof val === 'string') {
    try { return JSON.parse(val) as Record<string, unknown>; } catch { return {}; }
  }
  return {};
}

// ── Inferred Trait ─────────────────────────────────────────────────────────

export interface InferredTrait {
  trait: string;
  category: TraitCategory;
  confidence: number;        // 0-1
  evidence: string[];        // what led to this inference
  implies: string[];         // what this means for questioning
}

export type TraitCategory =
  | 'typography'
  | 'color-system'
  | 'spacing'
  | 'emotional-tone'
  | 'visual-complexity'
  | 'design-maturity'
  | 'industry-domain'
  | 'composition-style'
  | 'brand-personality';

export interface InferenceResult {
  inferred: InferredTrait[];
  ambiguousAreas: TraitCategory[];
  shouldAsk: string[];       // domain areas worth questioning
  shouldSkip: string[];      // things we already know
  summary: string;           // natural-language summary for the AI prompt
}

// ── Brand data interfaces ──────────────────────────────────────────────────

interface BrandIdentitySnapshot {
  colors: string[];
  hasSerifFonts: boolean;
  hasMonospace: boolean;
  fontFamilies: string[];
  hasGradients: boolean;
  logoWordmark: boolean;
  logoSymbol: boolean;
  spacingValues: number[];
  domainDirectives: string[];
  industry: string;
  assetTags: string[];
}

// ── Main Inference Function ────────────────────────────────────────────────

export async function inferDesignTraits(
  brandId?: string,
  prompt?: string,
  mode?: string,
): Promise<InferenceResult> {
  const inferred: InferredTrait[] = [];

  if (!brandId) {
    // No brand — minimal inference from prompt text alone
    const promptTraits = inferFromPrompt(prompt || '');
    inferred.push(...promptTraits);
    return buildResult(inferred);
  }

  // Load all brand data in parallel
  const [identity, assets, directives] = await Promise.all([
    loadFullBrandIdentity(brandId).catch(() => null),
    loadAssetContext(brandId).catch(() => ({ logos: [], fonts: [], screenshots: [], identity: [] })),
    inferDomainFromBrand(brandId),
  ]);

  // Build a snapshot from all data sources
  const snapshot = buildBrandSnapshot(identity, assets, directives);

  // Run inference rules
  inferred.push(...inferTypography(snapshot));
  inferred.push(...inferColorSystem(snapshot));
  inferred.push(...inferSpacing(snapshot));
  inferred.push(...inferEmotionalTone(snapshot));
  inferred.push(...inferVisualComplexity(snapshot));
  inferred.push(...inferDesignMaturity(snapshot));
  inferred.push(...inferIndustryDomain(snapshot, prompt));
  inferred.push(...inferCompositionStyle(snapshot));
  inferred.push(...inferBrandPersonality(snapshot));

  // Add prompt-based inferences
  if (prompt) {
    inferred.push(...inferFromPrompt(prompt));
  }

  return buildResult(inferred);
}

// ── Snapshot Builder ───────────────────────────────────────────────────────

function buildBrandSnapshot(
  identity: Awaited<ReturnType<typeof loadFullBrandIdentity>> | null,
  assets: Awaited<ReturnType<typeof loadAssetContext>>,
  domain: string,
): BrandIdentitySnapshot {
  const colors: string[] = [];
  let hasSerifFonts = false;
  let hasMonospace = false;
  const fontFamilies: string[] = [];
  let hasGradients = false;
  let logoWordmark = false;
  let logoSymbol = false;
  const spacingValues: number[] = [];
  const assetTags: string[] = [];
  const directiveDomains: string[] = [domain];

  if (identity) {
    // Colors from color palettes
    for (const cp of identity.colorPalettes) {
      const cols = (cp.extractedData.colors as Array<{ hex: string }>) || [];
      colors.push(...cols.map((c) => c.hex));
    }

    // Typography
    for (const typo of identity.typography) {
      const ff = typo.extractedData.fontFamily as string || '';
      if (ff) fontFamilies.push(ff);
      if (/serif|georgia|garamond|playfair|bodoni|didot|caslon|merriweather|newsreader/i.test(ff)) {
        hasSerifFonts = true;
      }
      if (/mono|code|console|jetbrains|fira/i.test(ff)) {
        hasMonospace = true;
      }
    }

    // Spacing
    for (const sp of identity.spacing) {
      const scale = sp.extractedData.scale as number[] || [];
      spacingValues.push(...scale);
    }

    // Logo analysis
    for (const logo of (identity.logoAnalysis || [])) {
      if (logo.extractedData.isWordmark) logoWordmark = true;
      if (logo.extractedData.hasSymbol || logo.extractedData.isSymbolic) logoSymbol = true;
      if (logo.extractedData.hasGradient) hasGradients = true;
    }

    // Composition patterns
    for (const comp of (identity.compositionPatterns || [])) {
      const style = comp.extractedData.style as string;
      if (style) directiveDomains.push(style);
    }
  }

  // Asset tags
  for (const logo of (assets?.logos || [])) {
    assetTags.push(...(logo.tags || []));
  }
  for (const ss of (assets?.screenshots || [])) {
    assetTags.push(...(ss.tags || []));
  }

  return {
    colors: [...new Set(colors)],
    hasSerifFonts,
    hasMonospace,
    fontFamilies,
    hasGradients,
    logoWordmark,
    logoSymbol,
    spacingValues,
    domainDirectives: [...new Set(directiveDomains)],
    industry: domain,
    assetTags: [...new Set(assetTags)],
  };
}

async function inferDomainFromBrand(brandId: string): Promise<string> {
  try {
    const { loadBrandConfig } = await import('@/lib/db');
    const config = await loadBrandConfig(brandId);
    if (config) {
      const analysis = safeJSONParse(config.analysis_json || '{}');
      return (analysis.industry as string) || (analysis.theme as Record<string, unknown>)?.vibe as string || '';
    }
  } catch { /* ignore */ }
  return '';
}

// ── Inference Rules ────────────────────────────────────────────────────────

function inferTypography(s: BrandIdentitySnapshot): InferredTrait[] {
  const traits: InferredTrait[] = [];

  if (s.hasSerifFonts) {
    traits.push({
      trait: 'editorial-serif',
      category: 'typography',
      confidence: 0.85,
      evidence: ['Serif font dependency detected in brand assets'],
      implies: ['Editorial, luxury, or heritage typographic direction'],
    });
  }

  if (s.fontFamilies.length > 0) {
    traits.push({
      trait: `typography-family: ${s.fontFamilies.slice(0, 3).join(', ')}`,
      category: 'typography',
      confidence: 0.7,
      evidence: [`Font families loaded: ${s.fontFamilies.join(', ')}`],
      implies: ['Typography style is already established in brand'],
    });
  }

  if (s.hasMonospace) {
    traits.push({
      trait: 'technical-monospace',
      category: 'typography',
      confidence: 0.7,
      evidence: ['Monospace font in brand assets'],
      implies: ['Developer, fintech, or data-oriented product'],
    });
  }

  return traits;
}

function inferColorSystem(s: BrandIdentitySnapshot): InferredTrait[] {
  const traits: InferredTrait[] = [];
  const isDark = s.colors.filter((c) => {
    const r = parseInt(c.slice(1, 3), 16);
    const g = parseInt(c.slice(3, 5), 16);
    const b = parseInt(c.slice(5, 7), 16);
    return (r + g + b) / 3 < 100;
  });

  const hasBurgundy = s.colors.some((c) => /8[0-9A-F]2[0-9A-F]|7[0-9A-F][0-1][0-9A-F]/.test(c));
  const hasNavy = s.colors.some((c) => /#[0-2][0-9A-F][0-4][0-9A-F][5-9A-F]/.test(c));
  const isMinimal = s.colors.length <= 4;

  if (s.colors.length > 0) {
    traits.push({
      trait: `established-palette: ${s.colors.length} colors`,
      category: 'color-system',
      confidence: 0.8,
      evidence: [`${s.colors.length} brand colors extracted: ${s.colors.slice(0, 5).join(', ')}`],
      implies: ['Color palette is defined in brand — avoid asking about color choices'],
    });
  }

  if (hasBurgundy) {
    traits.push({
      trait: 'luxury-burgundy',
      category: 'color-system',
      confidence: 0.6,
      evidence: ['Burgundy/deep red tones in palette'],
      implies: ['Luxury, prestige, or heritage brand positioning'],
    });
  }

  if (hasNavy) {
    traits.push({
      trait: 'corporate-navy',
      category: 'color-system',
      confidence: 0.6,
      evidence: ['Navy/dark blue tones in palette'],
      implies: ['Corporate, fintech, or trust-oriented brand'],
    });
  }

  if (isMinimal && s.colors.length > 0) {
    traits.push({
      trait: 'restrained-palette',
      category: 'color-system',
      confidence: 0.7,
      evidence: ['Minimal color palette (<=4 colors)'],
      implies: ['Restrained, mature design language'],
    });
  }

  if (s.hasGradients) {
    traits.push({
      trait: 'gradient-friendly',
      category: 'color-system',
      confidence: 0.55,
      evidence: ['Gradients detected in brand assets'],
      implies: ['Open to atmospheric or dimensional color effects'],
    });
  }

  const darkRatio = s.colors.length > 0 ? isDark.length / s.colors.length : 0;
  if (darkRatio > 0.5 && s.colors.length >= 3) {
    traits.push({
      trait: 'dark-palette-dominant',
      category: 'color-system',
      confidence: 0.7,
      evidence: ['Majority of brand colors are dark tones'],
      implies: ['Dark-mode-first design likely expected'],
    });
  }

  return traits;
}

function inferSpacing(s: BrandIdentitySnapshot): InferredTrait[] {
  const traits: InferredTrait[] = [];

  if (s.spacingValues.length >= 3) {
    const avg = s.spacingValues.reduce((a, b) => a + b, 0) / s.spacingValues.length;
    const isGenerous = avg > 20;
    const isTight = avg < 10;

    if (isGenerous) {
      traits.push({
        trait: 'generous-spacing',
        category: 'spacing',
        confidence: 0.7,
        evidence: [`Average spacing unit: ${Math.round(avg)}px`],
        implies: ['Editorial, luxury, or premium spacing philosophy'],
      });
    } else if (isTight) {
      traits.push({
        trait: 'dense-layout',
        category: 'spacing',
        confidence: 0.65,
        evidence: [`Average spacing unit: ${Math.round(avg)}px`],
        implies: ['Dashboard, data-dense, or SaaS product layout'],
      });
    }

    traits.push({
      trait: `spacing-scale: ${s.spacingValues.slice(0, 5).join(', ')}px`,
      category: 'spacing',
      confidence: 0.6,
      evidence: ['Measured spacing values from brand components'],
      implies: ['Spacing rhythm is already established'],
    });
  }

  return traits;
}

function inferEmotionalTone(s: BrandIdentitySnapshot): InferredTrait[] {
  const traits: InferredTrait[] = [];

  const tags = s.assetTags.join(' ').toLowerCase();
  const all = `${s.industry} ${s.fontFamilies.join(' ')} ${tags}`.toLowerCase();

  if (/fintech|bank|finance|invest|pay|credit|crypto|blockchain|compliance|kyc/i.test(all)) {
    traits.push({
      trait: 'fintech-emotional-tone',
      category: 'emotional-tone',
      confidence: 0.8,
      evidence: ['Fintech signals in brand profile'],
      implies: ['Tone should be: calm authority, trust, precision, security'],
    });
  }

  if (/luxury|premium|elite|exclusive|vip|concierge|private/i.test(all)) {
    traits.push({
      trait: 'luxury-emotional-tone',
      category: 'emotional-tone',
      confidence: 0.8,
      evidence: ['Luxury signals in brand profile'],
      implies: ['Tone should be: exclusive, refined, aspirational, restrained'],
    });
  }

  if (/gaming|game|play|stream|esport/i.test(all)) {
    traits.push({
      trait: 'gaming-emotional-tone',
      category: 'emotional-tone',
      confidence: 0.75,
      evidence: ['Gaming signals in brand assets'],
      implies: ['Tone should be: energetic, immersive, bold, dynamic'],
    });
  }

  if (/saas|dashboard|platform|automation|workflow|productivity/i.test(all)) {
    traits.push({
      trait: 'saas-emotional-tone',
      category: 'emotional-tone',
      confidence: 0.7,
      evidence: ['SaaS signals in brand profile'],
      implies: ['Tone should be: efficient, clear, capable, trustworthy'],
    });
  }

  if (s.hasSerifFonts && !/gaming|saas/i.test(all)) {
    traits.push({
      trait: 'editorial-elegance',
      category: 'emotional-tone',
      confidence: 0.6,
      evidence: ['Serif typography suggests editorial/refined positioning'],
      implies: ['Emotional tone leans toward elegance, not utility'],
    });
  }

  return traits;
}

function inferVisualComplexity(s: BrandIdentitySnapshot): InferredTrait[] {
  const traits: InferredTrait[] = [];
  const all = `${s.industry} ${s.fontFamilies.join(' ')} ${s.assetTags.join(' ')}`;

  if (/glass|glassmorphism|blur|backdrop/i.test(all)) {
    traits.push({
      trait: 'glassmorphism',
      category: 'visual-complexity',
      confidence: 0.75,
      evidence: ['Glassmorphism/backdrop blur patterns detected'],
      implies: ['Prefers dimensional, layered visual language'],
    });
  }

  if (s.colors.length <= 3 && s.hasSerifFonts) {
    traits.push({
      trait: 'editorial-minimalism',
      category: 'visual-complexity',
      confidence: 0.65,
      evidence: ['Restrained palette + serif = editorial minimalism'],
      implies: ['Minimalist, typography-driven visual language'],
    });
  }

  if (s.hasGradients && s.colors.length >= 5) {
    traits.push({
      trait: 'expressive-color',
      category: 'visual-complexity',
      confidence: 0.55,
      evidence: ['Gradients + rich palette'],
      implies: ['Open to expressive, atmospheric visual effects'],
    });
  }

  return traits;
}

function inferDesignMaturity(s: BrandIdentitySnapshot): InferredTrait[] {
  const traits: InferredTrait[] = [];
  const all = `${s.industry} ${s.fontFamilies.join(' ')} ${s.assetTags.join(' ')}`;

  if (/radix|shadcn|headless/i.test(all)) {
    traits.push({
      trait: 'professional-ui-foundation',
      category: 'design-maturity',
      confidence: 0.7,
      evidence: ['Professional UI primitives in use (Radix/shadcn/Headless)'],
      implies: ['Design system maturity is high — avoid beginner-level questions'],
    });
  }

  if (s.spacingValues.length >= 4) {
    traits.push({
      trait: 'established-spacing-system',
      category: 'design-maturity',
      confidence: 0.65,
      evidence: ['Consistent spacing scale detected'],
      implies: ['Spacing system is mature and intentional'],
    });
  }

  if (s.colors.length >= 5 && s.fontFamilies.length >= 2) {
    traits.push({
      trait: 'sophisticated-design-system',
      category: 'design-maturity',
      confidence: 0.6,
      evidence: ['Multi-font, rich palette design system'],
      implies: ['Design language is sophisticated — ask strategic, not tactical, questions'],
    });
  }

  return traits;
}

function inferIndustryDomain(s: BrandIdentitySnapshot, prompt?: string): InferredTrait[] {
  const traits: InferredTrait[] = [];

  // Already inferred industry from brand analysis_json
  if (s.industry) {
    traits.push({
      trait: `industry: ${s.industry}`,
      category: 'industry-domain',
      confidence: 0.75,
      evidence: [`Industry '${s.industry}' from brand analysis`],
      implies: [`Design context is ${s.industry} — use domain-appropriate references`],
    });
  }

  // Detect from prompt keywords
  const p = (prompt || '').toLowerCase();
  if (/app store|appstore|store connect|screenshot/.test(p)) {
    traits.push({
      trait: 'format: app-store-screenshots',
      category: 'industry-domain',
      confidence: 0.9,
      evidence: ['App Store screenshot request detected'],
      implies: ['Format is known: iOS screenshots, 1290x2796px'],
    });
  }
  if (/landing|hero|homepage|website/.test(p)) {
    traits.push({
      trait: 'format: landing-page',
      category: 'industry-domain',
      confidence: 0.85,
      evidence: ['Landing page request detected'],
      implies: ['Format is known: web landing page'],
    });
  }
  if (/carousel|instagram|slides|stories/.test(p)) {
    traits.push({
      trait: 'format: social-carousel',
      category: 'industry-domain',
      confidence: 0.85,
      evidence: ['Social carousel request detected'],
      implies: ['Format is known: social media slides'],
    });
  }

  return traits;
}

function inferCompositionStyle(s: BrandIdentitySnapshot): InferredTrait[] {
  const traits: InferredTrait[] = [];

  if (s.logoWordmark && !s.logoSymbol) {
    traits.push({
      trait: 'wordmark-only-brand',
      category: 'composition-style',
      confidence: 0.55,
      evidence: ['Wordmark logo, no symbol detected'],
      implies: ['Typography-heavy composition, less icon-dependent'],
    });
  }

  if (s.logoSymbol && s.logoWordmark) {
    traits.push({
      trait: 'full-lockup-brand',
      category: 'composition-style',
      confidence: 0.55,
      evidence: ['Both symbol and wordmark in logo'],
      implies: ['Can use symbol solo or full lockup'],
    });
  }

  return traits;
}

function inferBrandPersonality(s: BrandIdentitySnapshot): InferredTrait[] {
  const traits: InferredTrait[] = [];
  const all = `${s.industry} ${s.fontFamilies.join(' ')} ${s.assetTags.join(' ')}`;

  if (/bold|strong|confident|disrupt|challeng/i.test(all)) {
    traits.push({
      trait: 'bold-personality',
      category: 'brand-personality',
      confidence: 0.6,
      evidence: ['Bold/confident language in brand profile'],
      implies: ['Design should feel assertive, not passive'],
    });
  }

  if (/calm|gentle|care|health|wellness|mind/i.test(all)) {
    traits.push({
      trait: 'calm-personality',
      category: 'brand-personality',
      confidence: 0.6,
      evidence: ['Calm/wellness language in brand profile'],
      implies: ['Design should feel soothing, trustworthy, gentle'],
    });
  }

  return traits;
}

function inferFromPrompt(prompt: string): InferredTrait[] {
  const traits: InferredTrait[] = [];
  const p = prompt.toLowerCase();

  // Detect emotional intent
  if (/convert|conversion|sales|buy|cta|sign.?up/i.test(p)) {
    traits.push({ trait: 'goal: conversion', category: 'emotional-tone', confidence: 0.7,
      evidence: ['Conversion-oriented language in prompt'],
      implies: ['Design goal is conversion, not just awareness'] });
  }
  if (/trust|credibility|enterprise|security|compliance/i.test(p)) {
    traits.push({ trait: 'goal: trust', category: 'emotional-tone', confidence: 0.7,
      evidence: ['Trust/credibility language in prompt'],
      implies: ['Design goal is establishing trust and authority'] });
  }
  if (/premium|luxury|exclusive|elite|high.?end/i.test(p)) {
    traits.push({ trait: 'goal: prestige', category: 'emotional-tone', confidence: 0.75,
      evidence: ['Premium/luxury language in prompt'],
      implies: ['Design goal is perceived prestige and exclusivity'] });
  }
  if (/brand|awareness|launch|announce|new product|introducing/i.test(p)) {
    traits.push({ trait: 'goal: awareness', category: 'emotional-tone', confidence: 0.65,
      evidence: ['Brand awareness language in prompt'],
      implies: ['Design goal is visibility and brand recognition'] });
  }

  // Detect audience
  if (/developer|dev |api|sdk|code/i.test(p)) {
    traits.push({ trait: 'audience: developers', category: 'industry-domain', confidence: 0.7,
      evidence: ['Developer audience signals in prompt'],
      implies: ['Audience is technical — prefer precision over marketing fluff'] });
  }
  if (/executive|c.?suite|enterprise|decision.?maker/i.test(p)) {
    traits.push({ trait: 'audience: executives', category: 'industry-domain', confidence: 0.7,
      evidence: ['Executive audience signals in prompt'],
      implies: ['Audience is decision-makers — value authority and clarity'] });
  }

  return traits;
}

// ── Result Builder ─────────────────────────────────────────────────────────

function buildResult(inferred: InferredTrait[]): InferenceResult {
  // Identify ambiguous areas (categories with no or low-confidence inferences)
  const allCategories: TraitCategory[] = [
    'typography', 'color-system', 'spacing', 'emotional-tone',
    'visual-complexity', 'design-maturity', 'industry-domain',
    'composition-style', 'brand-personality',
  ];

  const coveredCategories = new Set(inferred.map((t) => t.category));
  const ambiguousAreas = allCategories.filter((c) => !coveredCategories.has(c));

  // Check for low-confidence areas (any category where max confidence < 0.5)
  for (const cat of coveredCategories) {
    const maxConf = Math.max(...inferred.filter((t) => t.category === cat).map((t) => t.confidence));
    if (maxConf < 0.5 && !ambiguousAreas.includes(cat)) {
      ambiguousAreas.push(cat);
    }
  }

  // Build shouldAsk and shouldSkip
  const highConfidence = inferred.filter((t) => t.confidence >= 0.6);
  const shouldSkip = highConfidence.map((t) => t.trait);
  const shouldAsk = ambiguousAreas;

  // Build natural-language summary
  const summaryParts: string[] = ['DESIGN INTELLIGENCE — What I Already Know:\n'];

  for (const t of highConfidence) {
    summaryParts.push(`• ${t.trait} (confidence: ${Math.round(t.confidence * 100)}%)`);
    if (t.evidence.length > 0) summaryParts.push(`  Evidence: ${t.evidence[0]}`);
  }

  if (ambiguousAreas.length > 0) {
    summaryParts.push(`\nSTILL AMBIGUOUS — Worth Asking About: ${ambiguousAreas.join(', ')}`);
  }

  if (shouldSkip.length > 0) {
    summaryParts.push(`\nDO NOT ASK ABOUT: ${shouldSkip.slice(0, 8).join('; ')}`);
  }

  return {
    inferred,
    ambiguousAreas,
    shouldAsk: shouldAsk.slice(0, 5),
    shouldSkip: shouldSkip.slice(0, 10),
    summary: summaryParts.join('\n'),
  };
}

// ── Question quality scorer ────────────────────────────────────────────────

const BANNED_QUESTION_PATTERNS = [
  /what colou?rs?/i,
  /what (is )?the colou?r/i,
  /what font/i,
  /what typography/i,
  /what style/i,
  /light or dark/i,
  /dark or light/i,
  /what layout/i,
  /what size/i,
  /what dimensions/i,
  /how many (pages|screens|slides)/i,
  /sans.?serif or serif/i,
  /serif or sans/i,
];

export function isLowQualityQuestion(question: string): boolean {
  return BANNED_QUESTION_PATTERNS.some((p) => p.test(question));
}

const STRATEGIC_QUESTION_STARTERS = [
  'Should the',
  'Do you want',
  'Which feels more',
  'Is the goal',
  'Would you prefer',
  'How should the interface feel',
  'Should the visual hierarchy',
  'Which emotional direction',
  'Should the product feel',
  'What kind of',
];

export function isStrategicQuestion(question: string): boolean {
  return STRATEGIC_QUESTION_STARTERS.some((s) => question.toLowerCase().startsWith(s.toLowerCase()));
}

// ── Build enhanced clarify prompt ──────────────────────────────────────────

export function buildInterrogationPrompt(
  inferenceResult: InferenceResult,
  conversationLog: string,
): string {
  const { summary, shouldAsk } = inferenceResult;
  const hasBrandContext = /EXISTING BRAND CONTEXT/.test(conversationLog);

    return `You are fydesign's Senior Creative Director. You deeply understand design strategy, brand positioning, and visual taste. You have already studied the brand and its assets extensively.

${summary}

CONVERSATION HISTORY:
${conversationLog}

STRATEGIC QUESTIONING RULES:
${hasBrandContext ? `0. ⚠️ BRAND CONTEXT IS PROVIDED ABOVE. You ALREADY KNOW what this app is, what it does, and what its identity is. NEVER ask "what is the app about", "what industry", "what's the purpose" — those facts are settled.
` : ''}1. ${shouldAsk.length > 0 ? `AMBIGUOUS AREAS DETECTED: ${shouldAsk.join(', ')}. You MUST ask about at least 1 of these areas. Do NOT skip to ready=true when there are ambiguous areas — the questions make the final design dramatically better.` : 'ONLY ask about what is still genuinely ambiguous — never ask about what is already known.'}
2. Ask 1-2 strategic questions. These are NOT friction — they are the difference between a generic carousel and an editorial masterpiece. The user WANTS to be asked.
3. Be perceptive, like you've already deeply studied the brand. Show in your questions that you understood — reference the brand by name, its industry, its unique positioning.
4. For each question, provide 3-4 specific, actionable options that a creative director would ask. Questions must be about CREATIVE DIRECTION (e.g. "Should the visual tone feel editorial-premium or energetically bold?", "Which emotional hook should lead — urgency, aspiration, or social proof?") — never about basic facts.
5. ONLY set "ready": true when the user has ANSWERED your questions in a prior message. On the FIRST message, always ask.
6. Focus questions on: ${shouldAsk.length > 0 ? shouldAsk.join(', ') : 'emotional tone, visual direction, and compositional strategy'}.
7. FORBIDDEN questions (auto-fail): "what type of app is it?", "what industry?", "what does the user feel?", "what's the purpose?" — these are facts you must deduce, not ask.
8. Always respond in the same language as the user's message.

You must reply in JSON format with exactly these fields:
- "ready": boolean. True ONLY if you have enough information to write an amazing, detailed prompt for the designer. False if you still need to ask a question.
- "questions": array. If ready=false, your questions for the user. Each question must have:
  - "id": string (unique identifier)
  - "question": string (the question text)
  - "options": string[] (array of 3-4 options)
  - "allowCustom": boolean (usually true)
- "enrichedPrompt": string. If ready=true, a massive, highly detailed prompt that will be sent to the design engine. It should include everything discussed.

Return JSON example:
{
  "ready": false,
  "questions": [
    {
      "id": "tone",
      "question": "Para entender mejor, ¿qué tono visual prefieres?",
      "options": ["Oscuro y elegante", "Claro y minimalista", "Vibrante y atrevido"],
      "allowCustom": true
    }
  ],
  "enrichedPrompt": ""
}`;
}
