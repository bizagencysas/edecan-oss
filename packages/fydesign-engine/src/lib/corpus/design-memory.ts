// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Persistent Design Memory — Learns why things look premium over time         ║
// ║  Categories: layout, spacing, typography, color, animation, composition,     ║
// ║              restraint, anti-pattern, quality-signal                         ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import crypto from 'crypto';
import {
  getLocalDesignMemoryStats,
  loadDesignMemoryInsights,
  saveDesignMemoryInsights,
  type DesignMemoryRow,
} from '@/lib/db';
import { embedText, isEmbeddingAvailable, EMBEDDING_DIM } from './embedding';
import type { HeuristicPattern } from './heuristic-extractor';

export interface DesignInsight {
  id: string;
  category: 'layout' | 'spacing' | 'typography' | 'color' | 'animation' | 'composition' | 'restraint' | 'anti-pattern' | 'quality-signal';
  insight: string;
  why: string;
  signals: string[];
  confidence: number;
  evidenceCount: number;
  sourceRepos: string[];
  qualityScore: number;
  embedding?: number[] | null;
}

// ── Built-in Design Memory (seed knowledge) ───────────────────────────────

const SEED_MEMORY: Omit<DesignInsight, 'id' | 'embedding'>[] = [
  // ── Layout wisdom ───────────────────────────────────────────────────────
  {
    category: 'layout',
    insight: 'Asymmetric layouts with strong focal points feel premium; centered layouts feel template-like',
    why: 'Centered layouts are the default — they require no compositional decisions. Asymmetry shows intentionality.',
    signals: ['asymmetric', 'focal-point', 'rule-of-thirds', 'visual-weight'],
    confidence: 0.85, evidenceCount: 1, sourceRepos: [], qualityScore: 0.85,
  },
  {
    category: 'layout',
    insight: '12-column grids with asymmetric content placement create visual sophistication',
    why: 'Grid systems provide invisible structure that makes content feel organized without looking rigid.',
    signals: ['grid', '12-column', 'asymmetric', 'structure'],
    confidence: 0.8, evidenceCount: 1, sourceRepos: [], qualityScore: 0.8,
  },
  {
    category: 'layout',
    insight: 'One dominant focal point per view creates visual authority',
    why: 'Multiple competing focal points create visual noise. A single clear entry point guides the eye with confidence.',
    signals: ['focal-point', 'hero', 'hierarchy', 'entry-point'],
    confidence: 0.9, evidenceCount: 1, sourceRepos: [], qualityScore: 0.9,
  },

  // ── Spacing wisdom ──────────────────────────────────────────────────────
  {
    category: 'spacing',
    insight: 'Generous whitespace is the cheapest luxury material in design',
    why: 'Whitespace costs nothing to add but communicates confidence, restraint, and premium positioning instantly.',
    signals: ['whitespace', 'padding', 'margin', 'breathing-room'],
    confidence: 0.9, evidenceCount: 1, sourceRepos: [], qualityScore: 0.9,
  },
  {
    category: 'spacing',
    insight: 'Uneven spacing creates visual rhythm; equal spacing everywhere feels mechanical',
    why: 'Rhythm comes from variation. Equal spacing is the absence of rhythm — it feels like a template grid.',
    signals: ['rhythm', 'uneven', 'visual-rhythm', 'proximity'],
    confidence: 0.8, evidenceCount: 1, sourceRepos: [], qualityScore: 0.8,
  },
  {
    category: 'spacing',
    insight: '120-200px section spacing defines premium rhythm; 40-80px feels cramped',
    why: 'Premium brands use inter-section spacing that gives each section its own visual territory.',
    signals: ['section-spacing', '160px', 'generous', 'territory'],
    confidence: 0.75, evidenceCount: 1, sourceRepos: [], qualityScore: 0.75,
  },

  // ── Typography wisdom ──────────────────────────────────────────────────
  {
    category: 'typography',
    insight: 'Strong type hierarchy (clear size jumps between levels) is the #1 signal of design quality',
    why: 'Without typographic hierarchy, users cannot scan. All premium interfaces have immediately visible type hierarchy.',
    signals: ['hierarchy', 'scale', 'heading', 'body', 'contrast'],
    confidence: 0.95, evidenceCount: 1, sourceRepos: [], qualityScore: 0.95,
  },
  {
    category: 'typography',
    insight: 'One display font + one body font is optimal; three fonts creates visual tax',
    why: 'Font pairing requires restraint. Each additional font family adds cognitive load without proportional benefit.',
    signals: ['font-pairing', 'restraint', 'display', 'body'],
    confidence: 0.8, evidenceCount: 1, sourceRepos: [], qualityScore: 0.8,
  },
  {
    category: 'typography',
    insight: 'Use the PROJECT FONTS from the brand config. Create variety through weight extremes (100-900), optical sizes (Display vs Text), and spacing — not by swapping font families.',
    why: 'Brand consistency requires using the configured fonts. Variety comes from HOW you use those fonts, not which fonts you pick.',
    signals: ['brand-fonts', 'weight', 'optical-size', 'spacing', 'consistency'],
    confidence: 0.95, evidenceCount: 1, sourceRepos: [], qualityScore: 0.95,
  },

  // ── Color wisdom ───────────────────────────────────────────────────────
  {
    category: 'color',
    insight: 'Restrained color (3-5 palette colors) feels premium; rainbow palettes feel cheap',
    why: 'Color restraint communicates confidence. When everything is colorful, nothing is emphasized.',
    signals: ['restrained', 'palette', 'limited', '3-colors', 'accent'],
    confidence: 0.85, evidenceCount: 1, sourceRepos: [], qualityScore: 0.85,
  },
  {
    category: 'color',
    insight: 'Use color as seasoning (accents only), not the main ingredient of the UI',
    why: 'When color is used sparingly, each use carries meaning. Overuse dilutes all color signals.',
    signals: ['accent', 'seasoning', 'sparing', 'meaning'],
    confidence: 0.8, evidenceCount: 1, sourceRepos: [], qualityScore: 0.8,
  },

  // ── Animation wisdom ───────────────────────────────────────────────────
  {
    category: 'animation',
    insight: '200-400ms micro-interactions feel responsive; animations over 1s block interaction',
    why: 'Users perceive <100ms as instant, <400ms as responsive, >1s as waiting. Animation should never cause waiting.',
    signals: ['200ms', '400ms', 'micro-interaction', 'duration', 'responsive'],
    confidence: 0.9, evidenceCount: 1, sourceRepos: [], qualityScore: 0.9,
  },
  {
    category: 'animation',
    insight: 'Ease-out for entering elements, ease-in for exiting — this matches physical intuition',
    why: 'Objects in the real world decelerate when arriving and accelerate when leaving. UI should match.',
    signals: ['ease-out', 'ease-in', 'easing', 'physical', 'intuition'],
    confidence: 0.85, evidenceCount: 1, sourceRepos: [], qualityScore: 0.85,
  },

  // ── Composition wisdom ─────────────────────────────────────────────────
  {
    category: 'composition',
    insight: 'Z-pattern composition for landing pages, F-pattern for reading interfaces',
    why: 'Western readers scan in Z (left-right, diagonal, left-right) for scanning and F (left-right, left-down) for reading.',
    signals: ['z-pattern', 'f-pattern', 'scan', 'read', 'eye-tracking'],
    confidence: 0.8, evidenceCount: 1, sourceRepos: [], qualityScore: 0.8,
  },

  // ── Restraint ──────────────────────────────────────────────────────────
  {
    category: 'restraint',
    insight: 'The best designers are the best editors — subtract until nothing more can be removed',
    why: 'Every element added dilutes the impact of every other element. Design quality = signal-to-noise ratio.',
    signals: ['subtraction', 'editing', 'minimal', 'essential', 'noise-reduction'],
    confidence: 0.9, evidenceCount: 1, sourceRepos: [], qualityScore: 0.9,
  },
  {
    category: 'restraint',
    insight: 'One visual technique per section (glass, shadow, gradient, illustration) — never combine',
    why: 'Multiple visual techniques compete for attention and create visual chaos. One technique = clarity.',
    signals: ['one-technique', 'clarity', 'focus', 'restraint'],
    confidence: 0.85, evidenceCount: 1, sourceRepos: [], qualityScore: 0.85,
  },
  {
    category: 'restraint',
    insight: 'If a design works in grayscale, color will make it better. If it needs color to work, it\'s broken.',
    why: 'Layout, spacing, and hierarchy must stand on their own. Color is enhancement, not foundation.',
    signals: ['grayscale', 'structure-first', 'color-last', 'foundation'],
    confidence: 0.9, evidenceCount: 1, sourceRepos: [], qualityScore: 0.9,
  },

  // ── Anti-patterns ──────────────────────────────────────────────────────
  {
    category: 'anti-pattern',
    insight: 'Three equal feature cards above the fold is the #1 sign of a template design',
    why: 'This pattern appears in 90%+ of startup templates. It communicates "I used a template" instantly.',
    signals: ['feature-cards', 'three-columns', 'equal', 'template'],
    confidence: 0.9, evidenceCount: 1, sourceRepos: [], qualityScore: 0.9,
  },
  {
    category: 'anti-pattern',
    insight: 'Purple-to-blue gradient backgrounds signal "startup template" more than any other visual cue',
    why: 'This specific gradient has become the universal signal of generic SaaS design. Any other color choice is better.',
    signals: ['gradient', 'purple-blue', 'startup', 'generic', 'template'],
    confidence: 0.95, evidenceCount: 1, sourceRepos: [], qualityScore: 0.95,
  },
  {
    category: 'anti-pattern',
    insight: 'Mockups floating on gradient blobs look like every other startup — use real product screenshots instead',
    why: 'Floating mockups on blobs are the "stock photo" of SaaS. Real product surfaces build trust.',
    signals: ['mockup', 'blob', 'floating', 'gradient', 'generic'],
    confidence: 0.85, evidenceCount: 1, sourceRepos: [], qualityScore: 0.85,
  },
  {
    category: 'anti-pattern',
    insight: 'Fake data, impossible metrics, and placeholder content destroy design credibility',
    why: 'Users can instantly detect fake data. Real-looking content makes design feel authentic and trustworthy.',
    signals: ['fake-data', 'placeholder', 'lorem-ipsum', 'credibility', 'authentic'],
    confidence: 0.9, evidenceCount: 1, sourceRepos: [], qualityScore: 0.9,
  },

  // ── Quality signals ────────────────────────────────────────────────────
  {
    category: 'quality-signal',
    insight: 'Real product screenshots (not illustrations) are the strongest signal of premium SaaS design',
    why: 'Showing the actual product demonstrates confidence and transparency. Illustrations hide the product.',
    signals: ['screenshot', 'product', 'real', 'transparency', 'confidence'],
    confidence: 0.9, evidenceCount: 1, sourceRepos: [], qualityScore: 0.9,
  },
  {
    category: 'quality-signal',
    insight: 'Consistent 4px/8px spacing scale signals professional execution',
    why: 'Even-numbered spacing scales create visual rhythm. Odd/random spacing reveals ad-hoc design decisions.',
    signals: ['4px', '8px', 'spacing-scale', 'consistent', 'professional'],
    confidence: 0.8, evidenceCount: 1, sourceRepos: [], qualityScore: 0.8,
  },
  {
    category: 'quality-signal',
    insight: 'Monospace for data, proportional for text — this distinction alone signals design maturity',
    why: 'Using monospace for numbers/tables and proportional fonts for prose shows typographic awareness.',
    signals: ['monospace', 'data', 'tabular', 'proportional', 'typography'],
    confidence: 0.75, evidenceCount: 1, sourceRepos: [], qualityScore: 0.75,
  },

  // ── Creative variety (never repeat the same visual language) ────────────
  {
    category: 'typography',
    insight: 'Use the BRAND FONTS from the project config. Create drama by varying weight (Thin to Black), style (Regular vs Italic), and optical size (Display vs Text vs Rounded) within the same family.',
    why: 'Brand consistency comes from using the configured fonts. SF Pro has Display, Text, Rounded, Compact — that alone gives massive variety without breaking brand.',
    signals: ['brand-fonts', 'weight-variety', 'optical-size', 'display', 'personality'],
    confidence: 0.95, evidenceCount: 3, sourceRepos: [], qualityScore: 0.95,
  },
  {
    category: 'composition',
    insight: 'Rotate composition archetypes per generation: split-screen, full-bleed, layered depth, broken grid, diagonal tension, radial focus, kinetic typography. NEVER default to the same layout.',
    why: 'Repeating layouts is the death of creative design. Each generation should feel like a different designer with a different vision.',
    signals: ['variety', 'composition', 'archetype', 'layout-rotation', 'different'],
    confidence: 0.95, evidenceCount: 3, sourceRepos: [], qualityScore: 0.95,
  },
  {
    category: 'quality-signal',
    insight: 'Copy must have creative-director voice: specific > generic, provocative > safe, short > long. "You\'re not a hotel. You\'re a cultural statement." beats "A unique hospitality experience."',
    why: 'Generic marketing copy ("Our solution provides...") instantly signals template. Editorial voice signals craft and intentionality.',
    signals: ['copy', 'voice', 'editorial', 'creative-director', 'specific'],
    confidence: 0.95, evidenceCount: 3, sourceRepos: [], qualityScore: 0.95,
  },
  {
    category: 'color',
    insight: 'INVENT a new color world per design — warm earth, cold steel, tropical, muted Scandinavian, neon-on-dark, jewel tones. Use ONE accent color surgically. Never the same palette twice.',
    why: 'Color is emotional identity. Repeating the same palette makes every design feel like the same brand. Variety in color = variety in emotion.',
    signals: ['color-variety', 'accent', 'surgical', 'restraint', 'emotional-color'],
    confidence: 0.9, evidenceCount: 2, sourceRepos: [], qualityScore: 0.9,
  },
  {
    category: 'composition',
    insight: 'Premium structural elements (accent rules, section dividers, stat blocks, metadata footers) are TECHNIQUES available to any style — not exclusive to editorial serif layouts.',
    why: 'These elements signal intentional design when used with any typography or color system. They work in geometric sans, in bold slab, in minimal grotesque.',
    signals: ['structural', 'accent-rule', 'stat-block', 'footer', 'technique'],
    confidence: 0.85, evidenceCount: 2, sourceRepos: [], qualityScore: 0.85,
  },
  {
    category: 'typography',
    insight: 'Typographic contrast within the brand font family: pair weight extremes (Thin + Black), style extremes (Regular + Italic), and size extremes (120px display + 11px labels). Different combinations each time.',
    why: 'Contrast in type creates tension and resolution. The same brand font at weight 200 vs 900 creates more drama than two different fonts at similar weights.',
    signals: ['contrast', 'weight-extremes', 'type-drama', 'brand-font', 'tension'],
    confidence: 0.85, evidenceCount: 2, sourceRepos: [], qualityScore: 0.85,
  },
];

// ── Extract insights from heuristic patterns ──────────────────────────────

export function extractInsightsFromPatterns(patterns: HeuristicPattern[]): Omit<DesignInsight, 'id' | 'embedding'>[] {
  const insights: Omit<DesignInsight, 'id' | 'embedding'>[] = [];

  for (const pattern of patterns) {
    // Layout patterns → layout + composition insights
    if (pattern.patternType === 'layout-system' || pattern.patternType === 'grid-system') {
      if (pattern.rules.length >= 2) {
        insights.push({
          category: 'layout',
          insight: pattern.rules.slice(0, 2).join('. '),
          why: `Extracted from ${pattern.metadata.repo} (${pattern.metadata.framework})`,
          signals: pattern.signals,
          confidence: pattern.metadata.confidence || 0.6,
          evidenceCount: 1,
          sourceRepos: [pattern.metadata.repo],
          qualityScore: 0.5,
        });
      }
      // Grid patterns → spacing insights
      if (pattern.patternType === 'grid-system' && pattern.cssMoves.length > 2) {
        insights.push({
          category: 'spacing',
          insight: `Grid-based responsive spacing using ${pattern.cssMoves.slice(0, 3).join(', ')}`,
          why: `From ${pattern.metadata.repo}: grid-based layouts create consistent spacing rhythm`,
          signals: ['grid', 'spacing', 'responsive'],
          confidence: 0.6,
          evidenceCount: 1,
          sourceRepos: [pattern.metadata.repo],
          qualityScore: 0.4,
        });
      }
    }

    // Animation patterns → animation insights
    if (pattern.patternType === 'animation-system') {
      insights.push({
        category: 'animation',
        insight: pattern.summary || pattern.rules[0] || pattern.title,
        why: `Animation strategy from ${pattern.metadata.repo}`,
        signals: pattern.signals,
        confidence: pattern.metadata.confidence || 0.6,
        evidenceCount: 1,
        sourceRepos: [pattern.metadata.repo],
        qualityScore: 0.45,
      });
    }

    // Typography patterns → typography insights
    if (pattern.patternType === 'typography-system') {
      insights.push({
        category: 'typography',
        insight: pattern.rules.slice(0, 2).join('. '),
        why: `Typography system from ${pattern.metadata.repo}`,
        signals: pattern.signals,
        confidence: pattern.metadata.confidence || 0.7,
        evidenceCount: 1,
        sourceRepos: [pattern.metadata.repo],
        qualityScore: 0.5,
      });
    }

    // Color patterns → color insights
    if (pattern.patternType === 'color-system') {
      insights.push({
        category: 'color',
        insight: pattern.rules.slice(0, 2).join('. '),
        why: `Color system from ${pattern.metadata.repo}`,
        signals: pattern.signals,
        confidence: pattern.metadata.confidence || 0.7,
        evidenceCount: 1,
        sourceRepos: [pattern.metadata.repo],
        qualityScore: 0.5,
      });
    }

    // Anti-patterns from pattern avoid lists
    for (const avoid of pattern.avoid.slice(0, 2)) {
      if (avoid.length > 10) {
        insights.push({
          category: 'anti-pattern',
          insight: avoid,
          why: `Anti-pattern identified in ${pattern.metadata.repo}`,
          signals: ['avoid', pattern.patternType],
          confidence: 0.5,
          evidenceCount: 1,
          sourceRepos: [pattern.metadata.repo],
          qualityScore: 0.35,
        });
      }
    }

    // CSS snippets → quality-signal insights (actual reusable code from repos)
    for (const snippet of pattern.snippets.slice(0, 2)) {
      if (snippet.code && snippet.code.length > 20 && snippet.code.length < 500) {
        insights.push({
          category: 'quality-signal',
          insight: `${pattern.title}: ${snippet.kind} technique — ${snippet.code.slice(0, 200)}`,
          why: `Real CSS/SVG snippet from ${pattern.metadata.repo} — proven in production`,
          signals: [snippet.kind, pattern.patternType, 'snippet', 'production-code'],
          confidence: pattern.metadata.confidence || 0.7,
          evidenceCount: 1,
          sourceRepos: [pattern.metadata.repo],
          qualityScore: 0.65,
        });
      }
    }

    // CSS moves → quality-signal insights (techniques worth remembering)
    if (pattern.cssMoves.length >= 3) {
      insights.push({
        category: 'quality-signal',
        insight: `CSS techniques from ${pattern.metadata.repo}: ${pattern.cssMoves.slice(0, 5).join('; ')}`,
        why: `Proven CSS approaches from a real production codebase`,
        signals: ['css-technique', pattern.patternType, 'production'],
        confidence: 0.6,
        evidenceCount: 1,
        sourceRepos: [pattern.metadata.repo],
        qualityScore: 0.5,
      });
    }
  }

  return insights;
}

// ── Seed + persist design memory ──────────────────────────────────────────

export async function seedDesignMemory(): Promise<number> {
  const rows = await Promise.all(
    SEED_MEMORY.map(async (seed): Promise<Omit<DesignMemoryRow, 'created_at' | 'updated_at'>> => {
      const embedding = isEmbeddingAvailable()
        ? await embedText(`${seed.category}: ${seed.insight}. Why: ${seed.why}`).catch(() => null)
        : null;
      return {
        id: `dmem-seed-${crypto.createHash('sha256').update(`${seed.category}:${seed.insight}`).digest('hex').slice(0, 16)}`,
        category: seed.category,
        insight: seed.insight,
        why: seed.why,
        signals: seed.signals,
        confidence: seed.confidence,
        evidence_count: seed.evidenceCount,
        source_repos: seed.sourceRepos,
        quality_score: seed.qualityScore,
        embedding,
      };
    }),
  );
  return saveDesignMemoryInsights(rows);
}

// ── Learn from repo patterns ──────────────────────────────────────────────

export async function learnFromPatterns(patterns: HeuristicPattern[], repoFullName: string): Promise<number> {
  const insights = extractInsightsFromPatterns(patterns);
  const rows = await Promise.all(
    insights.map(async (insight): Promise<Omit<DesignMemoryRow, 'created_at' | 'updated_at'>> => ({
      id: `dmem-${crypto.randomUUID().slice(0, 12)}`,
      category: insight.category,
      insight: insight.insight,
      why: insight.why,
      signals: insight.signals,
      confidence: insight.confidence,
      evidence_count: insight.evidenceCount,
      source_repos: repoFullName ? [repoFullName] : [],
      quality_score: insight.qualityScore,
      embedding: isEmbeddingAvailable()
        ? await embedText(`${insight.category}: ${insight.insight}`).catch(() => null)
        : null,
    })),
  );
  return saveDesignMemoryInsights(rows);
}

// ── Retrieve design memory ────────────────────────────────────────────────

export async function retrieveDesignMemory(
  categories?: string[],
  query?: string,
  limit = 10,
): Promise<DesignInsight[]> {
  if ((await getLocalDesignMemoryStats()).total === 0) await seedDesignMemory();
  const embedding = query && isEmbeddingAvailable()
    ? await embedText(query).catch(() => null)
    : null;
  const rows = await loadDesignMemoryInsights({ categories, query, embedding, limit });
  return rows.map((row) => ({
    id: row.id,
    category: row.category as DesignInsight['category'],
    insight: row.insight,
    why: row.why,
    signals: row.signals,
    confidence: row.confidence,
    evidenceCount: row.evidence_count,
    sourceRepos: row.source_repos,
    qualityScore: row.quality_score,
  }));
}

export async function getDesignMemoryStats(): Promise<{
  total: number;
  byCategory: Record<string, number>;
  avgQuality: number;
}> {
  if ((await getLocalDesignMemoryStats()).total === 0) await seedDesignMemory();
  return getLocalDesignMemoryStats();
}
