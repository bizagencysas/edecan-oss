/**
 * Cliente HTTP de `apps/api/edecan_api/routers/reuniones.py` (`/v1/reuniones/*` —
 * transcripción con el STT del tenant + minutas por LLM del tenant, `ARCHITECTURE.md`
 * §15, WP-V6-05).
 *
 * Mismo motivo de duplicación que `lib/api-viajes.ts`/`lib/api-voz.ts` (ver su
 * docstring): `lib/api.ts` está fuera de la lista de archivos que este paquete de
 * trabajo puede tocar, y sus helpers de fetch autenticado (`authedFetch`/`apiJson`)
 * son privados de ese módulo — así que este archivo replica localmente el mismo
 * patrón (Bearer + un reintento tras refrescar el token en 401, incluido el gate de
 * TOTP). Tipos propios en vez de `lib/types.ts` por el mismo motivo — EXCEPTO
 * `FileOut`, que sí se reusa de `lib/types.ts` (importado, no tocado) porque el
 * selector de archivos de la página necesita `listFiles()` de `lib/api.ts`, que ya
 * existe y devuelve exactamente ese tipo.
 */

import { API_BASE_URL, ApiError } from "./api";
import { recoverSessionAfterUnauthorized, isRefreshResultCurrent } from "./session-refresh";
import { getAccessToken } from "./tokens";

// ---------------------------------------------------------------------------
// Tipos (`edecan_meetings` / `routers/reuniones.py`)
// ---------------------------------------------------------------------------

export type ReunionStatus = "pending" | "running" | "done" | "error";

export interface ReunionAccion {
  tarea: string;
  responsable: string | null;
}

export interface ReunionOut {
  id: string;
  file_id: string;
  titulo: string;
  status: ReunionStatus;
  transcript_file_id: string | null;
  resumen: string | null;
  decisiones: string[];
  acciones: ReunionAccion[];
  temas: string[];
  duracion_segundos: number | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface ReunionIn {
  file_id: string;
  titulo?: string;
}

/**
 * Disclaimer de consentimiento OBLIGATORIO — string EXACTO, duplicado a propósito
 * (`ARCHITECTURE.md` §10.1) de `packages/meetings/edecan_meetings/tools.py`
 * (`DISCLAIMER_CONSENTIMIENTO`) y `apps/api/edecan_api/routers/reuniones.py`
 * (constante del mismo nombre) — los tres deben decir EXACTAMENTE lo mismo, byte
 * por byte. NO reformules esta frase (mismo criterio que `ATTESTATION_TEXT` en
 * `app/(app)/app/voz/page.tsx`).
 */
export const DISCLAIMER_CONSENTIMIENTO =
  "Recuerda: asegúrate de contar con el consentimiento de todos los " +
  "participantes para grabar y transcribir esta reunión.";

// ---------------------------------------------------------------------------
// Auth (mismo patrón que `lib/api.ts` / `lib/api-viajes.ts`)
// ---------------------------------------------------------------------------

async function rawFetch(path: string, init: RequestInit): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = getAccessToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return fetch(`${API_BASE_URL}${path}`, { ...init, headers });
}

async function authedFetch(path: string, init: RequestInit = {}): Promise<Response> {
  let res = await rawFetch(path, init);
  if (res.status === 401) {
    const result = await recoverSessionAfterUnauthorized(API_BASE_URL);
    if (isRefreshResultCurrent(result)) res = await rawFetch(path, init);
  }
  return res;
}

async function extractErrorMessage(res: Response): Promise<{ message: string; detail: unknown }> {
  let detail: unknown;
  try {
    detail = await res.clone().json();
  } catch {
    return { message: `Error HTTP ${res.status}`, detail: undefined };
  }
  const raw = (detail as { detail?: unknown } | null)?.detail;
  if (typeof raw === "string") return { message: raw, detail };
  if (Array.isArray(raw)) {
    const message = raw
      .map((item) =>
        typeof item === "object" && item && "msg" in item
          ? String((item as { msg: unknown }).msg)
          : String(item),
      )
      .join(" · ");
    return { message: message || `Error HTTP ${res.status}`, detail };
  }
  return { message: `Error HTTP ${res.status}`, detail };
}

async function apiJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const body = init.body;
  if (typeof body === "string") {
    headers.set("Content-Type", "application/json");
  }
  const res = await authedFetch(path, { ...init, headers, body });
  if (!res.ok) {
    const { message, detail } = await extractErrorMessage(res);
    throw new ApiError(res.status, message, detail);
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

function jsonBody(value: unknown): RequestInit {
  return { body: JSON.stringify(value) };
}

// ---------------------------------------------------------------------------
// /v1/reuniones
// ---------------------------------------------------------------------------

export async function crearReunion(input: ReunionIn): Promise<ReunionOut> {
  return apiJson<ReunionOut>("/v1/reuniones", { method: "POST", ...jsonBody(input) });
}

export async function listarReuniones(): Promise<ReunionOut[]> {
  return apiJson<ReunionOut[]>("/v1/reuniones");
}

export async function obtenerReunion(id: string): Promise<ReunionOut> {
  return apiJson<ReunionOut>(`/v1/reuniones/${encodeURIComponent(id)}`);
}

export async function borrarReunion(id: string): Promise<void> {
  await apiJson<void>(`/v1/reuniones/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export { ApiError };
