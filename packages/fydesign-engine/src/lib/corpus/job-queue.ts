// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Corpus Job Queue — Neon Postgres-backed job queue with SKIP LOCKED         ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { getDb, enqueueJob, claimNextJob, completeJob, failJob, requeueStuckJobs } from '@/lib/db';
import type { DiscoveredRepo } from './discovery';
import crypto from 'crypto';

export interface CorpusJob {
  id: string;
  repoFullName: string;
  type: string;
  status: string;
  priority: number;
  attempts: number;
  maxAttempts: number;
  error: string | null;
  payload: Record<string, unknown>;
  lockedAt: string | null;
  lockedBy: string | null;
  createdAt: string;
  updatedAt: string;
}

function jobId(): string {
  return `job-${crypto.randomUUID().slice(0, 12)}`;
}

export async function enqueueDiscoveryJobs(repos: DiscoveredRepo[]): Promise<number> {
  let count = 0;
  const db = getDb();

  for (const repo of repos) {
    try {
      await enqueueJob({
        id: jobId(),
        repoFullName: repo.fullName,
        type: 'ingest',
        priority: Math.round(repo.qualityScore ?? 0) * 10,
        payload: {
          owner: repo.owner,
          repo: repo.repo,
          stars: repo.stars,
          topics: repo.topics,
          qualityScore: repo.qualityScore,
        },
      });
      count++;
    } catch (error) {
      console.warn(`[JobQueue] failed to enqueue ${repo.fullName}:`, error instanceof Error ? error.message : error);
    }
  }

  return count;
}

export async function claimJob(workerId: string): Promise<CorpusJob | null> {
  try {
    const row = await claimNextJob(workerId);
    if (!row) return null;
    return rowToJob(row);
  } catch (error) {
    console.warn('[JobQueue] claimJob failed:', error instanceof Error ? error.message : error);
    return null;
  }
}

export async function markJobComplete(jobId: string): Promise<void> {
  await completeJob(jobId);
}

export async function markJobFailed(jobId: string, error: string): Promise<void> {
  await failJob(jobId, error);
}

export async function requeueZombieJobs(timeoutMinutes = 30): Promise<number> {
  return requeueStuckJobs(timeoutMinutes);
}

function rowToJob(row: {
  id: string;
  repo_full_name: string;
  type: string;
  status: string;
  priority: number;
  attempts: number;
  max_attempts: number;
  error: string | null;
  payload: Record<string, unknown>;
  locked_at: string | null;
  locked_by: string | null;
  created_at: string;
  updated_at: string;
}): CorpusJob {
  return {
    id: row.id,
    repoFullName: row.repo_full_name,
    type: row.type,
    status: row.status,
    priority: row.priority,
    attempts: row.attempts,
    maxAttempts: row.max_attempts,
    error: row.error || null,
    payload: row.payload || {},
    lockedAt: row.locked_at || null,
    lockedBy: row.locked_by || null,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}
