// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Corpus Cleanup — Temp directory lifecycle management                        ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { readdir, rm, stat } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { exec } from 'node:child_process';
import { promisify } from 'node:util';

const execAsync = promisify(exec);
const TMP_ROOT = path.join(os.tmpdir(), 'fydesign-corpus');

export async function cleanupCloneDir(cloneDir: string): Promise<void> {
  if (existsSync(cloneDir)) {
    await rm(cloneDir, { recursive: true, force: true });
  }
}

export async function cleanupAllTempDirs(): Promise<number> {
  if (!existsSync(TMP_ROOT)) return 0;

  let count = 0;
  const entries = await readdir(TMP_ROOT, { withFileTypes: true });
  for (const entry of entries) {
    if (entry.isDirectory()) {
      const fullPath = path.join(TMP_ROOT, entry.name);
      await rm(fullPath, { recursive: true, force: true });
      count++;
    }
  }

  return count;
}

export async function cleanupOldTempDirs(maxAgeHours: number): Promise<number> {
  if (!existsSync(TMP_ROOT)) return 0;

  const cutoff = Date.now() - maxAgeHours * 60 * 60 * 1000;
  let count = 0;

  const entries = await readdir(TMP_ROOT, { withFileTypes: true });
  for (const entry of entries) {
    if (entry.isDirectory()) {
      const fullPath = path.join(TMP_ROOT, entry.name);
      try {
        const stats = await stat(fullPath);
        if (stats.mtimeMs < cutoff) {
          await rm(fullPath, { recursive: true, force: true });
          count++;
        }
      } catch (err) {
        console.warn('[cleanup] stat failed for', fullPath, ':', err instanceof Error ? err.message : err);
      }
    }
  }

  return count;
}

export async function getTempDiskUsage(): Promise<string> {
  if (!existsSync(TMP_ROOT)) return '0K';

  try {
    const { stdout } = await execAsync(`du -sh "${TMP_ROOT}"`);
    return stdout.split('\t')[0]?.trim() || '0K';
  } catch {
    return 'unknown';
  }
}
