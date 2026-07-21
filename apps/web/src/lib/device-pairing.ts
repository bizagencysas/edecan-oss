export interface DevicePairingOut {
  pairing_uri: string;
  expires_at: string;
  expires_in_seconds: number;
}

const DEFAULT_PAIRING_LIFETIME_SECONDS = 10 * 60;

/** Prefiere la fecha absoluta del servidor y conserva un fallback acotado. */
export function pairingExpiryMs(pairing: DevicePairingOut, nowMs = Date.now()): number {
  const absolute = Date.parse(pairing.expires_at);
  if (Number.isFinite(absolute)) return absolute;
  const lifetime = Number.isFinite(pairing.expires_in_seconds)
    ? Math.max(0, pairing.expires_in_seconds)
    : DEFAULT_PAIRING_LIFETIME_SECONDS;
  return nowMs + lifetime * 1000;
}

export function pairingSecondsLeft(expiresAtMs: number, nowMs = Date.now()): number {
  return Math.max(0, Math.ceil((expiresAtMs - nowMs) / 1000));
}

export function formatPairingTimeLeft(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(safe / 60);
  const remainder = safe % 60;
  return `${minutes}:${remainder.toString().padStart(2, "0")}`;
}
