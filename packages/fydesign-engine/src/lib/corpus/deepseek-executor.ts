// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  DeepSeek Executor — Full ingestion pipeline orchestration                   ║
// ║  DeepSeek crawls, clones, parses, extracts, classifies, scores, embeds,      ║
// ║  stores, and implements. Follows Opus-authored rules exactly.                ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import crypto from 'crypto';
import { discoverRepos, scoreRepoQuality, shouldIngest, rejectRepo } from './discovery';
import { enqueueDiscoveryJobs, claimJob, markJobComplete, markJobFailed, requeueZombieJobs } from './job-queue';
import { cloneRepoTempSafe, removeCloneDir } from './clone-runner';
import { isRepoSafeToRun } from './repo-sandbox';
import { profileRepo } from './file-profiler';
import { extractComponentNames, extractPropsPatterns, extractJSXPatterns, classifyComponent, extractImports } from './ast-extractor';
import { extractTailwindTokens, extractCSSVariables, extractColorSystem, extractSpacingScale, extractTypographySystem } from './token-extractor';
import { extractLayoutPatterns, detectGridSystem } from './layout-extractor';
import { extractAnimationPatterns, detectAnimationLibrary } from './animation-extractor';
import { extractHeuristics, patternToChunk, assessConfidence } from './heuristic-extractor';
import { extractSpacingHeuristics, detectEditorialAesthetics, detectFintechAesthetics, extractNavigationSystems, scorePremiumComposition } from './aesthetics-extractor';
import { embedPattern, embedScreenshot, isEmbeddingAvailable } from './embedding';
import { scorePatternQuality, shouldRejectPattern } from './ranking';
import { storeDiscoveredRepos, storePattern, storeScreenshotData, markRepoIngested, getRepoStatus } from './storage';
import { cleanupCloneDir, cleanupOldTempDirs } from './cleanup';
import { loadDirectivesForDomain, applyDirectivesToScoring } from './opus-directives';
import { captureRepoScreenshots } from './screenshot-runner';
import { learnFromPatterns, seedDesignMemory } from './design-memory';
import type { HeuristicPattern } from './heuristic-extractor';
import { readdir, readFile } from 'node:fs/promises';
import path from 'node:path';

export interface PipelineResult {
  jobId: string;
  repoFullName: string;
  status: 'completed' | 'failed';
  patternsExtracted: number;
  screenshotsCaptured: number;
  duration: number;
  error?: string;
}

export interface WorkerResult {
  processed: number;
  failed: number;
  patternsStored: number;
}

export interface WorkerStats {
  totalProcessed: number;
  totalFailed: number;
  totalPatterns: number;
  startTime: string;
  endTime?: string;
}

const DEEPSEEK_RULES = [
  'Do not rethink architecture.',
  'Do not replace Opus directives with its own taste.',
  'Do not permanently store cloned repos.',
  'Do not store large source files.',
  'Do not run unsafe install scripts.',
  'Do not ingest repos without source metadata.',
  'Do not save patterns without scores.',
  'Do not embed raw files.',
  'Do not use full copied code as reusable snippets.',
  'Do not block if corpus retrieval fails; fallback to built-in patterns.',
];

export async function executeIngestionPipeline(
  repoFullName: string,
  jobId: string,
  token: string,
): Promise<PipelineResult> {
  const startTime = Date.now();
  let cloneDir: string | null = null;
  let patternsExtracted = 0;
  let screenshotsCaptured = 0;

  console.log(`[Executor] Starting pipeline for ${repoFullName} (job: ${jobId})`);
  console.log(`[Executor] Rules: ${DEEPSEEK_RULES.length} execution rules loaded`);

  try {
    // Step 1: Check if already processed
    const existingStatus = await getRepoStatus(repoFullName);
    if (existingStatus === 'ingested') {
      console.log(`[Executor] ${repoFullName} already ingested, skipping`);
      markJobComplete(jobId).catch(() => {});
      return { jobId, repoFullName, status: 'completed', patternsExtracted: 0, screenshotsCaptured: 0, duration: Date.now() - startTime };
    }

    // Step 2: Clone repo temporarily
    console.log(`[Executor] Cloning ${repoFullName}...`);
    cloneDir = await cloneRepoTempSafe(repoFullName, jobId);
    if (!cloneDir) {
      throw new Error('Clone failed');
    }
    console.log(`[Executor] Cloned to ${cloneDir}`);

    // Step 3: Profile repo
    console.log(`[Executor] Profiling...`);
    const profile = await profileRepo(cloneDir);
    console.log(`[Executor] Framework: ${profile.framework}, Files: ${profile.totalFiles}, Frontend: ${profile.totalFrontendFiles}`);

    // Step 4: Static extraction
    console.log(`[Executor] Static extraction...`);
    const tokens = await extractTailwindTokens(cloneDir);
    const cssVars = await extractCSSVariables(cloneDir);
    const layouts = await extractLayoutPatterns(profile, cloneDir);
    const animations = await extractAnimationPatterns(cloneDir, profile);
    console.log(`[Executor] Tokens: ${Object.keys(tokens.colors).length} colors, Layouts: ${layouts.length}, Animations: ${animations.length}`);

    // Step 4.5: Aesthetics extraction
    const spacingHeuristics = await extractSpacingHeuristics(profile, cloneDir);
    const editorialSignals = await detectEditorialAesthetics(profile, cloneDir);
    const fintechSignals = await detectFintechAesthetics(profile, cloneDir);
    const navSystems = await extractNavigationSystems(profile, cloneDir);
    const premiumScore = scorePremiumComposition(profile);
    console.log(`[Executor] Spacing: ${spacingHeuristics.length} patterns, Editorial: ${editorialSignals.length}, Fintech: ${fintechSignals.length}, Nav: ${navSystems.length}, Premium: ${premiumScore.overall}`);

    // Step 5: Heuristic extraction
    console.log(`[Executor] Heuristic extraction...`);
    const heuristics = await extractHeuristics(profile, tokens, layouts, animations, cloneDir, repoFullName);
    console.log(`[Executor] Generated ${heuristics.length} heuristic patterns`);

    // Step 6: Score patterns
    console.log(`[Executor] Scoring...`);
    const directives = await loadDirectivesForDomain(profile.framework === 'nextjs' ? 'saas' : 'general');
    const scoredHeuristics = heuristics
      .map((h) => {
        const qualityScore = scorePatternQuality(h);
        const directiveAdjustment = directives
          ? applyDirectivesToScoring(h, [directives])
          : 0;
        return { pattern: h, qualityScore: qualityScore + directiveAdjustment };
      })
      .filter(({ pattern }) => {
        const rejection = shouldRejectPattern(pattern);
        if (rejection.reject) {
          console.log(`[Executor] Rejected pattern "${pattern.title}": ${rejection.reason}`);
        }
        return !rejection.reject;
      });
    console.log(`[Executor] ${scoredHeuristics.length} patterns passed scoring`);

    // Step 7: Embed and store patterns
    console.log(`[Executor] Embedding + storing...`);
    for (const { pattern, qualityScore } of scoredHeuristics) {
      try {
        let embedding: number[] | null = null;
        if (isEmbeddingAvailable()) {
          embedding = await embedPattern({
            summary: pattern.summary || pattern.title,
            rules: pattern.rules,
            tags: pattern.tags,
            metadata: pattern.metadata,
          });
        }

        // Convert to chunk and store
        const chunk = patternToChunk(pattern, repoFullName, qualityScore);
        const chunkWithEmbedding = { ...chunk, embedding };

        await storePattern(pattern, embedding, repoFullName, qualityScore);
        patternsExtracted++;
      } catch (error) {
        console.warn(`[Executor] Failed to store pattern "${pattern.title}":`, error instanceof Error ? error.message : error);
        // Rule 10: Do not block if storage fails
      }
    }
    console.log(`[Executor] Stored ${patternsExtracted} patterns`);

    // Step 7.5: Learn from patterns (design memory accumulation)
    if (heuristics.length > 0) {
      try {
        const learned = await learnFromPatterns(heuristics, repoFullName);
        console.log(`[Executor] Learned ${learned} design memory insights`);
      } catch (error) {
        console.warn('[Executor] design memory learning failed:', error instanceof Error ? error.message : error);
      }
    }

    // Step 8: Screenshots (optional, if safe to run) — 2min hard timeout
    const isSafe = await isRepoSafeToRun(cloneDir);
    if (isSafe) {
      try {
        console.log(`[Executor] Capturing screenshots...`);
        const screenshots = await Promise.race([
          captureRepoScreenshots(cloneDir, repoFullName),
          new Promise<never>((_, reject) =>
            setTimeout(() => reject(new Error('Screenshot capture timed out after 2min')), 120_000)
          ),
        ]);
        screenshotsCaptured = screenshots.length;

        // Embed and store screenshots
        for (const ss of screenshots) {
          try {
            let embedding: number[] | null = null;
            if (isEmbeddingAvailable()) {
              embedding = await embedScreenshot({
                visualTags: ss.visualTags,
                routeOrFile: ss.routeOrFile,
                repoFullName: ss.repoFullName,
              });
            }
            await storeScreenshotData(ss, embedding);
          } catch { /* Rule 10: don't block */ }
        }
        console.log(`[Executor] Stored ${screenshotsCaptured} screenshots`);
      } catch (error) {
        console.warn('[Executor] Screenshot capture failed:', error instanceof Error ? error.message : error);
      }
    }

    // Step 9: Mark repo ingested
    await markRepoIngested(repoFullName);

    // Step 10: Complete job
    await markJobComplete(jobId);

    const duration = Date.now() - startTime;
    console.log(`[Executor] Pipeline complete: ${patternsExtracted} patterns, ${screenshotsCaptured} screenshots, ${duration}ms`);

    return { jobId, repoFullName, status: 'completed', patternsExtracted, screenshotsCaptured, duration };
  } catch (error) {
    const duration = Date.now() - startTime;
    const errMsg = error instanceof Error ? error.message : 'Unknown error';
    console.error(`[Executor] Pipeline failed for ${repoFullName}:`, errMsg);

    markJobFailed(jobId, errMsg).catch(() => {});
    return { jobId, repoFullName, status: 'failed', patternsExtracted, screenshotsCaptured, duration, error: errMsg };
  } finally {
    // CRITICAL: Always cleanup temp files (Rule 3)
    if (cloneDir) {
      await cleanupCloneDir(cloneDir).catch(() => {});
    }
  }
}

export async function executeDiscoveryAndEnqueue(
  token: string,
): Promise<{ discovered: number; enqueued: number }> {
  console.log('[Executor] Starting discovery...');

  const repos = await discoverRepos(token);
  console.log(`[Executor] Discovered ${repos.length} repos`);

  const scored = repos
    .map((repo) => ({ ...repo, qualityScore: scoreRepoQuality(repo) }))
    .filter((repo) => {
      const rejection = rejectRepo(repo);
      if (rejection) {
        console.log(`[Executor] Rejected ${repo.fullName}: ${rejection}`);
      }
      return !rejection && shouldIngest(repo);
    });

  console.log(`[Executor] ${scored.length} repos passed filters`);

  // Store discovered repos
  await storeDiscoveredRepos(scored);

  // Enqueue top repos as jobs
  const top = scored
    .sort((a, b) => (b.qualityScore || 0) - (a.qualityScore || 0))
    .slice(0, 50);

  const enqueued = await enqueueDiscoveryJobs(top);
  console.log(`[Executor] Enqueued ${enqueued} jobs`);

  return { discovered: repos.length, enqueued };
}

export async function executeWorker(
  workerId: string,
  token: string,
): Promise<WorkerResult> {
  console.log(`[Worker ${workerId}] Starting...`);

  // Clean up zombie jobs first
  const zombies = await requeueZombieJobs(30);
  if (zombies > 0) console.log(`[Worker ${workerId}] Requeued ${zombies} zombie jobs`);

  // Clean up old temp dirs
  const cleaned = await cleanupOldTempDirs(24);
  if (cleaned > 0) console.log(`[Worker ${workerId}] Cleaned ${cleaned} old temp dirs`);

  const job = await claimJob(workerId);
  if (!job) {
    console.log(`[Worker ${workerId}] No jobs available`);
    return { processed: 0, failed: 0, patternsStored: 0 };
  }

  console.log(`[Worker ${workerId}] Claimed job ${job.id} for ${job.repoFullName}`);

  const result = await executeIngestionPipeline(job.repoFullName, job.id, token);

  return {
    processed: result.status === 'completed' ? 1 : 0,
    failed: result.status === 'failed' ? 1 : 0,
    patternsStored: result.patternsExtracted,
  };
}

export async function runContinuousWorker(
  workerId: string,
  token: string,
  options?: { maxJobs?: number; pollIntervalMs?: number },
): Promise<WorkerStats> {
  const POLL_INTERVAL_MS = Number(process.env.CORPUS_POLL_INTERVAL_MS) || 5000;
  const maxJobs = options?.maxJobs || 10;
  const pollIntervalMs = options?.pollIntervalMs || POLL_INTERVAL_MS;

  const stats: WorkerStats = {
    totalProcessed: 0,
    totalFailed: 0,
    totalPatterns: 0,
    startTime: new Date().toISOString(),
  };

  console.log(`[Worker ${workerId}] Continuous mode: maxJobs=${maxJobs}, pollInterval=${pollIntervalMs}ms`);

  while (stats.totalProcessed + stats.totalFailed < maxJobs) {
    const result = await executeWorker(workerId, token);

    stats.totalProcessed += result.processed;
    stats.totalFailed += result.failed;
    stats.totalPatterns += result.patternsStored;

    if (result.processed === 0 && result.failed === 0) {
      // No jobs available, wait before polling
      console.log(`[Worker ${workerId}] No jobs, polling in ${pollIntervalMs}ms...`);
      await new Promise((r) => setTimeout(r, pollIntervalMs));
    }
  }

  stats.endTime = new Date().toISOString();
  console.log(`[Worker ${workerId}] Complete: ${stats.totalProcessed} processed, ${stats.totalFailed} failed, ${stats.totalPatterns} patterns`);
  return stats;
}
