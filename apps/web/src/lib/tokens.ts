/**
 * Almacenamiento efímero de los JWT de la interfaz web. En la app instalada,
 * la identidad durable es el dueño guardado en la base local, no un refresh
 * token del proceso anterior: cada apertura obtiene un par nuevo por
 * loopback. En navegador, ambos tokens también quedan en `sessionStorage`.
 *
 * Este módulo está separado de `api.ts` y `auth-context.tsx` para evitar un
 * ciclo entre el cliente HTTP y el contexto React.
 */

const ACCESS_KEY = "edecan_access_token";
const REFRESH_KEY = "edecan_refresh_token";
const DESKTOP_RUNTIME_KEY = "edecan_desktop_runtime";
const DESKTOP_CAPABILITY_KEY = "edecan_desktop_capability";
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

function captureDesktopLaunch(): void {
  if (!hasSessionStorage()) return;
  const params = new URLSearchParams(window.location?.search ?? "");
  if (params.get("edecan_desktop") === "1") {
    window.sessionStorage.setItem(DESKTOP_RUNTIME_KEY, "1");
  }

  const hash = window.location?.hash ?? "";
  const capability = new URLSearchParams(hash.startsWith("#") ? hash.slice(1) : hash).get(
    "edecan_capability",
  );
  if (!capability) return;
  window.sessionStorage.setItem(DESKTOP_CAPABILITY_KEY, capability);
  // El secreto ya quedó en memoria de esta WebView/pestaña. Se elimina de la
  // barra e historial sin recargar para que no aparezca en capturas o enlaces.
  window.history?.replaceState?.(
    null,
    "",
    `${window.location.pathname || "/"}${window.location.search || ""}`,
  );
}

export function isDesktopApp(): boolean {
  if (typeof window === "undefined") return false;
  if ("__TAURI__" in window) return true;
  if (hasSessionStorage()) {
    // La ventana nativa siempre arranca en `/?edecan_desktop=1`. Guardamos
    // esa señal no secreta solo durante la vida de la WebView para que siga
    // disponible después de navegar a `/login/` o `/app/`. En cada apertura
    // Rust vuelve a emitirla antes de crear la sesión local del proceso.
    captureDesktopLaunch();
    if (window.sessionStorage.getItem(DESKTOP_RUNTIME_KEY) === "1") return true;
  }
  // La ventana principal de Tauri navega al backend HTTP local. Según la
  // plataforma, el global IPC no se inyecta en ese origen remoto; Rust marca
  // la WebView con un User-Agent propio como respaldo para no confundirla con
  // Safari/Chrome.
  return window.navigator?.userAgent?.includes(DESKTOP_USER_AGENT_PREFIX) === true;
}

export function getDesktopCapability(): string | null {
  if (!hasSessionStorage()) return null;
  captureDesktopLaunch();
  return window.sessionStorage.getItem(DESKTOP_CAPABILITY_KEY);
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
  // Limpia la credencial persistente usada por versiones 0.7 anteriores. Ya
  // no representa la identidad del Edecán local y puede apuntar a una sesión
  // de Redis que desapareció al cerrar el backend.
  if (isDesktopApp() && hasPersistentStorage()) {
    window.localStorage.removeItem(REFRESH_KEY);
  }
  return window.sessionStorage.getItem(REFRESH_KEY);
}

export function setTokens(accessToken: string, refreshToken: string): void {
  sessionGeneration += 1;
  if (!hasSessionStorage()) return;
  window.sessionStorage.setItem(ACCESS_KEY, accessToken);
  window.sessionStorage.setItem(REFRESH_KEY, refreshToken);
  // Ningún JWT de la UI sobrevive al cierre completo, ni siquiera en Tauri.
  if (hasPersistentStorage()) {
    window.localStorage.removeItem(ACCESS_KEY);
    window.localStorage.removeItem(REFRESH_KEY);
  }
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
