// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Repo Brain Ingestor                                                       ║
// ║  Turns GitHub repo analysis into reusable design corpus patterns.           ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { analyzeRepository } from '@/lib/github-analyzer';
import { saveDesignCorpusPattern } from '@/lib/db';
import type { AppAnalysis } from '@/lib/types';

export interface CorpusIngestResult {
  repo: string;
  ok: boolean;
  patternId?: string;
  title?: string;
  error?: string;
}

function slugify(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 80);
}

function unique(values: Array<string | undefined | null>): string[] {
  return Array.from(new Set(values.filter((value): value is string => !!value && value.trim().length > 0)));
}

function inferSignals(analysis: AppAnalysis): string[] {
  const screenNames = analysis.screens?.map(screen => screen.screenName) || [];
  const componentNames = analysis.screens?.flatMap(screen => screen.components || []) || [];
  const textSignals = analysis.screens?.flatMap(screen => screen.texts || []) || [];

  return unique([
    analysis.framework,
    analysis.appName,
    analysis.repoName,
    ...screenNames,
    ...componentNames.slice(0, 24),
    ...textSignals.slice(0, 24),
  ]).slice(0, 40);
}

function inferRules(analysis: AppAnalysis): string[] {
  const screens = analysis.screens || [];
  const layouts = unique(screens.map(screen => screen.estimatedLayout));
  const components = unique(screens.flatMap(screen => screen.components || []));
  const colors = analysis.theme?.accentColors || [];

  const rules = [
    `Framework signal: ${analysis.framework}. Use patterns that feel native to this product type instead of generic web layouts.`,
    screens.length > 0
      ? `The repo exposes ${screens.length} screen patterns. Recreate real UI structure when showing product surfaces: ${screens.slice(0, 5).map(s => s.screenName).join(', ')}.`
      : 'No clear screens were detected. Infer structure from theme/config files and avoid pretending exact screens exist.',
    layouts.length > 0
      ? `Observed layout families: ${layouts.slice(0, 6).join(', ')}. Keep generated designs consistent with these screen structures.`
      : 'If layout data is weak, create a strong temporary hierarchy rather than placeholder blocks.',
    components.length > 0
      ? `Detected components: ${components.slice(0, 10).join(', ')}. Use these as the vocabulary for product mockups.`
      : 'Favor real product primitives: nav, cards, stats, lists, forms, charts, and settings.',
  ];

  if (colors.length > 0) {
    rules.push(`Detected accent colors: ${colors.slice(0, 6).join(', ')}. Use them as brand DNA, not tiny trim.`);
  }

  return rules;
}

function inferCssMoves(analysis: AppAnalysis): string[] {
  const theme = analysis.theme;
  const moves = [
    'Use CSS variables for extracted colors, radius, typography, and surface contrast.',
    'Use SVG/canvas only to enhance the observed product language, not to replace it with generic decoration.',
  ];

  if (theme?.borderRadius) moves.push(`Match the product radius rhythm around ${theme.borderRadius}px when building cards and controls.`);
  if (theme?.darkBackgroundColor) moves.push('Include dark-surface variants when the prompt asks for premium, ads, or social formats.');
  if (theme?.accentColors?.length) moves.push('Build gradient and chart systems from detected accent colors.');

  return moves;
}

function toPattern(analysis: AppAnalysis) {
  const id = `repo-${slugify(analysis.repoFullName || analysis.repoName)}`;
  const title = `${analysis.appName || analysis.repoName} repo pattern`;

  return {
    id,
    title,
    appliesTo: unique([
      analysis.framework,
      analysis.appName,
      analysis.repoName,
      ...(analysis.description || '').split(/\s+/).slice(0, 12),
    ]).slice(0, 24),
    signals: inferSignals(analysis),
    rules: inferRules(analysis),
    cssMoves: inferCssMoves(analysis),
    avoid: [
      'Do not replace detected product structure with placeholder rectangles.',
      'Do not ignore extracted colors when brand tokens exist.',
      'Do not invent product claims that are not present in repo text or user prompt.',
    ],
    sourceRepos: [analysis.repoFullName || analysis.repoName],
    weight: 1,
  };
}

export async function ingestReposIntoCorpus(repos: string[], token: string): Promise<CorpusIngestResult[]> {
  const results: CorpusIngestResult[] = [];
  const cleanRepos = unique(repos).slice(0, 25);

  for (const repo of cleanRepos) {
    try {
      const analysis = await analyzeRepository(repo, token);
      const pattern = toPattern(analysis);
      await saveDesignCorpusPattern(pattern);
      results.push({ repo, ok: true, patternId: pattern.id, title: pattern.title });
    } catch (error) {
      results.push({
        repo,
        ok: false,
        error: error instanceof Error ? error.message : 'Unknown ingest error',
      });
    }
  }

  return results;
}
