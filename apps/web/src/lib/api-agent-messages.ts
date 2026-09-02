/**
 * Cliente HTTP de `/v1/agents/messages` — mensajería directa entre agentes
 * (`GET` lista, `POST` envía un mensaje de un agente a otro). El backend se
 * construye en paralelo: este vertical slice se apoya en el contrato limpio y
 * degrada con gracia si la ruta todavía no existe (404 → la UI muestra
 * "Próximamente", nunca un éxito falso).
 *
 * Mismo patrón de vertical slice que `lib/api-teams.ts` / `lib/api-misiones.ts`:
 * `lib/api.ts` es compartido, así que acá se calca su manejo de autenticación
 * (Bearer + un reintento tras refrescar en 401, con dedupe en `session-refresh`).
 */

import { API_BASE_URL, ApiError } from "./api";
import { recoverSessionAfterUnauthorized, isRefreshResultCurrent } from "./session-refresh";
import { getAccessToken, hasSession } from "./tokens";

/** Mensaje directo entre dos compañeros persistentes. Campos tolerantes: el
 * backend puede variar de forma mientras se monta. */
export interface AgentMessage {
  id: string;
  from_agent_id: string | null;
  to_agent_id: string | null;
  from_name?: string | null;
  to_name?: string | null;
  content: string;
  created_at: string;
}

export interface SendAgentMessageInput {
  from_agent_id: string;
  to_agent_id: string;
  content: string;
}

function isNotFound(err: unknown): boolean {
  return err instanceof ApiError && err.status === 404;
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

/** `GET /v1/agents/messages` — mensajes entre agentes del tenant. */
export async function listAgentMessages(): Promise<AgentMessage[]> {
  return apiJson<AgentMessage[]>("/v1/agents/messages");
}

/** `POST /v1/agents/messages` — envía un mensaje de un agente a otro. */
export async function sendAgentMessage(input: SendAgentMessageInput): Promise<AgentMessage> {
  return apiJson<AgentMessage>("/v1/agents/messages", { method: "POST", body: input });
}

export { ApiError, isNotFound };
