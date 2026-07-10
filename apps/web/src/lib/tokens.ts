/**
 * Almacenamiento de tokens JWT en el navegador (localStorage). Módulo
 * separado de `api.ts` y `auth-context.tsx` para que ambos puedan leerlo sin
 * depender uno del otro (evita import circular entre el cliente HTTP, que
 * necesita el access token para cada request, y el contexto de React, que
 * necesita el cliente HTTP para llamar a `/v1/auth/*`).
 */

const ACCESS_KEY = "edecan_access_token";
const REFRESH_KEY = "edecan_refresh_token";

function hasStorage(): boolean {
  return typeof window !== "undefined" && typeof window.localStorage !== "undefined";
}

export function getAccessToken(): string | null {
  if (!hasStorage()) return null;
  return window.localStorage.getItem(ACCESS_KEY);
}

export function getRefreshToken(): string | null {
  if (!hasStorage()) return null;
  return window.localStorage.getItem(REFRESH_KEY);
}

export function setTokens(accessToken: string, refreshToken: string): void {
  if (!hasStorage()) return;
  window.localStorage.setItem(ACCESS_KEY, accessToken);
  window.localStorage.setItem(REFRESH_KEY, refreshToken);
}

export function clearTokens(): void {
  if (!hasStorage()) return;
  window.localStorage.removeItem(ACCESS_KEY);
  window.localStorage.removeItem(REFRESH_KEY);
}

export function hasSession(): boolean {
  return getAccessToken() !== null;
}
