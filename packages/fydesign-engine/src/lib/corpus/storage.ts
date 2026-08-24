// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Corpus Storage — High-level storage orchestration                           ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { randomUUID } from 'node:crypto';
import {
  saveCorpusRepo,
  saveCorpusPatternFull,
  saveCorpusScreenshot,
  getCorpusStats as dbGetCorpusStats,
  updateCorpusRepoStatus,
} from '@/lib/db';
import type { DiscoveredRepo } from './discovery';
import type { HeuristicPattern, CorpusPatternChunk } from './heuristic-extractor';
import type { CorpusScreenshot } from './screenshot-runner';

export interface CorpusStats {
  totalRepos: number;
  ingestedRepos: number;
  totalPatterns: number;
  totalScreenshots: number;
  avgQualityScore: number;
  lastDiscoveryAt: string | null;
}

export async function storeDiscoveredRepos(repos: DiscoveredRepo[]): Promise<number> {
  let count = 0;
  for (const repo of repos) {
    try {
      await saveCorpusRepo({
        id: repo.id,
        owner: repo.owner,
        repo: repo.repo,
        fullName: repo.fullName,
        htmlUrl: repo.htmlUrl,
        description: repo.description,
        defaultBranch: repo.defaultBranch,
        stars: repo.stars,
        forks: repo.forks,
        topics: repo.topics,
        language: repo.language,
        license: repo.license,
        discoveredBy: 'discovery-cron',
        qualityScore: repo.qualityScore || 0,
        status: 'discovered',
      });
      count++;
    } catch (error) {
      console.warn(`[Storage] failed to save repo ${repo.fullName}:`, error instanceof Error ? error.message : error);
    }
  }
  return count;
}

export async function storePattern(pattern: HeuristicPattern, embedding: number[] | null, repoFullName: string, qualityScore = 0): Promise<string> {
  const summary = pattern.summary || `${pattern.title}. ${pattern.rules.slice(0, 2).join(' ')}`.slice(0, 500);
  const chunk: CorpusPatternChunk = {
    id: `pat-${randomUUID().slice(0, 12)}`,
    repoFullName,
    patternType: pattern.patternType,
    title: pattern.title,
    summary,
    tags: pattern.tags,
    appliesTo: pattern.appliesTo,
    signals: pattern.signals,
    rules: pattern.rules,
    cssMoves: pattern.cssMoves,
    snippets: pattern.snippets,
    avoid: pattern.avoid,
    metadata: pattern.metadata,
    sourceFiles: pattern.metadata.files || [],
    confidence: pattern.metadata.confidence || 0,
    qualityScore,
  };

  await saveCorpusPatternFull({
    id: chunk.id,
    repoFullName,
    patternType: chunk.patternType,
    title: chunk.title,
    summary: chunk.summary,
    tags: chunk.tags,
    appliesTo: chunk.appliesTo,
    signals: chunk.signals,
    rules: chunk.rules,
    cssMoves: chunk.cssMoves,
    snippets: chunk.snippets,
    avoid: chunk.avoid,
    metadata: chunk.metadata,
    sourceFiles: chunk.sourceFiles,
    confidence: chunk.confidence,
    qualityScore: chunk.qualityScore,
    embedding,
  });

  return chunk.id;
}

export async function storeScreenshotData(screenshot: CorpusScreenshot, embedding?: number[] | null): Promise<string> {
  await saveCorpusScreenshot({
    id: screenshot.id,
    repoFullName: screenshot.repoFullName,
    routeOrFile: screenshot.routeOrFile,
    storageUrl: screenshot.storageUrl,
    thumbnailUrl: screenshot.thumbnailUrl,
    width: screenshot.width,
    height: screenshot.height,
    perceptualHash: screenshot.perceptualHash,
    visualTags: screenshot.visualTags,
    qualityScore: screenshot.qualityScore,
    embedding,
  });
  return screenshot.id;
}

export async function markRepoIngested(fullName: string): Promise<void> {
  await updateCorpusRepoStatus(fullName, 'ingested');
}

export async function getRepoStatus(fullName: string): Promise<string | null> {
  const { loadCorpusRepo } = await import('@/lib/db');
  const repo = await loadCorpusRepo(fullName);
  return repo?.status || null;
}

export async function getStats(): Promise<CorpusStats> {
  const stats = await dbGetCorpusStats();
  return {
    ...stats,
    lastDiscoveryAt: null,
  };
}
