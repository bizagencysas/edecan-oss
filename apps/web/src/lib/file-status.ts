import type { FileOut } from "./types";

export const FILE_STATUS_POLL_MS = 3_000;

const ACTIVE_FILE_STATUSES = new Set(["uploaded", "processing"]);

/** Solo consulta mientras el worker todavía puede cambiar el estado. */
export function filesNeedRefresh(files: readonly Pick<FileOut, "status">[]): boolean {
  return files.some((file) => ACTIVE_FILE_STATUSES.has(file.status));
}

/**
 * Keeps server ordering while retaining records created locally that are not
 * visible in a potentially eventually-consistent list response yet.
 */
export function mergeFileSnapshots(
  current: readonly FileOut[],
  incoming: readonly FileOut[],
): FileOut[] {
  const incomingIds = new Set(incoming.map((file) => file.id));
  return [...incoming, ...current.filter((file) => !incomingIds.has(file.id))];
}

export function upsertFile(files: readonly FileOut[], file: FileOut): FileOut[] {
  return [file, ...files.filter((candidate) => candidate.id !== file.id)];
}

export interface LatestRequestTicket {
  isCurrent(): boolean;
}

/** Small deterministic guard against out-of-order async list responses. */
export function createLatestRequestGuard(): {
  begin(): LatestRequestTicket;
  invalidate(): void;
} {
  let generation = 0;
  return {
    begin() {
      const requestGeneration = ++generation;
      return { isCurrent: () => requestGeneration === generation };
    },
    invalidate() {
      generation += 1;
    },
  };
}
