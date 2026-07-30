// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Clone Runner — Temporary git clone lifecycle                                ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { spawn } from 'node:child_process';
import { mkdir, rm } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import path from 'node:path';
import os from 'node:os';

function run(cmd: string, args: string[], opts: { cwd: string; timeout?: number }): Promise<void> {
  return new Promise((resolve, reject) => {
    const p = spawn(cmd, args, { ...opts, stdio: 'pipe' });
    let stderr = '';
    p.stderr.on('data', d => { stderr += d.toString(); });
    p.on('close', code => code === 0 ? resolve() : reject(new Error(stderr || `${cmd} exited ${code}`)));
    p.on('error', reject);
    if (opts.timeout) setTimeout(() => { p.kill('SIGTERM'); reject(new Error(`${cmd} timed out`)); }, opts.timeout);
  });
}

function runCapture(cmd: string, args: string[], opts: { cwd: string }): Promise<string> {
  return new Promise((resolve, reject) => {
    const p = spawn(cmd, args, { ...opts, stdio: 'pipe' });
    let stdout = '';
    let stderr = '';
    p.stdout.on('data', d => { stdout += d.toString(); });
    p.stderr.on('data', d => { stderr += d.toString(); });
    p.on('close', code => code === 0 ? resolve(stdout) : reject(new Error(stderr || `${cmd} exited ${code}`)));
    p.on('error', reject);
  });
}

const VALID_NAME = /^[a-zA-Z0-9._-]+$/;

const TMP_ROOT = path.join(os.tmpdir(), 'fydesign-corpus');
const MAX_SIZE_MB = 500;

export function getCloneDir(jobId: string, repoFullName: string): string {
  const [owner, repo] = repoFullName.split('/');
  return path.join(TMP_ROOT, jobId, `${owner}-${repo}`);
}

export async function cloneRepoTemp(repoFullName: string, jobId: string): Promise<string> {
  const cloneDir = getCloneDir(jobId, repoFullName);

  if (existsSync(cloneDir)) {
    await rm(cloneDir, { recursive: true, force: true });
  }

  await mkdir(cloneDir, { recursive: true });

  const [owner, repo] = repoFullName.split('/');

  if (!VALID_NAME.test(owner) || !VALID_NAME.test(repo)) {
    throw new Error(`Invalid repository name: ${repoFullName}`);
  }

  const url = `https://github.com/${owner}/${repo}.git`;

  try {
    await run('git', ['clone', '--depth=1', '--filter=blob:none', url, '.'], { cwd: cloneDir, timeout: 120_000 });
  } catch (error) {
    // Clean up on failure
    await rm(cloneDir, { recursive: true, force: true }).catch(() => {});
    throw new Error(
      `Clone failed for ${repoFullName}: ${error instanceof Error ? error.message : error}`,
    );
  }

  // Size check
  try {
    const stdout = await runCapture('du', ['-sm', cloneDir], { cwd: cloneDir });
    const sizeMb = parseInt(stdout.split('\t')[0], 10) || 0;
    if (sizeMb > MAX_SIZE_MB) {
      await rm(cloneDir, { recursive: true, force: true });
      throw new Error(`Repo too large: ${sizeMb}MB exceeds ${MAX_SIZE_MB}MB budget`);
    }
  } catch (error) {
    if (error instanceof Error && error.message.includes('Repo too large')) throw error;
    // du command itself failed, proceed anyway
  }

  return cloneDir;
}

export async function cloneRepoTempSafe(repoFullName: string, jobId: string): Promise<string | null> {
  try {
    return await cloneRepoTemp(repoFullName, jobId);
  } catch (error) {
    console.warn(`[CloneRunner] safe clone failed for ${repoFullName}:`, error instanceof Error ? error.message : error);
    return null;
  }
}

export async function removeCloneDir(cloneDir: string): Promise<void> {
  if (existsSync(cloneDir)) {
    await rm(cloneDir, { recursive: true, force: true });
  }
}
