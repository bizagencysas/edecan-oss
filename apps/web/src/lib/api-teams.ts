/**
 * Cliente HTTP de `/v1/teams`, `/v1/teams/{id}/message(s)` y
 * `/v1/messages/{id}/*` (reacciones e hilos) — el backend de equipos se está
 * construyendo en paralelo; este vertical slice se apoya en el contrato
 * limpio y degrada con gracia si una ruta todavía no existe (404 → la página
 * muestra "Próximamente", nunca un éxito falso).
 *
 * Vertical slice propio (mismo criterio que `lib/api-misiones.ts`, ver su
 * docstring): `lib/api.ts` es compartido y no se edita, así que este archivo
 * importa de ahí `API_BASE_URL`/`ApiError` y calca su manejo de autenticación
 * (Bearer + un reintento tras refrescar en 401), con dedupe global en
 * `session-refresh`.
 */

import type { AgentEvent } from "./types";
import { API_BASE_URL, ApiError } from "./api";
import { recoverSessionAfterUnauthorized, isRefreshResultCurrent } from "./session-refresh";
import { getAccessToken, hasSession } from "./tokens";
import { parseAgentEvent } from "./chat-blocks";
import { SseDataParser } from "./sse";

// --- Tipos (contrato limpio; los campos se tratan con tolerancia porque el
// backend todavía puede variar de forma mientras se monta) -------------------

export interface TeamMember {
  agent_id: string;
  role: string | null;
  /** Nombre visible resuelto por el backend cuando está disponible. */
  agent_name?: string | null;
}

export interface Team {
  id: string;
  name: string;
  members: TeamMember[];
  created_at?: string | null;
  updated_at?: string | null;
}

export interface TeamMessage {
  id: string;
  team_id?: string;
  role: string;
  text: string;
  sender_id?: string | null;
  sender_name?: string | null;
  agent_id?: string | null;
  reactions?: MessageReaction[];
  created_at?: string | null;
}

export interface MessageReaction {
  emoji: string;
  count: number;
  /** `true` cuando la reacción la puso el usuario actual (si el backend lo expone). */
  me?: boolean;
}

export interface ThreadReply {
  id: string;
  message_id: string;
  text: string;
  role: string;
  created_at: string;
  /** El backend devuelve `content` (JSONB {text}), no `text` plano. */
  content?: { text?: string } | string;
}

/** Evento extra del stream de equipos: un miembro delegó trabajo a otro. */
export interface TeamDelegationEvent {
  type: "delegation";
  agent_id: string | null;
  agent_name: string | null;
  instruction: string | null;
  text: string | null;
}

export type TeamStreamEvent = AgentEvent | TeamDelegationEvent;

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function optString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function parseTeamEvent(value: unknown): TeamStreamEvent | null {
  const raw = record(value);
  if (raw && raw.type === "delegation") {
    return {
      type: "delegation",
      agent_id: optString(raw.agent_id),
      agent_name: optString(raw.agent_name),
      instruction: optString(raw.instruction),
      text: optString(raw.text),
    };
  }
  return parseAgentEvent(value);
}

function isNotFound(err: unknown): boolean {
  return err instanceof ApiError && err.status === 404;
}

// --- Fetch autenticado con refresh-on-401 (calca lib/api.ts, ver docstring) -

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

// --- Equipos ----------------------------------------------------------------

/** `GET /v1/teams` — lista de equipos del tenant. Lanza `ApiError` (404 incluido)
 * para que la página pueda distinguir "backend no montado" de "sin equipos". */
export async function listTeams(): Promise<Team[]> {
  return apiJson<Team[]>("/v1/teams");
}

/** Variante tolerante a 404 para consumidores que degradan a lista vacía
 * (barra lateral, autocompletado de menciones). */
export async function listTeamsTolerant(): Promise<Team[]> {
  try {
    return await apiJson<Team[]>("/v1/teams");
  } catch (err) {
    if (isNotFound(err)) return [];
    throw err;
  }
}

/** `POST /v1/teams` — crea un equipo. */
export async function createTeam(name: string): Promise<Team> {
  return apiJson<Team>("/v1/teams", { method: "POST", body: { name } });
}

/** `DELETE /v1/teams/{id}`. */
export async function deleteTeam(id: string): Promise<void> {
  await apiJson<void>(`/v1/teams/${id}`, { method: "DELETE" });
}

/** `POST /v1/teams/{id}/members` — agrega un miembro desde el roster. */
export async function addTeamMember(
  id: string,
  input: { agent_id: string; role: string },
): Promise<Team> {
  return apiJson<Team>(`/v1/teams/${id}/members`, { method: "POST", body: input });
}

/** `DELETE /v1/teams/{id}/members/{agent_id}`. */
export async function removeTeamMember(id: string, agentId: string): Promise<void> {
  await apiJson<void>(`/v1/teams/${id}/members/${encodeURIComponent(agentId)}`, {
    method: "DELETE",
  });
}

// --- Mensajes del equipo -----------------------------------------------------

/** `GET /v1/teams/{id}/messages`. Tolera 404 (backend aún no montado). */
export async function listTeamMessages(id: string): Promise<TeamMessage[]> {
  try {
    return await apiJson<TeamMessage[]>(`/v1/teams/${id}/messages`);
  } catch (err) {
    if (isNotFound(err)) return [];
    throw err;
  }
}

/** `POST /v1/teams/{id}/message` — envía y consume el stream SSE (respuesta del
 * asistente + eventos de delegación). Lanza `ApiError` si el stream falla. */
export async function sendTeamMessage(
  id: string,
  text: string,
  onEvent: (event: TeamStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const requestHeaders = new Headers();
  requestHeaders.set("Content-Type", "application/json");
  requestHeaders.set("Accept", "text/event-stream");
  const res = await authedFetch(`/v1/teams/${id}/message`, {
    method: "POST",
    headers: requestHeaders,
    body: JSON.stringify({ text }),
    signal,
  });
  if (!res.ok || !res.body) {
    const { message, detail } = await extractErrorMessage(res);
    throw new ApiError(res.status, message, detail);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  const parser = new SseDataParser();
  let streamFailure: Error | null = null;

  function emitPayloads(payloads: string[]) {
    for (const jsonText of payloads) {
      if (!jsonText.trim()) continue;
      try {
        const event = parseTeamEvent(JSON.parse(jsonText));
        if (event) {
          onEvent(event);
          if (event.type === "error") streamFailure = new Error(event.message);
        }
      } catch {
        // Frame SSE malformado: se ignora sin tumbar el resto del stream.
      }
    }
  }

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    emitPayloads(parser.push(decoder.decode(value, { stream: true })));
  }
  emitPayloads(parser.push(decoder.decode(), true));
  if (streamFailure) throw streamFailure;
}

// --- Reacciones e hilos (`/v1/messages/{id}/*`) ------------------------------

/** `POST /v1/messages/{id}/reactions` — agrega una reacción. */
export async function addMessageReaction(
  messageId: string,
  emoji: string,
): Promise<MessageReaction[]> {
  return apiJson<MessageReaction[]>(`/v1/messages/${messageId}/reactions`, {
    method: "POST",
    body: { emoji },
  });
}

/** `DELETE /v1/messages/{id}/reactions/{emoji}`. */
export async function removeMessageReaction(messageId: string, emoji: string): Promise<void> {
  await apiJson<void>(
    `/v1/messages/${messageId}/reactions/${encodeURIComponent(emoji)}`,
    { method: "DELETE" },
  );
}

/** `POST /v1/messages/{id}/thread` — responde en un hilo. */
export async function startMessageThread(messageId: string, text: string): Promise<ThreadReply> {
  return apiJson<ThreadReply>(`/v1/messages/${messageId}/thread`, { method: "POST", body: { text } });
}

/** `GET /v1/messages/{id}/thread` — lista las respuestas del hilo. */
export async function listMessageThread(messageId: string): Promise<ThreadReply[]> {
  return apiJson<ThreadReply[]>(`/v1/messages/${messageId}/thread`);
}

// Re-exporta `ApiError` para sus consumidores, mismo patrón de los demás
// vertical slices (api-automatizaciones, api-skills, etc.).
export { ApiError };