export type DesktopUpdateChannel = "stable" | "preview";

export interface DesktopUpdateMetadata {
  version: string;
  currentVersion: string;
  notes: string | null;
  publishedAt: string | null;
  channel: DesktopUpdateChannel;
}

export interface DesktopUpdateCheckResult {
  currentVersion: string;
  update: DesktopUpdateMetadata | null;
}

export type DesktopUpdateProgress =
  | { event: "started"; content_length: number | null }
  | { event: "progress"; chunk_length: number }
  | { event: "finished" };

export const DESKTOP_UPDATE_CHANNEL_KEY = "edecan_desktop_update_channel";
export const DESKTOP_UPDATE_LAST_CHECK_KEY = "edecan_desktop_update_last_check";
export const DESKTOP_UPDATE_CHECK_INTERVAL_MS = 4 * 60 * 60 * 1000;

export function normalizeDesktopUpdateChannel(
  value: string | null | undefined,
): DesktopUpdateChannel {
  return value === "preview" ? "preview" : "stable";
}

export function shouldCheckForDesktopUpdate(
  lastCheckRaw: string | null,
  now = Date.now(),
): boolean {
  const lastCheck = Number(lastCheckRaw);
  return (
    !Number.isFinite(lastCheck) ||
    lastCheck <= 0 ||
    now - lastCheck >= DESKTOP_UPDATE_CHECK_INTERVAL_MS
  );
}

export function updateProgressPercent(
  downloaded: number,
  total: number | null,
): number | null {
  if (!total || total <= 0) return null;
  return Math.min(100, Math.max(0, Math.round((downloaded / total) * 100)));
}
