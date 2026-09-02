/**
 * Cliente HTTP de bots persistentes (modelo Grok Bot): sidebar de bots con chat
 * propio (`/v1/agents/workers`, `/message`, `/messages`) y chats 1:1 entre bots
 * (`/v1/agents/direct-chats`). Turnos reales vía SSE — sin ACK sintético.
 */

import type { AgentEvent } from "./types";
import {
  API_BASE_URL,
  ApiError,
  type PersistentWorker,
  type WorkerCreateInput,
  type WorkerPatchInput,
} from "./api";
import { recoverSessionAfterUnauthorized, isRefreshResultCurrent } from "./session-refresh";
import { getAccessToken, hasSession } from "./tokens";
import { parseAgentEvent } from "./chat-blocks";
import { SseDataParser } from "./sse";

export interface BotMessage {
  id: string;
  role: string;
  text: string;
  sender_id?: string | null;
  sender_name?: string | null;
  created_at?: string | null;
}

export interface DirectChat {
  id: string;
  agent_a_id: string;
  agent_b_id: string;
  conversation_id?: string | null;
  agent_a_name?: string | null;
  agent_a_display?: string | null;
  agent_b_name?: string | null;
  agent_b_display?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export type { PersistentWorker, WorkerCreateInput, WorkerPatchInput };
export { ApiError };

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

async function apiJson<T>(
  path: string,
  // `body` se excluye de RequestInit a propósito: esta función acepta objetos
  // serializables y los stringify ella misma (RequestInit.body solo admite
  // BodyInit, y la intersección de tipos rompía a todos los callers).
  init: Omit<RequestInit, "body"> & { body?: unknown } = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  let body: BodyInit | undefined;
  if (init.body !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(init.body);
  }
  const res = await authedFetch(path, { ...init, headers, body });
  return parseJsonOrThrow<T>(res);
}

async function streamSse(
  path: string,
  body: unknown,
  onEvent: (event: AgentEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const requestHeaders = new Headers();
  requestHeaders.set("Content-Type", "application/json");
  requestHeaders.set("Accept", "text/event-stream");
  const res = await authedFetch(path, {
    method: "POST",
    headers: requestHeaders,
    body: JSON.stringify(body),
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
        const event = parseAgentEvent(JSON.parse(jsonText));
        if (event) {
          onEvent(event);
          if (event.type === "error") streamFailure = new Error(event.message);
        }
      } catch {
        // Frame malformado: ignorar.
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

export async function listBots(): Promise<PersistentWorker[]> {
  return apiJson<PersistentWorker[]>("/v1/agents/workers");
}

export async function createBot(input: WorkerCreateInput): Promise<PersistentWorker> {
  return apiJson<PersistentWorker>("/v1/agents/workers", { method: "POST", body: input });
}

export async function listBotMessages(botId: string): Promise<BotMessage[]> {
  return apiJson<BotMessage[]>(`/v1/agents/workers/${botId}/messages`);
}

export function sendBotMessage(
  botId: string,
  text: string,
  onEvent: (event: AgentEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  return streamSse(`/v1/agents/workers/${botId}/message`, { text }, onEvent, signal);
}

export async function listDirectChats(): Promise<DirectChat[]> {
  return apiJson<DirectChat[]>("/v1/agents/direct-chats");
}

export async function createDirectChat(agentAId: string, agentBId: string): Promise<DirectChat> {
  return apiJson<DirectChat>("/v1/agents/direct-chats", {
    method: "POST",
    body: { agent_a_id: agentAId, agent_b_id: agentBId },
  });
}

export async function listDirectChatMessages(chatId: string): Promise<BotMessage[]> {
  return apiJson<BotMessage[]>(`/v1/agents/direct-chats/${chatId}/messages`);
}

export function sendDirectChatMessage(
  chatId: string,
  text: string,
  speaker: string,
  onEvent: (event: AgentEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  return streamSse(
    `/v1/agents/direct-chats/${chatId}/message`,
    { text, speaker },
    onEvent,
    signal,
  );
}
