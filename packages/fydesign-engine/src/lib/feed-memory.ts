// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Feed memory — per-brand history so generations stay non-repetitive AND       ║
// ║  visually consistent (a coherent feed, not a chaotic one).                    ║
// ║                                                                              ║
// ║  Stored as JSONL at  <brandDir>/.feed-memory.jsonl  (one line per generation).║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { mkdir, readFile, appendFile } from 'node:fs/promises';
import { join } from 'node:path';

export interface MemoryEntry {
  date: string;        // ISO timestamp
  media: string;       // image | post | campaign | edit | svg | video
  brief: string;       // the user's prompt / topic
  headline?: string;   // the generated headline (posts)
  concept?: string;    // short summary of the visual concept
  files?: number;      // how many assets produced
}

const FILE = '.feed-memory.jsonl';

/** Load the brand's recent feed history (most-recent `limit` entries). */
export async function loadFeedMemory(brandDir: string, limit = 30): Promise<MemoryEntry[]> {
  try {
    const txt = await readFile(join(brandDir, FILE), 'utf8');
    const lines = txt.split('\n').filter((l) => l.trim());
    const out: MemoryEntry[] = [];
    for (const l of lines.slice(-limit)) {
      try { out.push(JSON.parse(l) as MemoryEntry); } catch { /* skip bad line */ }
    }
    return out;
  } catch {
    return [];
  }
}

/** Append one generation to the brand's feed history. Never throws. */
export async function appendFeedMemory(brandDir: string, entry: MemoryEntry): Promise<void> {
  try {
    await mkdir(brandDir, { recursive: true });
    await appendFile(join(brandDir, FILE), JSON.stringify(entry) + '\n', 'utf8');
  } catch {
    /* noop — memory is best-effort */
  }
}

/** A compact digest of recent posts for the planning prompt. */
export function memoryDigest(entries: MemoryEntry[]): string {
  if (!entries.length) return '';
  const recent = entries.slice(-20);
  return recent
    .map((e, i) => {
      const day = (e.date || '').slice(0, 10);
      const head = e.headline ? ` — "${e.headline}"` : '';
      const brief = e.brief ? ` (${e.brief.slice(0, 70)})` : '';
      return `${i + 1}. [${day}] ${e.media}${head}${brief}`;
    })
    .join('\n');
}
