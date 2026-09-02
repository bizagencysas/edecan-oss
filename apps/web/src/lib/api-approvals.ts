/**
 * Cliente HTTP de `/v1/approvals` (`edecan_api.routers.approvals`): aprobaciones
 * durables de acciones peligrosas del chat que sobreviven un reload. Vertical
 * slice propio (mismo criterio que `lib/api-automatizaciones.ts`, ver su
 * docstring): `lib/api.ts` es compartido y no se edita para esto, así que este
 * archivo importa de ahí `API_BASE_URL`/`ApiError` y calca su manejo de auth
 * (Bearer + un reintento tras refrescar en 401), con dedupe global en
 * `session-refresh`.
 *
 * Ojo con `approveApproval`: `POST /v1/approvals/{id}/approve` NO devuelve JSON —
 * reanuda el turno del agente y responde con `StreamingResponse` (SSE). Acá se
 * drena el cuerpo a completitud (el turno corre server-side) y se ignoran los
 * eventos; los errores de validación previos al streaming sí llegan como 4xx
 * normales y se traducen a `ApiError`.
 */

import { API_BASE_URL, ApiError } from "./api";
import { recoverSessionAfterUnauthorized, isRefreshResultCurrent } from "./session-refresh";
import { getAccessToken } from "./tokens";

/** `_public_row` de `routers/approvals.py` (sin `pending_turn`, redactado). */
export interface Approval {
  id: string;
  conversation_id: string;
  tool_call_id: string;
  /** Nombre de la herramienta que pidió confirmación (`snapshot.name`). */
  name: string | null;
  args: Record<string, unknown>;
  status: string;
  created_at: string | null;
  decided_at: string | null;
}

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
  return { message: `Error HTTP ${res.status}`, detail };
}

async function apiJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const body = init.body;
  if (typeof body === "string") headers.set("Content-Type", "application/json");
  const res = await authedFetch(path, { ...init, headers, body });
  if (!res.ok) {
    const { message, detail } = await extractErrorMessage(res);
    throw new ApiError(res.status, message, detail);
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

/** `GET /v1/approvals` — pendientes (status `pending`), más recientes primero. */
export async function listApprovals(): Promise<Approval[]> {
  return apiJson<Approval[]>("/v1/approvals");
}

/** `POST /v1/approvals/{id}/deny` — marca `denied` y devuelve JSON. */
export async function denyApproval(id: string): Promise<{ approval_id: string; status: string }> {
  return apiJson<{ approval_id: string; status: string }>(`/v1/approvals/${id}/deny`, {
    method: "POST",
  });
}

/** `POST /v1/approvals/{id}/approve` — reanuda el turno (SSE); drena el stream. */
export async function approveApproval(id: string): Promise<void> {
  const res = await authedFetch(`/v1/approvals/${id}/approve`, { method: "POST" });
  if (!res.ok) {
    const { message, detail } = await extractErrorMessage(res);
    throw new ApiError(res.status, message, detail);
  }
  if (res.body) {
    const reader = res.body.getReader();
    try {
      for (;;) {
        const { done } = await reader.read();
        if (done) break;
      }
    } finally {
      try {
        reader.releaseLock();
      } catch {
        // El lector ya quedó liberado si el turno abortó a mitad de stream.
      }
    }
  }
}

export { ApiError };