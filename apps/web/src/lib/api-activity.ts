/**
 * Cliente HTTP de `/v1/activity` y de `POST /v1/agents/workers/pause-all`
 * (parada de emergencia). Ambos contratos se están construyendo EN PARALELO
 * por el agente de backend; este vertical slice codea contra la forma
 * comprometida y degrada con gracia si una ruta todavía no existe.
 *
 * `lib/api.ts` es compartido y no se edita, así que este archivo calca su
 * manejo de autenticación (Bearer + un reintento tras refrescar en 401, con
 * dedupe global en `session-refresh`) — mismo criterio que
 * `lib/api-misiones.ts`/`lib/api-mcp.ts` (ver sus docstrings).
 *
 * `listActivity` SÍ deja que un 404 se propague como `ApiError`: el drawer de
 * actividad distingue "backend no montado todavía" (→ estado "Próximamente",
 * nunca un éxito falso) de "sin actividad". `pauseAllWorkers` es una escritura:
 * un 404 se muestra como error normal.
 */

import { API_BASE_URL, ApiError } from "./api";
import { recoverSessionAfterUnauthorized, isRefreshResultCurrent } from "./session-refresh";
import { getAccessToken, hasSession } from "./tokens";

/** Forma comprometida de `GET /v1/activity` (acciones observables recientes). */
export interface ActivityItem {
  type: string;
  agent: string | null;
  summary: string;
  at: string | null;
  status: string;
}

async function rawFetch(path: string, init: RequestInit): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = getAccessToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return fetch(`${API_BASE_URL}${path}`, { ...init, headers });
}

function redirectToLogin(): void {
  if (typeof window === "undefined" || hasSession()) return;
  if (window.location.pathname !== "/login") {
    window.location.assign("/login/");
  }
}

async function authedFetch(path: string, init: RequestInit = {}): Promise<Response> {
  let res = await rawFetch(path, init);
  if (res.status === 401) {
    const result = await recoverSessionAfterUnauthorized(API_BASE_URL);
    if (isRefreshResultCurrent(result)) {
      res = await rawFetch(path, init);
    } else if (!result.ok && result.reason === "invalid") {
      redirectToLogin();
    }
  }
  return res;
}

async function extractErrorMessage(res: Response): Promise<{ message: string; detail: unknown }> {
  let detail: unknown;
  try {
    detail = await res.clone().json();
  } catch {
    try {
      const text = await res.text();
      return { message: text || `Error HTTP ${res.status}`, detail: text };
    } catch {
      return { message: `Error HTTP ${res.status}`, detail: undefined };
    }
  }
  const raw = (detail as { detail?: unknown } | null)?.detail;
  if (typeof raw === "string") return { message: raw, detail };
  if (Array.isArray(raw)) {
    const message = raw
      .map((item) => (typeof item === "object" && item && "msg" in item ? String(item.msg) : String(item)))
      .join(" · ");
    return { message: message || `Error HTTP ${res.status}`, detail };
  }
  return { message: `Error HTTP ${res.status}`, detail };
}

async function parseJsonOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const { message, detail } = await extractErrorMessage(res);
    throw new ApiError(res.status, message, detail);
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

interface JsonRequestInit extends Omit<RequestInit, "body"> {
  body?: unknown;
}

async function apiJson<T>(path: string, init: JsonRequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  let body: BodyInit | undefined;
  if (init.body !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(init.body);
  }
  const res = await authedFetch(path, { ...init, headers, body });
  return parseJsonOrThrow<T>(res);
}

/** `GET /v1/activity` — acciones observables recientes. Un 404 se propaga
 * como `ApiError` (status 404) para que el consumidor pinte "Próximamente". */
export async function listActivity(): Promise<ActivityItem[]> {
  return apiJson<ActivityItem[]>("/v1/activity");
}

/** `POST /v1/agents/workers/pause-all` — parada de emergencia de todos los
 * compañeros persistentes. Devuelve el total pausado cuando el backend lo expone. */
export async function pauseAllWorkers(): Promise<{ paused?: number }> {
  return apiJson<{ paused?: number }>("/v1/agents/workers/pause-all", { method: "POST" });
}

export { ApiError };