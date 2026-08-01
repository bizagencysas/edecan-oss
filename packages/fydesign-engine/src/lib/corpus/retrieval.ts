// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Corpus Retrieval — Vector search + re-ranking during generation             ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import {
  searchCorpusPatternsByVector,
  searchCorpusPatternsByKeywords,
  searchScreenshotsByVector,
  type CorpusPatternRow,
  type CorpusScreenshotRow,
} from '@/lib/db';
import { embedText, isEmbeddingAvailable, EMBEDDING_DIM } from './embedding';
import { rerankPatterns, computeDomainMatch, scorePatternQuality } from './ranking';
import type { PatternWithScore, RetrievalQuery } from './ranking';
import type { HeuristicPattern } from './heuristic-extractor';
import type { CorpusScreenshot } from './screenshot-runner';

export async function retrievePatterns(
  query: RetrievalQuery,
  limit = 6,
): Promise<HeuristicPattern[]> {
  const poolSize = Math.max(limit * 2, 12);
  const queryText = buildRetrievalQuery(query);

  if (isEmbeddingAvailable()) {
    try {
      const queryEmbedding = await embedText(queryText);
      if (queryEmbedding.length === EMBEDDING_DIM && !queryEmbedding.every((v) => v === 0)) {
        const rows = await searchCorpusPatternsByVector(queryEmbedding, poolSize);

        if (rows.length > 0) {
          const patterns: PatternWithScore[] = rows.map((row) => {
            const heuristic: HeuristicPattern = {
              patternType: row.pattern_type,
              title: row.title,
              summary: row.summary,
              tags: row.tags,
              appliesTo: row.applies_to,
              signals: row.signals,
              rules: row.rules,
              cssMoves: row.css_moves,
              snippets: row.snippets,
              avoid: row.avoid,
              metadata: {
                repo: row.repo_full_name,
                files: row.source_files,
                framework: String(row.metadata?.framework || ''),
                confidence: row.confidence,
              },
            };

            return {
              pattern: heuristic,
              vectorSimilarity: (row as CorpusPatternRow & { similarity: number }).similarity || 0,
              qualityScore: scorePatternQuality(heuristic),
              domainMatch: computeDomainMatch(heuristic, query.mode),
              recency: 0.7,
              sourceDiversity: 0.5,
              brandFit: query.brandName ? computeDomainMatch(heuristic, query.brandName) : 0.5,
            };
          });

          return rerankPatterns(patterns, query)
            .slice(0, limit)
            .map((p) => p.pattern);
        }
      }
    } catch (error) {
      console.warn('[Retrieval] vector search failed, falling back to keywords:', error instanceof Error ? error.message : error);
    }
  }

  return searchByKeywords([queryText, query.mode, query.prompt, query.industry || ''].filter(Boolean), limit);
}

export async function retrieveScreenshots(
  query: RetrievalQuery,
  limit = 6,
): Promise<CorpusScreenshot[]> {
  const queryText = buildRetrievalQuery(query);

  if (isEmbeddingAvailable()) {
    try {
      const queryEmbedding = await embedText(queryText);
      if (queryEmbedding.length === EMBEDDING_DIM && !queryEmbedding.every((v) => v === 0)) {
        const rows = await searchScreenshotsByVector(queryEmbedding, limit);
        return rows.map((row) => ({
          id: row.id,
          repoFullName: row.repo_full_name,
          routeOrFile: row.route_or_file,
          storageUrl: row.storage_url,
          thumbnailUrl: row.thumbnail_url,
          width: row.width || 0,
          height: row.height || 0,
          perceptualHash: row.perceptual_hash || '',
          visualTags: row.visual_tags,
          qualityScore: row.quality_score,
        }));
      }
    } catch (error) {
      console.warn('[Retrieval] screenshot search failed:', error instanceof Error ? error.message : error);
    }
  }

  return [];
}

export function buildRetrievalQuery(input: RetrievalQuery): string {
  return [
    input.brandName || '',
    input.mode || '',
    input.industry || '',
    input.prompt || '',
    input.styleNotes || '',
    input.creativeMode || '',
  ]
    .filter(Boolean)
    .join(' ');
}

export async function searchByKeywords(
  keywords: string[],
  limit = 6,
): Promise<HeuristicPattern[]> {
  try {
    const rows = await searchCorpusPatternsByKeywords(keywords, limit);

    return rows.map((row) => ({
      patternType: row.pattern_type,
      title: row.title,
      summary: row.summary,
      tags: row.tags,
      appliesTo: row.applies_to,
      signals: row.signals,
      rules: row.rules,
      cssMoves: row.css_moves,
      snippets: row.snippets,
      avoid: row.avoid,
      metadata: {
        repo: row.repo_full_name,
        files: row.source_files,
        framework: String(row.metadata?.framework || ''),
        confidence: row.confidence,
      },
    }));
  } catch (error) {
    console.warn('[Retrieval] keyword search failed:', error instanceof Error ? error.message : error);
    return [];
  }
}

export function getDiversePatterns(
  patterns: HeuristicPattern[],
  count: number,
): HeuristicPattern[] {
  const seenSources = new Set<string>();
  const diverse: HeuristicPattern[] = [];

  for (const p of patterns) {
    const source = String(p.metadata?.repo || 'unknown');
    if (!seenSources.has(source) || diverse.length === 0) {
      seenSources.add(source);
      diverse.push(p);
      if (diverse.length >= count) break;
    }
  }

  if (diverse.length < count) {
    for (const p of patterns) {
      if (!diverse.includes(p)) {
        diverse.push(p);
        if (diverse.length >= count) break;
      }
    }
  }

  return diverse.slice(0, count);
}
