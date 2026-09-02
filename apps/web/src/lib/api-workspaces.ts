/**
 * Cliente HTTP de `/v1/workspaces` (espacios de trabajo de equipo) — el
 * backend se está construyendo en paralelo; este vertical slice se apoya en
 * el contrato limpio y degrada con gracia si la ruta todavía no existe (404 →
 * la página muestra "Próximamente", nunca un éxito falso).
 *
 * Mismo patrón de auth que `lib/api-misiones.ts` (ver su docstring): vertical
 * slice propio, `lib/api.ts` no se edita.
 */

import { API_BASE_URL, ApiError } from "./api";
import { recoverSessionAfterUnauthorized, isRefreshResultCurrent } from "./session-refresh";
import { getAccessToken, hasSession } from "./tokens";

export interface WorkspaceAgent {
  agent_id: string;
  agent_name?: string | null;
}

export interface Workspace {
  id: string;
  name: string;
  agents: WorkspaceAgent[];
  created_at?: string | null;
  updated_at?: string | null;
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

/** `GET /v1/workspaces` — espacios de trabajo del tenant. Lanza `ApiError`
 * (404 incluido) para que la página distinga "no montado" de "sin workspaces". */
export async function listWorkspaces(): Promise<Workspace[]> {
  return apiJson<Workspace[]>("/v1/workspaces");
}

/** Variante tolerante a 404 para consumidores que degradan a lista vacía. */
export async function listWorkspacesTolerant(): Promise<Workspace[]> {
  try {
    return await apiJson<Workspace[]>("/v1/workspaces");
  } catch (err) {
    if (isNotFound(err)) return [];
    throw err;
  }
}

/** `POST /v1/workspaces` — crea un espacio de trabajo. */
export async function createWorkspace(name: string): Promise<Workspace> {
  return apiJson<Workspace>("/v1/workspaces", { method: "POST", body: { name } });
}

/** `POST /v1/workspaces/{id}/agents` — asigna un agente. */
export async function addWorkspaceAgent(id: string, agentId: string): Promise<Workspace> {
  return apiJson<Workspace>(`/v1/workspaces/${id}/agents`, {
    method: "POST",
    body: { agent_id: agentId },
  });
}

/** `DELETE /v1/workspaces/{id}/agents/{agent_id}` — quita un agente. */
export async function removeWorkspaceAgent(id: string, agentId: string): Promise<void> {
  await apiJson<void>(`/v1/workspaces/${id}/agents/${encodeURIComponent(agentId)}`, {
    method: "DELETE",
  });
}

export { ApiError };