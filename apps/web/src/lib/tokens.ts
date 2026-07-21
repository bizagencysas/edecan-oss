/**
 * Almacenamiento de tokens JWT. El access token siempre es efímero. En la app
 * de escritorio, el refresh token persiste para restaurar la sesión al volver
 * a abrir Edecán; en un navegador normal sigue limitado a `sessionStorage`.
 * separado de `api.ts` y `auth-context.tsx` para que ambos puedan leerlo sin
 * depender uno del otro (evita import circular entre el cliente HTTP, que
 * necesita el access token para cada request, y el contexto de React, que
 * necesita el cliente HTTP para llamar a `/v1/auth/*`).
 */

const ACCESS_KEY = "edecan_access_token";
const REFRESH_KEY = "edecan_refresh_token";
const DESKTOP_USER_AGENT_PREFIX = "EdecanDesktop/";

/**
 * Monotonic generation for the session owned by this JavaScript runtime.
 * Every explicit login, registration, logout or successful token rotation
 * advances it. A refresh response may only be committed if the generation
 * and refresh token it started with are still current.
 *
 * This closes two security-sensitive races: a late response cannot resurrect
 * a logged-out session and cannot overwrite credentials from a newer login.
 */
let sessionGeneration = 0;

export interface SessionSnapshot {
  generation: number;
  refreshToken: string | null;
}

function hasSessionStorage(): boolean {
  return typeof window !== "undefined" && typeof window.sessionStorage !== "undefined";
}

function isDesktopApp(): boolean {
  if (typeof window === "undefined") return false;
  if ("__TAURI__" in window) return true;
  // La ventana principal de Tauri navega al backend HTTP local. Según la
  // plataforma, el global IPC no se inyecta en ese origen remoto; Rust marca
  // la WebView con un User-Agent propio para no confundirla con Safari/Chrome.
  return window.navigator?.userAgent?.includes(DESKTOP_USER_AGENT_PREFIX) === true;
}

function hasPersistentStorage(): boolean {
  return typeof window !== "undefined" && typeof window.localStorage !== "undefined";
}

export function getAccessToken(): string | null {
  if (!hasSessionStorage()) return null;
  return window.sessionStorage.getItem(ACCESS_KEY);
}

export function getRefreshToken(): string | null {
  if (!hasSessionStorage()) return null;
  const ephemeral = window.sessionStorage.getItem(REFRESH_KEY);
  if (!isDesktopApp() || !hasPersistentStorage()) return ephemeral;
  const persistent = window.localStorage.getItem(REFRESH_KEY);
  if (persistent) return persistent;
  if (ephemeral) {
    window.localStorage.setItem(REFRESH_KEY, ephemeral);
    window.sessionStorage.removeItem(REFRESH_KEY);
  }
  return ephemeral;
}

export function setTokens(accessToken: string, refreshToken: string): void {
  sessionGeneration += 1;
  if (!hasSessionStorage()) return;
  window.sessionStorage.setItem(ACCESS_KEY, accessToken);
  if (isDesktopApp() && hasPersistentStorage()) {
    window.localStorage.setItem(REFRESH_KEY, refreshToken);
    window.sessionStorage.removeItem(REFRESH_KEY);
  } else {
    window.sessionStorage.setItem(REFRESH_KEY, refreshToken);
    if (hasPersistentStorage()) window.localStorage.removeItem(REFRESH_KEY);
  }
  // El access token nunca sobrevive al cierre completo, ni siquiera en Tauri.
  if (hasPersistentStorage()) window.localStorage.removeItem(ACCESS_KEY);
}

export function clearTokens(): void {
  sessionGeneration += 1;
  if (!hasSessionStorage()) return;
  window.sessionStorage.removeItem(ACCESS_KEY);
  window.sessionStorage.removeItem(REFRESH_KEY);
  if (hasPersistentStorage()) {
    window.localStorage.removeItem(ACCESS_KEY);
    window.localStorage.removeItem(REFRESH_KEY);
  }
}

export function hasSession(): boolean {
  return getAccessToken() !== null || getRefreshToken() !== null;
}

export function getSessionSnapshot(): SessionSnapshot {
  return {
    generation: sessionGeneration,
    refreshToken: getRefreshToken(),
  };
}

export function isSessionSnapshotCurrent(snapshot: SessionSnapshot): boolean {
  return (
    sessionGeneration === snapshot.generation &&
    getRefreshToken() === snapshot.refreshToken
  );
}

/** Atomically (within this runtime) commits a rotation only to its origin session. */
export function setTokensIfSessionCurrent(
  snapshot: SessionSnapshot,
  accessToken: string,
  refreshToken: string,
): boolean {
  if (!isSessionSnapshotCurrent(snapshot)) {
    return false;
  }
  setTokens(accessToken, refreshToken);
  return true;
}

/** Clears only the session represented by `snapshot`, never a newer login. */
export function clearTokensIfSessionCurrent(snapshot: SessionSnapshot): boolean {
  if (!isSessionSnapshotCurrent(snapshot)) return false;
  clearTokens();
  return true;
}
