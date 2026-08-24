// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Ranking — Pattern quality scoring, re-ranking, domain matching              ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import type { HeuristicPattern } from './heuristic-extractor';

export interface PatternWithScore {
  pattern: HeuristicPattern;
  vectorSimilarity?: number;
  qualityScore: number;
  domainMatch: number;
  recency: number;
  sourceDiversity: number;
  brandFit: number;
  finalScore?: number;
}

export interface RetrievalQuery {
  brandName?: string;
  mode: string;
  industry?: string;
  prompt: string;
  styleNotes?: string;
  creativeMode?: string;
}

const REJECTION_PATTERNS = [
  { keyword: /lorem ipsum/i, reason: 'placeholder content' },
  { keyword: /copyright infringement|rip-off|stolen/i, reason: 'copyright concerns' },
  { keyword: /inaccessible|no alt text/i, reason: 'accessibility issues' },
];

export function scorePatternQuality(pattern: HeuristicPattern): number {
  const scores: Record<string, number> = {
    layoutPrecision: scoreDimension(pattern, ['grid', 'layout', 'spacing', 'alignment', 'columns'], 0.3),
    visualSophistication: scoreDimension(pattern, ['gradient', 'glass', 'shadow', 'depth', 'mask', 'blur', 'blend'], 0.2),
    typographyMaturity: scoreDimension(pattern, ['typography', 'font', 'serif', 'sans', 'scale', 'heading', 'text'], 0.15),
    colorDiscipline: scoreDimension(pattern, ['color', 'palette', 'token', 'accent', 'neutral', 'primary'], 0.1),
    animationTaste: pattern.patternType === 'animation-system' ? 0.7 : 0.5,
    responsiveness: scoreDimension(pattern, ['responsive', 'breakpoint', 'mobile', 'desktop', 'sm', 'md', 'lg'], 0.05),
    accessibility: scoreDimension(pattern, ['accessible', 'contrast', 'focus', 'sr-only', 'aria'], 0.05),
    reusePotential: Math.min(pattern.snippets.length * 0.2 + pattern.cssMoves.length * 0.1, 1),
    novelty: pattern.metadata.confidence ? 0.5 + (pattern.metadata.confidence - 0.5) * 0.5 : 0.5,
    brandTransferability: scoreDimension(pattern, ['brand', 'premium', 'trust', 'editorial', 'luxury', 'fintech'], 0.1),
  };

  const weights = {
    layoutPrecision: 0.2,
    visualSophistication: 0.2,
    typographyMaturity: 0.15,
    colorDiscipline: 0.1,
    animationTaste: 0.05,
    responsiveness: 0.05,
    accessibility: 0.05,
    reusePotential: 0.1,
    novelty: 0.05,
    brandTransferability: 0.05,
  };

  return Object.entries(scores).reduce((sum, [key, score]) => sum + score * (weights[key as keyof typeof weights] || 0), 0);
}

function scoreDimension(pattern: HeuristicPattern, signals: string[], baseWeight: number): number {
  const haystack = JSON.stringify([pattern.tags, pattern.signals, pattern.rules, pattern.cssMoves]).toLowerCase();
  const matches = signals.filter((s) => haystack.includes(s.toLowerCase())).length;
  return Math.min(baseWeight + matches * 0.2, 0.95);
}

export function shouldRejectPattern(pattern: HeuristicPattern): { reject: boolean; reason?: string } {
  const haystack = [
    pattern.title,
    pattern.summary,
    ...pattern.tags,
    ...pattern.appliesTo,
    ...pattern.signals,
  ].join(' ').toLowerCase();

  for (const rp of REJECTION_PATTERNS) {
    if (rp.keyword.test(haystack)) {
      return { reject: true, reason: rp.reason };
    }
  }

  // Too few rules or CSS moves
  if (pattern.rules.length < 1 && pattern.cssMoves.length < 1) {
    return { reject: true, reason: 'too little design intelligence' };
  }

  // Too many snippets (sign of copied code)
  const totalCodeLen = pattern.snippets.reduce((sum, s) => sum + s.code.length, 0);
  if (totalCodeLen > 5000) {
    return { reject: true, reason: 'too much code in snippets' };
  }

  return { reject: false };
}

export function rerankPatterns(
  patterns: PatternWithScore[],
  query: RetrievalQuery,
): PatternWithScore[] {
  for (const p of patterns) {
    p.finalScore =
      (p.vectorSimilarity || 0.5) * 0.45 +
      p.qualityScore * 0.25 +
      p.domainMatch * 0.15 +
      p.recency * 0.05 +
      p.sourceDiversity * 0.05 +
      p.brandFit * 0.05;
  }

  patterns.sort((a, b) => (b.finalScore || 0) - (a.finalScore || 0));

  // Compute source diversity
  const sourceCounts = new Map<string, number>();
  for (const p of patterns) {
    const source = p.pattern.metadata?.repo || 'unknown';
    sourceCounts.set(source, (sourceCounts.get(source) || 0) + 1);
  }
  for (const p of patterns) {
    const source = p.pattern.metadata?.repo || 'unknown';
    const count = sourceCounts.get(source) || 1;
    p.sourceDiversity = 1 / count;
  }

  return patterns;
}

export function computeDomainMatch(pattern: HeuristicPattern, domain: string): number {
  if (!domain || domain === 'general') return 0.6;

  const domainLower = domain.toLowerCase();
  const haystack = [
    ...pattern.tags,
    ...pattern.appliesTo,
    ...pattern.signals,
    pattern.patternType,
    pattern.summary,
  ].join(' ').toLowerCase();

  const domainKeywords: Record<string, string[]> = {
    fintech: ['fintech', 'finance', 'bank', 'payment', 'credit', 'invest', 'money', 'trust', 'security'],
    luxury: ['luxury', 'premium', 'exclusive', 'vip', 'elegant', 'refined', 'editorial'],
    editorial: ['editorial', 'magazine', 'article', 'typography', 'serif', 'content'],
    saas: ['saas', 'dashboard', 'platform', 'app', 'product', 'software', 'admin'],
    dashboard: ['dashboard', 'analytics', 'metrics', 'chart', 'table', 'data', 'stats'],
    landing: ['landing', 'hero', 'page', 'conversion', 'cta', 'marketing'],
    ecommerce: ['shop', 'store', 'product', 'cart', 'commerce', 'retail'],
    'app-store': ['screenshot', 'app store', 'mockup', 'phone', 'mobile', 'ios', 'android'],
    carousel: ['carousel', 'slider', 'social', 'post', 'instagram', 'story'],
    ads: ['ad', 'paid', 'conversion', 'banner', 'social ad'],
  };

  const keywords = domainKeywords[domainLower] || [domainLower];
  const matches = keywords.filter((kw) => haystack.includes(kw)).length;
  return Math.min(matches / Math.max(keywords.length * 0.4, 1), 1);
}

export function computeSourceDiversity(patterns: PatternWithScore[]): Map<string, number> {
  const sourceCounts = new Map<string, number>();
  for (const p of patterns) {
    const source = p.pattern.metadata?.repo || 'unknown';
    sourceCounts.set(source, (sourceCounts.get(source) || 0) + 1);
  }
  return sourceCounts;
}
