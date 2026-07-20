/**
 * Almacenamiento efímero de tokens JWT en el navegador (`sessionStorage`). Módulo
 * separado de `api.ts` y `auth-context.tsx` para que ambos puedan leerlo sin
 * depender uno del otro (evita import circular entre el cliente HTTP, que
 * necesita el access token para cada request, y el contexto de React, que
 * necesita el cliente HTTP para llamar a `/v1/auth/*`).
 */

const ACCESS_KEY = "edecan_access_token";
const REFRESH_KEY = "edecan_refresh_token";

function hasStorage(): boolean {
  return typeof window !== "undefined" && typeof window.sessionStorage !== "undefined";
}

function removeLegacyPersistentTokens(): void {
  if (typeof window === "undefined" || typeof window.localStorage === "undefined") return;
  window.localStorage.removeItem(ACCESS_KEY);
  window.localStorage.removeItem(REFRESH_KEY);
}

export function getAccessToken(): string | null {
  if (!hasStorage()) return null;
  removeLegacyPersistentTokens();
  return window.sessionStorage.getItem(ACCESS_KEY);
}

export function getRefreshToken(): string | null {
  if (!hasStorage()) return null;
  removeLegacyPersistentTokens();
  return window.sessionStorage.getItem(REFRESH_KEY);
}

export function setTokens(accessToken: string, refreshToken: string): void {
  if (!hasStorage()) return;
  window.sessionStorage.setItem(ACCESS_KEY, accessToken);
  window.sessionStorage.setItem(REFRESH_KEY, refreshToken);
  // Limpieza de upgrades: versiones anteriores persistían ambos secretos en
  // localStorage, donde sobrevivían al cierre completo del navegador.
  removeLegacyPersistentTokens();
}

export function clearTokens(): void {
  if (!hasStorage()) return;
  window.sessionStorage.removeItem(ACCESS_KEY);
  window.sessionStorage.removeItem(REFRESH_KEY);
  removeLegacyPersistentTokens();
}

export function hasSession(): boolean {
  return getAccessToken() !== null;
}
