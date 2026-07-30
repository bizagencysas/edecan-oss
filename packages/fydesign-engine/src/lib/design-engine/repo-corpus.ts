// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Repo Brain Corpus                                                         ║
// ║  Retrieval layer for design patterns learned from many repositories.        ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import type { DesignMode } from './prompts';
import { loadDesignCorpusPatterns } from '@/lib/db';
import type { CreativeMode } from './prompts/fydesign';

export interface RepoBrainPattern {
  id: string;
  title: string;
  appliesTo: string[];
  signals: string[];
  rules: string[];
  cssMoves: string[];
  avoid: string[];
  sourceRepos: string[];
  weight?: number;
}

const BUILTIN_PATTERNS: RepoBrainPattern[] = [
  {
    id: 'css-mastery-toolkit',
    title: 'Advanced CSS/SVG visual toolkit',
    appliesTo: ['god', 'general', 'post', 'carousel', 'landing', 'ad', 'email', 'mockup', 'deck'],
    signals: ['creative', 'premium', 'god', 'wow', 'different', 'alive'],
    rules: [
      'Use browser-native artistry that supports the concept and exports cleanly as PNG.',
      'When the brief calls for premium feel, depth helps — mesh gradients, noise, pattern, layered shadows, glass, perspective. Flat is fine when the concept calls for it.',
      'Modern CSS is your toolkit: gradient text, layered shadows, glass panels, mesh gradients (oklch), noise textures, perspective transforms, clip-path, color-mix(). Reach for them when they earn their cost.',
    ],
    cssMoves: [
      'Multi-stop radial mesh gradients with oklch() for background depth',
      'Noise overlay: inline SVG feTurbulence + mix-blend-mode',
      'Layered box-shadows (3-4 layers) for premium elevation',
      'Glass: backdrop-filter: blur(20px) saturate(180%) with oklch bg',
      'Gradient text: linear-gradient + background-clip: text',
      'CSS masks and clip-path for creative layouts',
      'SVG filters, displacement maps for organic textures',
      'Perspective transforms on mockup elements',
      'color-mix(in oklch, ...) for systematic surfaces',
    ],
    avoid: [
      'External scripts',
      'Motion that ruins static export',
      'Flat monochrome canvases',
      'Single-layer box-shadow on cards',
    ],
    sourceRepos: ['creative coding', 'award sites', 'visualization systems'],
    weight: 1.3,
  },
  {
    id: 'composition-variety',
    title: 'Compositional variety engine',
    appliesTo: ['god', 'general', 'post', 'carousel', 'landing', 'ad', 'mockup', 'deck'],
    signals: ['design', 'creative', 'visual', 'layout'],
    rules: [
      'Aim for variety across designs in the same set. Composition archetypes available: split-screen, full-bleed hero, layered depth, editorial grid, diagonal tension, radial focus, broken grid, kinetic typography, data-viz-as-hero, photographic editorial.',
      'Use the brand font from the brand config. You can vary weight, size, and spacing within that family.',
      'Color palettes can lean into different emotional worlds depending on the brief: warm earth tones, cold industrial steel, tropical vibrance, muted neutrals, high-contrast editorial, pastel softness, monochromatic intensity.',
    ],
    cssMoves: [
      'Asymmetric grid-template-areas for editorial tension',
      'Full-viewport section compositions with scroll-snap',
      'Overlapping z-index layers for spatial depth',
      'Diagonal clip-path sections for dynamic energy',
    ],
    avoid: [
      'Defaulting to centered hero + 3 feature cards',
      'Ignoring brand fonts — always use the project font family',
      'Same color palette across different generations',
      'Repeating the same compositional structure',
    ],
    sourceRepos: ['design variety', 'creative direction', 'art direction'],
    weight: 1.4,
  },
  {
    id: 'premium-fintech-trust',
    title: 'Premium fintech trust interface',
    appliesTo: ['fintech', 'credit', 'banking', 'investing', 'mockup', 'landing'],
    signals: ['credit score', 'payments', 'security', 'cash flow', 'portfolio', 'dashboard'],
    rules: [
      'Show real financial UI: score gauges, payment history, trend lines, controls, and status states.',
      'Trust proof should be quiet: verification marks, security language, bank-grade surfaces.',
      'Even fintech designs should VARY — some warm and friendly, some clinical and precise, some bold and modern.',
    ],
    cssMoves: [
      'SVG score rings or line charts with tabular numerics',
      'Soft radial background washes anchored to brand color',
      'Layered glass panels with subtle border contrast',
    ],
    avoid: ['Playful money cartoons', 'Crypto neon chaos', 'Fake exaggerated financial metrics'],
    sourceRepos: ['fintech dashboards', 'banking apps', 'credit builder apps'],
    weight: 1.1,
  },
  {
    id: 'app-store-conversion-set',
    title: 'App Store conversion screenshot sequence',
    appliesTo: ['mockup', 'app store', 'play store', 'screenshot', 'mobile'],
    signals: ['screenshots', 'app store', 'google play', 'phone', 'iphone', 'android'],
    rules: [
      'One benefit per frame. Sequence: promise → product proof → key workflow → trust → CTA.',
      'Phone frames must be large, dimensional, and specific. Never use generic placeholders.',
      'Each slide needs distinct composition while preserving system consistency.',
    ],
    cssMoves: [
      '3D phone frame shadows and transform rotations',
      'Oversized headline zones',
      'Masked gradient fields behind device silhouettes',
    ],
    avoid: ['Identical slide layouts', 'Tiny phone mockups', 'Internal labels as visible text'],
    sourceRepos: ['mobile launch pages', 'store screenshot generators'],
    weight: 1.1,
  },
  {
    id: 'typography-as-design',
    title: 'Typography-driven design mastery',
    appliesTo: ['god', 'general', 'carousel', 'post', 'ad', 'landing', 'deck'],
    signals: ['typography', 'headline', 'text', 'copy', 'bold'],
    rules: [
      'Strong type hierarchy (clear size jumps between levels) is the #1 signal of design quality.',
      'Use the BRAND FONTS from the project config. Vary within the font family: use different weights (100-900), styles (italic, condensed), and optical sizes (Display, Text, Rounded). This creates drama while staying on-brand.',
      'Copy must have voice — specific > generic, provocative > safe, short > long. Every line should feel written by a creative director.',
    ],
    cssMoves: [
      'font-variation-settings for variable font expressiveness within the brand font family',
      'letter-spacing modulation: tight for display (-0.02em), wide for labels (0.12em)',
      'Mix of weight extremes: 200 + 900 for dramatic hierarchy within the same font family',
    ],
    avoid: [
      'Ignoring brand fonts — ALWAYS use the project font family',
      'One font weight throughout',
      'Generic marketing copy ("Our solution provides...")',
    ],
    sourceRepos: ['editorial design', 'type foundries', 'brand systems'],
    weight: 1.0,
  },
  {
    id: 'data-visualization-craft',
    title: 'Dense product dashboard clarity',
    appliesTo: ['dashboard', 'analytics', 'saas', 'admin', 'landing'],
    signals: ['metrics', 'dashboard', 'analytics', 'table', 'report', 'data'],
    rules: [
      'Information density is the feature. Use compact hierarchy, aligned controls, real data-shaped UI.',
      'Charts need labels, axes or implied scale, and accessible contrast.',
      'Numbers deserve special treatment: tabular numerics, monospace, structured alignment.',
    ],
    cssMoves: [
      'font-variant-numeric: tabular-nums for aligned numbers',
      'Hairline grids (0.5px borders)',
      'Dense responsive grid tracks',
      'SVG charts with crisp strokes',
    ],
    avoid: ['Marketing hero scale in dashboards', 'Cards inside cards', 'Charts with no info architecture'],
    sourceRepos: ['admin dashboards', 'analytics tools', 'observability apps'],
    weight: 1.0,
  },
  {
    id: 'color-emotion-system',
    title: 'Emotional color direction',
    appliesTo: ['god', 'general', 'carousel', 'post', 'ad', 'landing', 'deck', 'mockup'],
    signals: ['color', 'mood', 'tone', 'emotion', 'brand'],
    rules: [
      'Every design needs a color STORY — not just a palette. Ask: what emotion should the viewer feel?',
      'Restrained palettes (2-4 colors) feel premium. Use color as surgical emphasis, not decoration.',
      'VARY the color world per generation: earth tones, industrial grays, tropical warmth, arctic cool, sunset warmth, forest depth, ocean calm, electric energy, vintage muted, luxe jewel.',
    ],
    cssMoves: [
      'oklch() for perceptually uniform color systems',
      'color-mix() for systematic tints and shades',
      'Radial gradient washes as emotional backgrounds',
      'One accent color with 3 opacity levels for hierarchy',
    ],
    avoid: [
      'Purple-to-blue gradient (= "startup template" signal)',
      'Rainbow palettes',
      'Using brand colors at full saturation everywhere',
    ],
    sourceRepos: ['brand systems', 'color theory', 'emotional design'],
    weight: 1.0,
  },
  {
    id: 'spatial-depth-craft',
    title: 'Spatial depth and dimension',
    appliesTo: ['god', 'general', 'carousel', 'post', 'ad', 'landing', 'mockup'],
    signals: ['depth', 'dimension', '3d', 'layer', 'shadow', 'glass', 'premium'],
    rules: [
      'Flat design is dead. Premium designs need perceived depth: layered planes, elevation, atmospheric effects.',
      'Create depth through: overlapping elements, shadow layers, glass panels, perspective transforms, z-index stacking, background-to-foreground gradients.',
      'Each depth technique has a mood: soft shadows = friendly, hard shadows = editorial, glass = modern, perspective = dynamic.',
    ],
    cssMoves: [
      'Multi-layer box-shadow for realistic elevation (4+ layers)',
      'backdrop-filter: blur() + saturate() for glass panels',
      'Perspective + transform: rotateY() for dimensional mockups',
      'Background layers: base color → gradient wash → noise texture → content',
    ],
    avoid: [
      'Flat cards with no elevation',
      'Drop shadows without blur (looks dated)',
      'Overdone glassmorphism without contrast',
    ],
    sourceRepos: ['award sites', 'design systems', 'premium UI'],
    weight: 1.0,
  },
];

function scorePattern(pattern: RepoBrainPattern, input: {
  prompt: string;
  mode: DesignMode;
  brandTokens?: string;
  creativeMode?: CreativeMode;
}): number {
  const haystack = `${input.prompt} ${input.mode} ${input.brandTokens || ''} ${input.creativeMode || ''}`.toLowerCase();
  let score = pattern.weight || 1;

  for (const term of pattern.appliesTo) {
    if (haystack.includes(term.toLowerCase())) score += 3;
  }
  for (const signal of pattern.signals) {
    if (haystack.includes(signal.toLowerCase())) score += 2;
  }
  // God mode boosts: CSS mastery + composition variety (not editorial specifically)
  if (input.creativeMode === 'god' && pattern.id === 'css-mastery-toolkit') score += 6;
  if (input.creativeMode === 'god' && pattern.id === 'composition-variety') score += 5;
  if (input.creativeMode === 'god' && pattern.id === 'spatial-depth-craft') score += 3;
  if (input.mode === 'mockup' && pattern.id === 'app-store-conversion-set') score += 5;
  if (input.mode === 'deck' && pattern.id === 'typography-as-design') score += 4;
  if (input.mode === 'carousel' && pattern.id === 'composition-variety') score += 3;

  return score;
}

function dedupePatterns(patterns: RepoBrainPattern[]): RepoBrainPattern[] {
  const seen = new Set<string>();
  const result: RepoBrainPattern[] = [];
  for (const pattern of patterns) {
    if (seen.has(pattern.id)) continue;
    seen.add(pattern.id);
    result.push(pattern);
  }
  return result;
}

async function loadPersistedPatterns(input: {
  prompt: string;
  mode: DesignMode;
  brandTokens?: string;
}): Promise<{ patterns: RepoBrainPattern[]; source: 'db' | 'seeds' | 'none' }> {
  // First try the new pgvector corpus if available
  try {
    const { retrievePatterns } = await import('@/lib/corpus/retrieval');
    const patterns = await retrievePatterns(
      {
        prompt: input.prompt,
        mode: input.mode,
        brandName: input.brandTokens,
      },
      50,
    );
    if (patterns && patterns.length > 0) {
      return {
        source: 'db',
        patterns: patterns.map((p) => ({
          id: p.metadata?.repo ? `pgvec-${p.metadata.repo}` : crypto.randomUUID(),
          title: p.title,
          appliesTo: p.appliesTo,
          signals: p.signals,
          rules: p.rules,
          cssMoves: p.cssMoves,
          avoid: p.avoid,
          sourceRepos: [String(p.metadata?.repo || 'unknown')],
          weight: p.metadata?.confidence ? Number(p.metadata.confidence) : 1,
        })),
      };
    }
  } catch (error) {
    console.warn(
      '[RepoBrain] pgvector corpus unavailable:',
      error instanceof Error ? error.message : error,
    );
  }

  // Fallback to old design_corpus_patterns table
  try {
    const rows = await loadDesignCorpusPatterns(80);
    if (rows.length > 0) {
      return {
        source: 'db',
        patterns: rows.map((row) => ({
          id: row.id,
          title: row.title,
          appliesTo: row.applies_to,
          signals: row.signals,
          rules: row.rules,
          cssMoves: row.css_moves,
          avoid: row.avoid,
          sourceRepos: row.source_repos,
          weight: row.weight,
        })),
      };
    }
  } catch (error) {
    console.warn(
      '[RepoBrain] persisted corpus unavailable:',
      error instanceof Error ? error.message : error,
    );
  }

  // Fallback to seed corpus when DB is empty
  try {
    const { getSeedPatterns } = await import('./seed-corpus');
    const seeds = getSeedPatterns();
    if (seeds.length > 0) {
      console.log(`[RepoBrain] DB corpus empty — falling back to ${seeds.length} seed patterns`);
      return { source: 'seeds', patterns: seeds };
    }
  } catch (error) {
    console.warn(
      '[RepoBrain] seed corpus unavailable:',
      error instanceof Error ? error.message : error,
    );
  }

  return { source: 'none', patterns: [] };
}

export async function buildRepoBrainContext(input: {
  prompt: string;
  mode: DesignMode;
  brandTokens?: string;
  creativeMode?: CreativeMode;
  limit?: number;
}): Promise<string> {
  const { source, patterns: persistedPatterns } = await loadPersistedPatterns(input);
  const limit = input.limit || 6;

  const allMatched = dedupePatterns([...persistedPatterns, ...BUILTIN_PATTERNS])
    .map(pattern => ({ pattern, score: scorePattern(pattern, input) }))
    .sort((a, b) => b.score - a.score)
    .slice(0, limit);

  const selected = allMatched.map(item => item.pattern);

  // Corpus stats logging
  const dbCount = source === 'db' ? persistedPatterns.length : 0;
  const seedCount = source === 'seeds' ? persistedPatterns.length : 0;
  const selectedNames = selected.map(p => p.id).join(', ');
  console.log(
    `[RepoBrain] corpusStats: ${dbCount} from DB, ${seedCount} from seeds, ` +
    `${BUILTIN_PATTERNS.length} builtins available, ${selected.length} selected ` +
    `(mode=${input.mode}, creative=${input.creativeMode || 'balanced'}) — [${selectedNames}]`,
  );

  if (selected.length === 0) {
    return formatEmptyRepoBrain();
  }

  return formatRepoBrainContext(selected);
}

function formatRepoBrainPattern(pattern: RepoBrainPattern): string {
  return `### ${pattern.id} — ${pattern.title}
CSS techniques available:
${pattern.cssMoves.map(m => `- ${m}`).join('\n')}
Patterns that often work for this kind of design:
${pattern.rules.map(r => `- ${r}`).join('\n')}
Approaches that often feel templated (consider only if relevant to your concept):
${pattern.avoid.map(a => `- ${a}`).join('\n')}`;
}

function formatRepoBrainContext(patterns: RepoBrainPattern[]): string {
  return `## Optional Design Inspiration

The patterns below are reference material from similar projects. They are NOT rules — they are a toolkit. Use what genuinely serves the brief; ignore what doesn't. Your creative judgment overrides any pattern below.

${patterns.map(formatRepoBrainPattern).join('\n\n')}`;
}

function formatEmptyRepoBrain(): string {
  return ``;
}

export function listBuiltinRepoBrainPatterns(): RepoBrainPattern[] {
  return BUILTIN_PATTERNS;
}
