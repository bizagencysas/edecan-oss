// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Heuristic Extractor — Converts raw observations into design intelligence     ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import crypto from 'crypto';
import type { RepoProfile } from './file-profiler';
import type { DesignTokens, ColorSystem, SpacingScale, TypographySystem } from './token-extractor';
import type { LayoutPattern, GridSystem } from './layout-extractor';
import type { AnimationPattern } from './animation-extractor';
import type { ComponentClass } from './ast-extractor';

export interface HeuristicPattern {
  patternType: string;
  title: string;
  summary: string;
  tags: string[];
  appliesTo: string[];
  signals: string[];
  rules: string[];
  cssMoves: string[];
  snippets: { kind: string; code: string }[];
  avoid: string[];
  metadata: {
    repo: string;
    files: string[];
    framework: string;
    confidence: number;
  };
}

export interface CorpusPatternChunk {
  id: string;
  repoFullName: string;
  patternType: string;
  title: string;
  summary: string;
  tags: string[];
  appliesTo: string[];
  signals: string[];
  rules: string[];
  cssMoves: string[];
  snippets: { kind: string; code: string }[];
  avoid: string[];
  metadata: Record<string, unknown>;
  sourceFiles: string[];
  confidence: number;
  qualityScore: number;
}

export async function extractHeuristics(
  profile: RepoProfile,
  tokens: DesignTokens,
  layouts: LayoutPattern[],
  animations: AnimationPattern[],
  cloneDir: string,
  repoFullName: string,
): Promise<HeuristicPattern[]> {
  const patterns: HeuristicPattern[] = [];

  // 1. Layout system pattern
  if (layouts.length > 0) {
    patterns.push(buildLayoutPattern(profile, layouts, repoFullName));
  }

  // 2. Color system pattern
  if (Object.keys(tokens.colors).length > 0) {
    patterns.push(buildColorPattern(profile, tokens, repoFullName));
  }

  // 3. Typography pattern
  if (Object.keys(tokens.fontFamily).length > 0 || Object.keys(tokens.borderRadius).length > 0) {
    patterns.push(buildTypographyPattern(profile, tokens, repoFullName));
  }

  // 4. Animation patterns
  for (const anim of animations.slice(0, 3)) {
    patterns.push(buildAnimationPattern(profile, anim, repoFullName));
  }

  // 5. Component architecture pattern
  patterns.push(buildArchitecturePattern(profile, layouts, repoFullName));

  // 6. Framework-specific patterns
  if (profile.framework !== 'unknown') {
    patterns.push(buildFrameworkPattern(profile, repoFullName));
  }

  // 7. Responsive/grid system
  if (layouts.some((l) => l.type === 'grid')) {
    patterns.push(buildGridPattern(profile, layouts, repoFullName));
  }

  return patterns.filter((p) => p.rules.length > 0 || p.cssMoves.length > 0);
}

function makeSummary(pattern: HeuristicPattern): string {
  return `${pattern.title}. ${pattern.rules.slice(0, 2).join(' ')}`.slice(0, 500);
}

function mkPattern(pattern: Omit<HeuristicPattern, 'summary'>, repoFullName: string, confidence: number): HeuristicPattern {
  const full: HeuristicPattern = { ...pattern, summary: '', metadata: { ...pattern.metadata, repo: repoFullName, confidence } };
  full.summary = makeSummary(full);
  return full;
}

function buildLayoutPattern(
  profile: RepoProfile,
  layouts: LayoutPattern[],
  repoFullName: string,
): HeuristicPattern {
  const layoutTypes = [...new Set(layouts.map((l) => l.type))];
  const files = [...new Set(layouts.map((l) => l.file))];

  return {
    patternType: 'layout-system',
    title: `${profile.framework} layout system`,
    summary: '',
    tags: ['layout', profile.framework, ...layoutTypes],
    appliesTo: inferAppliesTo(profile),
    signals: ['layout', 'structure', 'composition'],
    rules: [
      `Uses ${layoutTypes.join(', ')} for primary layout composition.`,
      `Layout patterns found in ${files.length} files.`,
      profile.hasTailwind ? 'Uses Tailwind utility classes for layout.' : 'Uses custom CSS for layout.',
    ],
    cssMoves: layouts.slice(0, 5).flatMap((l) => l.cssClasses.map((c) => `tailwind: ${c}`)),
    snippets: [],
    avoid: ['Generic centered layouts', 'Over-nested flex containers'],
    metadata: { repo: repoFullName, files, framework: profile.framework, confidence: 0.7 },
  };
}

function buildColorPattern(
  profile: RepoProfile,
  tokens: DesignTokens,
  repoFullName: string,
): HeuristicPattern {
  const colorKeys = Object.keys(tokens.colors);
  const hasDark = colorKeys.some((k) => k.startsWith('dark') || k.includes('dark'));

  return {
    patternType: 'color-system',
    title: 'Color token system',
    summary: '',
    tags: ['color', 'tokens', ...(hasDark ? ['dark-mode'] : [])],
    appliesTo: inferAppliesTo(profile),
    signals: ['color', 'palette', 'design-tokens', ...(hasDark ? ['dark-mode'] : [])],
    rules: [
      `Color system has ${colorKeys.length} defined tokens.`,
      hasDark ? 'Includes dark mode color variants.' : 'No dark mode tokens detected.',
      'Use extracted colors as brand DNA, not tiny trim.',
    ],
    cssMoves: colorKeys.slice(0, 10).map((k) => `var(--${k})`),
    snippets: [],
    avoid: ['Ignoring detected brand colors', 'Using generic color palettes'],
    metadata: { repo: repoFullName, files: [], framework: profile.framework, confidence: 0.8 },
  };
}

function buildTypographyPattern(
  profile: RepoProfile,
  tokens: DesignTokens,
  repoFullName: string,
): HeuristicPattern {
  const fontFamilies = Object.keys(tokens.fontFamily);
  const radiusKeys = Object.keys(tokens.borderRadius);

  return {
    patternType: 'typography-system',
    title: 'Typography + shape system',
    summary: '',
    tags: ['typography', 'fonts', 'spacing', ...(radiusKeys.length > 0 ? ['border-radius'] : [])],
    appliesTo: inferAppliesTo(profile),
    signals: ['typography', 'fonts', 'text'],
    rules: [
      fontFamilies.length > 0 ? `Font families: ${fontFamilies.join(', ')}.` : 'No custom font families detected.',
      radiusKeys.length > 0 ? `Border radius tokens: ${radiusKeys.join(', ')}.` : 'Default border radius assumed.',
      'Match the product radius rhythm when building cards and controls.',
    ],
    cssMoves: [
      ...fontFamilies.map((f) => `font-family: var(--font-${f})`),
      ...radiusKeys.map((r) => `border-radius: var(--${r})`),
    ],
    snippets: [],
    avoid: ['Mismatched typography scale', 'Random border radius values'],
    metadata: { repo: repoFullName, files: [], framework: profile.framework, confidence: 0.75 },
  };
}

function buildAnimationPattern(
  profile: RepoProfile,
  anim: AnimationPattern,
  repoFullName: string,
): HeuristicPattern {
  return {
    patternType: 'animation-system',
    title: `${anim.type} animation pattern`,
    summary: anim.description,
    tags: ['animation', anim.type, 'motion'],
    appliesTo: inferAppliesTo(profile),
    signals: ['animation', 'motion', 'transition'],
    rules: [
      `Uses ${anim.type} for animations.`,
      anim.description,
      'Animation should enhance brand feel, not distract from content.',
    ],
    cssMoves: anim.snippet ? [anim.snippet] : anim.properties.map((p) => `${anim.type}: ${p}`),
    snippets: anim.snippet ? [{ kind: anim.type, code: anim.snippet }] : [],
    avoid: ['Over-animated interfaces', 'Motion that ruins static export'],
    metadata: { repo: repoFullName, files: [anim.file], framework: profile.framework, confidence: 0.65 },
  };
}

function buildArchitecturePattern(
  profile: RepoProfile,
  layouts: LayoutPattern[],
  repoFullName: string,
): HeuristicPattern {
  return {
    patternType: 'component-architecture',
    title: 'Component architecture',
    summary: '',
    tags: ['architecture', 'components', profile.framework],
    appliesTo: inferAppliesTo(profile),
    signals: ['components', 'architecture', 'structure'],
    rules: [
      `Framework: ${profile.framework}.`,
      profile.hasTypescript ? 'Uses TypeScript.' : 'Uses JavaScript.',
      profile.hasTailwind ? 'Uses Tailwind CSS.' : '',
      profile.hasShadcn ? 'Uses shadcn/ui components.' : '',
      profile.hasRadix ? 'Uses Radix UI primitives.' : '',
      profile.hasStorybook ? 'Has Storybook configured.' : '',
      `App directory: ${profile.appDir || 'not detected'}.`,
      `Components directory: ${profile.componentsDir || 'not detected'}.`,
      `${profile.totalFrontendFiles} of ${profile.totalFiles} files are frontend.`,
    ].filter(Boolean),
    cssMoves: [
      profile.hasTailwind ? 'Tailwind utility classes' : 'Custom CSS',
      profile.hasShadcn ? 'shadcn/ui component patterns' : '',
    ].filter(Boolean),
    snippets: [],
    avoid: ['Mixing too many UI paradigms', 'Inconsistent component patterns'],
    metadata: {
      repo: repoFullName,
      files: [...new Set(layouts.map((l) => l.file))],
      framework: profile.framework,
      confidence: 0.85,
    },
  };
}

function buildFrameworkPattern(
  profile: RepoProfile,
  repoFullName: string,
): HeuristicPattern {
  const frameworkRules: Record<string, string[]> = {
    nextjs: [
      'Uses Next.js App Router conventions.',
      'Routes are file-system based under app/ directory.',
      'Use server components where possible, client components for interactivity.',
    ],
    vite: [
      'Uses Vite-based project structure.',
      'Routes defined by React Router or similar.',
      'SPA conventions apply.',
    ],
    expo: [
      'Uses Expo/React Native for mobile.',
      'File-based routing with Expo Router.',
      'Native component patterns for iOS/Android.',
    ],
    astro: [
      'Uses Astro for content-focused sites.',
      'Island architecture: static HTML with interactive islands.',
    ],
    nuxt: [
      'Uses Nuxt/Vue conventions.',
      'Auto-imports and file-based routing.',
    ],
    remix: [
      'Uses Remix/React Router v7.',
      'Route modules with loader/action patterns.',
    ],
  };

  const rules = frameworkRules[profile.framework] || ['Unknown framework — generic patterns apply.'];

  return {
    patternType: 'framework-conventions',
    title: `${profile.framework} conventions`,
    summary: '',
    tags: [profile.framework, 'framework', 'conventions'],
    appliesTo: inferAppliesTo(profile),
    signals: [profile.framework, 'framework'],
    rules,
    cssMoves: profile.framework === 'nextjs'
      ? ['Next.js Image component', 'Server/client component boundaries']
      : [],
    snippets: [],
    avoid: ['Breaking framework conventions without reason'],
    metadata: { repo: repoFullName, files: [], framework: profile.framework, confidence: 0.9 },
  };
}

function buildGridPattern(
  profile: RepoProfile,
  layouts: LayoutPattern[],
  repoFullName: string,
): HeuristicPattern {
  const gridLayouts = layouts.filter((l) => l.type === 'grid');

  return {
    patternType: 'grid-system',
    title: 'Grid-based layout system',
    summary: '',
    tags: ['grid', 'css-grid', 'layout'],
    appliesTo: inferAppliesTo(profile),
    signals: ['grid', 'columns', 'responsive'],
    rules: [
      'Uses CSS Grid for page-level layout.',
      `${gridLayouts.length} grid-based components detected.`,
      'Grid is used for precise spatial control and asymmetrical compositions.',
    ],
    cssMoves: gridLayouts.flatMap((g) => g.cssClasses).slice(0, 10),
    snippets: gridLayouts.slice(0, 3).map((g) => ({
      kind: 'css',
      code: `/* Grid pattern from ${g.file} */\n${g.description}`,
    })),
    avoid: ['Overcomplicating with excessive grid nesting'],
    metadata: {
      repo: repoFullName,
      files: gridLayouts.map((g) => g.file),
      framework: profile.framework,
      confidence: 0.7,
    },
  };
}

function inferAppliesTo(profile: RepoProfile): string[] {
  const applies: Set<string> = new Set();

  const haystack = [
    profile.framework,
    ...Object.keys(profile.dependencies),
    ...profile.routes.join(' ').toLowerCase().split('/'),
  ].join(' ');

  if (/dashboard|admin|analytics|metrics|chart|table/i.test(haystack)) applies.add('dashboard');
  if (/landing|page|hero|marketing/i.test(haystack)) applies.add('landing');
  if (/saas|app|platform/i.test(haystack)) applies.add('saas');
  if (/blog|content|article|editorial|post/i.test(haystack)) applies.add('editorial');
  if (/ecommerce|shop|store|cart|product/i.test(haystack)) applies.add('ecommerce');
  if (/portfolio|agency|creative/i.test(haystack)) applies.add('portfolio');
  if (/finance|bank|payment|crypto|invest/i.test(haystack)) applies.add('fintech');

  if (applies.size === 0) applies.add('general');
  return [...applies];
}

export function generatePatternTitle(pattern: HeuristicPattern): string {
  return pattern.title || `${pattern.patternType} — ${pattern.metadata.framework}`;
}

export function assessConfidence(pattern: HeuristicPattern): number {
  let score = 0.4; // base

  if (pattern.rules.length >= 3) score += 0.15;
  if (pattern.cssMoves.length >= 3) score += 0.15;
  if (pattern.snippets.length > 0) score += 0.1;
  if (pattern.metadata.files.length > 0) score += 0.1;
  if (pattern.patternType !== 'unknown') score += 0.1;

  return Math.min(score, 0.98);
}

export function patternToChunk(
  pattern: HeuristicPattern,
  repoFullName: string,
  qualityScore: number,
): CorpusPatternChunk {
  const confidence = pattern.metadata.confidence || assessConfidence(pattern);

  return {
    id: `pat-${crypto.randomUUID().slice(0, 12)}`,
    repoFullName,
    patternType: pattern.patternType,
    title: pattern.title,
    summary: pattern.summary || makeSummary(pattern),
    tags: pattern.tags,
    appliesTo: pattern.appliesTo,
    signals: pattern.signals,
    rules: pattern.rules,
    cssMoves: pattern.cssMoves,
    snippets: pattern.snippets,
    avoid: pattern.avoid,
    metadata: {
      ...pattern.metadata,
      repo: repoFullName,
      framework: pattern.metadata.framework,
      confidence,
    },
    sourceFiles: pattern.metadata.files || [],
    confidence,
    qualityScore,
  };
}
