/**
 * Cliente HTTP de `/v1/computer` — plano de control de "toma de control /
 * pausa" de la computadora por agente y por superficie (Ola F,
 * `edecan_api.routers.computer`).
 *
 * No hay WebRTC ni streaming acá: la vista por polling vive en
 * `routers/remote.py`. Este vertical slice solo administra quién puede mover
 * cada superficie (`mode`: `agent`|`user`|`paused`).
 *
 * Mismo patrón de auth que `lib/api-misiones.ts` (vertical slice propio).
 */

import { API_BASE_URL, ApiError } from "./api";
import { recoverSessionAfterUnauthorized, isRefreshResultCurrent } from "./session-refresh";
import { getAccessToken, hasSession } from "./tokens";

export type ComputerMode = "agent" | "user" | "paused";

export interface ComputerSession {
  id: string;
  tenant_id: string;
  user_id: string;
  agent_id: string | null;
  kind: string;
  mode: ComputerMode | string;
  ephemeral: boolean;
  status: string;
  workspace_scope: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ComputerSessionCreateInput {
  kind?: string;
  agent_id?: string | null;
  ephemeral?: boolean;
  workspace_scope?: Record<string, unknown>;
  mode?: string;
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

/** `GET /v1/computer/sessions` — sesiones del tenant (filtro opcional `agent_id`). */
export async function listComputerSessions(agentId?: string): Promise<ComputerSession[]> {
  const qs = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : "";
  return apiJson<ComputerSession[]>(`/v1/computer/sessions${qs}`);
}

/** `POST /v1/computer/sessions` — crea una sesión (nace `mode='agent'`). */
export async function createComputerSession(input: ComputerSessionCreateInput): Promise<ComputerSession> {
  return apiJson<ComputerSession>("/v1/computer/sessions", { method: "POST", body: input });
}

/** `POST /v1/computer/sessions/{id}/takeover` — `mode='user'`. */
export async function takeoverComputerSession(id: string): Promise<ComputerSession> {
  return apiJson<ComputerSession>(`/v1/computer/sessions/${id}/takeover`, { method: "POST" });
}

/** `POST /v1/computer/sessions/{id}/return` — `mode='agent'`. */
export async function returnComputerSession(id: string): Promise<ComputerSession> {
  return apiJson<ComputerSession>(`/v1/computer/sessions/${id}/return`, { method: "POST" });
}

/** `POST /v1/computer/sessions/{id}/pause` — `mode='paused'` + `status='paused'`. */
export async function pauseComputerSession(id: string): Promise<ComputerSession> {
  return apiJson<ComputerSession>(`/v1/computer/sessions/${id}/pause`, { method: "POST" });
}

/** `POST /v1/computer/sessions/{id}/resume` — `mode='agent'` + `status='active'`. */
export async function resumeComputerSession(id: string): Promise<ComputerSession> {
  return apiJson<ComputerSession>(`/v1/computer/sessions/${id}/resume`, { method: "POST" });
}

/** `POST /v1/computer/sessions/{id}/end` — `status='ended'`. */
export async function endComputerSession(id: string): Promise<ComputerSession> {
  return apiJson<ComputerSession>(`/v1/computer/sessions/${id}/end`, { method: "POST" });
}

export { ApiError };