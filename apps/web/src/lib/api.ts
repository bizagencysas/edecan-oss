/**
 * Cliente HTTP de `edecan_api` (ARCHITECTURE.md §10.12, §10.14).
 *
 * Base URL: `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`). Todas
 * las rutas protegidas usan `Authorization: Bearer <access_token>`; si una
 * respuesta llega en 401 se intenta refrescar una sola vez con
 * `POST /v1/auth/refresh` (dedupe global entre todos los clientes API)
 * y se reintenta la petición original antes de rendirse y mandar al usuario
 * a `/login`. Si la cuenta tiene 2FA activo, `/v1/auth/refresh` exige
 * `totp_code` igual que `/login` (auth.py) — ese caso puntual se distingue
 * por el `detail` del 401 y se resuelve pidiéndole el código al usuario con
 * un prompt liviano en vez de cerrarle la sesión de una (ver
 * `tryRefreshWithTotpPrompt`, HOTFIXES_PENDIENTES.md #2).
 *
 * Las formas de request/response siguen el código real de
 * `apps/api/edecan_api/routers/*.py` (fuente de verdad), no siempre iguales
 * a `docs/api.md`.
 */

import { recoverSessionAfterUnauthorized, isRefreshResultCurrent } from "./session-refresh";
import { createSingleFlight } from "./single-flight";
import {
  clearTokens,
  getAccessToken,
  getDesktopCapability,
  getRefreshToken,
  hasSession,
  setTokens,
} from "./tokens";
import { isPublicAuthRoute } from "./auth-route-policy";
import { buildChatMessageInput } from "./chat-attachments";
import { parseAgentEvent } from "./chat-blocks";
import { SseDataParser } from "./sse";
import type { DevicePairingOut } from "./device-pairing";
import type { StudioActionInput, StudioActionResponse } from "./studio";
import type {
  AgentEvent,
  ChatModelCatalog,
  Contact,
  ContactsImportResult,
  ConnectorListItem,
  ConsentOut,
  ConversationOut,
  FileOut,
  FinanceSummary,
  ICloudContactsImportResult,
  ICloudStatus,
  MeOut,
  MemoryImportItem,
  MemoryItem,
  MessageOut,
  PersonaConfig,
  PhoneAgentTemplate,
  PhoneAgentTemplateBundle,
  PhoneAgentTemplateImportResult,
  PhoneAgentTemplateInput,
  PhoneCall,
  PushCredentialsInput,
  PushPreferences,
  PushStatus,
  Reminder,
  StripeStatus,
  StripeSyncResult,
  TokenPair,
  Transaction,
  UsageOut,
} from "./types";

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/+$/, "") ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
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

// ---------------------------------------------------------------------------
// Bajo nivel: fetch autenticado con refresh-on-401 deduplicado
// ---------------------------------------------------------------------------

// `/v1/auth/refresh` exige el mismo gate de TOTP que `/login` cuando la
// cuenta tiene 2FA activo (auth.py::refresh, ~L202-207 — intencional, ver el
// comentario del propio router). El texto del `detail` es lo único que
// distingue esa causa puntual de un refresh token genuinamente inválido o
// expirado (los otros 401 posibles acá), así que se compara literal contra
// el mismo mensaje que usa el backend.
async function rawFetch(path: string, init: RequestInit, skipAuth: boolean): Promise<Response> {
  const headers = new Headers(init.headers);
  if (!skipAuth) {
    const token = getAccessToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
  }
  return fetch(`${API_BASE_URL}${path}`, { ...init, headers });
}

function redirectToLogin(): void {
  if (typeof window === "undefined" || hasSession()) return;
  if (window.location.pathname !== "/login") {
    window.location.assign("/login/");
  }
}

/**
 * Fetch con Bearer automático + un reintento tras refrescar el token en 401.
 * Si el refresh falla puntualmente por el gate de TOTP, primero intenta
 * resolverlo con `tryRefreshWithTotpPrompt` antes de rendirse.
 */
async function authedFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const skipAuth = isPublicAuthRoute(path);
  let res = await rawFetch(path, init, skipAuth);
  if (res.status === 401 && !skipAuth) {
    const result = await recoverSessionAfterUnauthorized(API_BASE_URL);
    if (isRefreshResultCurrent(result)) {
      res = await rawFetch(path, init, skipAuth);
    } else if (!result.ok && result.reason === "invalid") {
      redirectToLogin();
    }
  }
  return res;
}

async function parseJsonOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const { message, detail } = await extractErrorMessage(res);
    throw new ApiError(res.status, message, detail);
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  if (!text) return undefined as T;
  return JSON.parse(text) as T;
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

// ---------------------------------------------------------------------------
// Auth (§10.12) — estas rutas no llevan Bearer propio
// ---------------------------------------------------------------------------

const runLocalDesktopSession = createSingleFlight<TokenPair>();

export async function openLocalDesktopSession(): Promise<TokenPair> {
  return runLocalDesktopSession(async () => {
    const capability = getDesktopCapability();
    if (!capability) {
      throw new Error("No se pudo verificar esta aplicación de Edecán.");
    }
    const pair = await apiJson<TokenPair>("/v1/auth/local", {
      method: "POST",
      headers: { "X-Edecan-Desktop-Capability": capability },
    });
    setTokens(pair.access_token, pair.refresh_token);
    return pair;
  });
}

export async function register(email: string, password: string, tenantName: string): Promise<TokenPair> {
  const pair = await apiJson<TokenPair>("/v1/auth/register", {
    method: "POST",
    body: { email, password, tenant_name: tenantName },
  });
  setTokens(pair.access_token, pair.refresh_token);
  return pair;
}

export async function login(email: string, password: string, totpCode?: string): Promise<TokenPair> {
  const pair = await apiJson<TokenPair>("/v1/auth/login", {
    method: "POST",
    body: { email, password, totp_code: totpCode || undefined },
  });
  setTokens(pair.access_token, pair.refresh_token);
  return pair;
}

export function logout(): void {
  const refreshToken = getRefreshToken();
  clearTokens();
  if (!refreshToken) return;
  // El cierre local es inmediato; la revocación remota es best-effort para
  // que una caída de red nunca deje la UI atrapada en una sesión aparente.
  void rawFetch(
    "/v1/auth/logout",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    },
    true,
  ).catch(() => undefined);
}

export async function enableTotp(): Promise<{ secret: string; provisioning_uri: string }> {
  return apiJson("/v1/auth/totp/enable", { method: "POST" });
}

export async function verifyTotp(code: string): Promise<{ verified: boolean }> {
  return apiJson("/v1/auth/totp/verify", { method: "POST", body: { code } });
}

/**
 * `POST /v1/auth/totp/disable` — única ruta de recuperación para una cuenta
 * con 2FA activado que perdió su dispositivo/app autenticadora (re-exige la
 * CONTRASEÑA, no un código TOTP; ver docstring del router en
 * `apps/api/edecan_api/routers/auth.py`).
 */
export async function disableTotp(password: string): Promise<{ disabled: boolean }> {
  return apiJson("/v1/auth/totp/disable", { method: "POST", body: { password } });
}

// --- Perfil ----------------------------------------------------------------

export async function getMe(): Promise<MeOut> {
  return apiJson<MeOut>("/v1/me");
}

// --- Persona "nivel Dios" ----------------------------------------------------

export async function getPersona(): Promise<PersonaConfig> {
  return apiJson<PersonaConfig>("/v1/persona");
}

export async function updatePersona(patch: Partial<PersonaConfig>): Promise<PersonaConfig> {
  return apiJson<PersonaConfig>("/v1/persona", { method: "PUT", body: patch });
}

export async function previewPersona(): Promise<{ system_prompt: string }> {
  return apiJson("/v1/persona/preview");
}

// --- Conversaciones ----------------------------------------------------------

export async function listConversations(): Promise<ConversationOut[]> {
  return apiJson<ConversationOut[]>("/v1/conversations");
}

export async function createConversation(title?: string): Promise<ConversationOut> {
  return apiJson<ConversationOut>("/v1/conversations", { method: "POST", body: { title } });
}

export async function getConversation(id: string): Promise<ConversationOut> {
  return apiJson<ConversationOut>(`/v1/conversations/${id}`);
}

export async function renameConversation(id: string, title: string): Promise<ConversationOut> {
  return apiJson<ConversationOut>(`/v1/conversations/${id}`, {
    method: "PATCH",
    body: { title },
  });
}

export async function deleteConversation(id: string): Promise<void> {
  await apiJson<void>(`/v1/conversations/${id}`, { method: "DELETE" });
}

export async function clearConversationContext(id: string): Promise<ConversationOut> {
  return apiJson<ConversationOut>(`/v1/conversations/${id}/clear`, { method: "POST" });
}

export async function branchConversation(
  id: string,
  opts: { after_message_id?: string; until_message_id?: string } = {},
): Promise<ConversationOut> {
  return apiJson<ConversationOut>(`/v1/conversations/${id}/branch`, {
    method: "POST",
    body: opts,
  });
}

export async function rewindConversation(id: string): Promise<ConversationOut> {
  return apiJson<ConversationOut>(`/v1/conversations/${id}/rewind`, { method: "POST" });
}

export async function setMessageFlags(
  conversationId: string,
  messageId: string,
  flags: { pinned?: boolean; bookmark?: boolean },
): Promise<MessageOut> {
  return apiJson<MessageOut>(`/v1/conversations/${conversationId}/messages/${messageId}/flags`, {
    method: "POST",
    body: flags,
  });
}

export interface HostDevice {
  id: string;
  nombre: string;
  plataforma: string;
  kind: string;
  status: string;
  last_seen_at: string | null;
}

export async function listDevices(): Promise<HostDevice[]> {
  return apiJson<HostDevice[]>("/v1/devices");
}

export type AutonomyLevel = "ask" | "read_only" | "draft" | "full";

export interface WorkerAvatarEye {
  x?: number;
  y?: number;
  rx?: number;
  ry?: number;
  rotation?: number;
}

export interface WorkerAvatarEyes {
  style?: string | null;
  color?: string | null;
  left?: WorkerAvatarEye | null;
  right?: WorkerAvatarEye | null;
}

/** Dict JSONB que acompaña al worker (`avatar` en `persistent_agents`). */
export interface WorkerAvatar {
  initials?: string | null;
  accent?: string | null;
  fill?: string | null;
  shape?: string | null;
  image_url?: string | null;
  style?: string | null;
  seed?: string | null;
  eyes?: WorkerAvatarEyes | null;
}

export interface PersistentWorker {
  id: string;
  name: string;
  purpose: string;
  status: string;
  enabled: boolean;
  workspace: string | null;
  display_name: string | null;
  avatar: WorkerAvatar | null;
  role_title: string | null;
  role_short: string | null;
  job_description: string | null;
  personality: string | null;
  communication_style: string | null;
  instructions: string | null;
  constraints: string | null;
  approval_policy: Record<string, unknown> | null;
  autonomy_level: AutonomyLevel | null;
  relation: string | null;
  model_policy: Record<string, unknown> | null;
  /** Último checkpoint durado del worker (`run_persistent_agent._save_checkpoint`):
   * `{ task_id, instruction_hash, status, started_at?, finished_at?, result?, error? }`.
   * `null` si todavía no corrió ninguna tarea. */
  last_checkpoint?: Record<string, unknown> | null;
  updated_at: string;
}

export interface WorkerCreateInput {
  name: string;
  purpose: string;
  display_name?: string | null;
  avatar?: WorkerAvatar;
  role_title?: string | null;
  role_short?: string | null;
  job_description?: string | null;
  personality?: string | null;
  communication_style?: string | null;
  instructions?: string | null;
  constraints?: string | null;
  approval_policy?: Record<string, unknown>;
  autonomy_level?: AutonomyLevel;
  relation?: string | null;
  model_policy?: Record<string, unknown>;
}

export interface WorkerPatchInput {
  enabled?: boolean;
  status?: string;
  display_name?: string | null;
  avatar?: WorkerAvatar;
  role_title?: string | null;
  role_short?: string | null;
  job_description?: string | null;
  personality?: string | null;
  communication_style?: string | null;
  instructions?: string | null;
  constraints?: string | null;
  approval_policy?: Record<string, unknown>;
  autonomy_level?: AutonomyLevel;
  relation?: string | null;
  model_policy?: Record<string, unknown>;
}

export interface WorkerHandoff {
  id: string;
  destination_worker_id: string;
  destination_name?: string;
  task_id: string;
  envelope: { instruction?: string } | string | null;
  status: string;
  created_at: string;
}

export async function listWorkers(): Promise<PersistentWorker[]> {
  return apiJson<PersistentWorker[]>("/v1/agents/workers");
}

export async function createWorker(input: WorkerCreateInput): Promise<PersistentWorker> {
  return apiJson<PersistentWorker>("/v1/agents/workers", { method: "POST", body: input });
}

export async function patchWorker(id: string, input: WorkerPatchInput): Promise<PersistentWorker> {
  return apiJson<PersistentWorker>(`/v1/agents/workers/${id}`, { method: "PATCH", body: input });
}

export async function enqueueWorkerTask(id: string, instruction: string): Promise<{ task_id: string }> {
  return apiJson<{ task_id: string }>(`/v1/agents/workers/${id}/tasks`, {
    method: "POST",
    body: { instruction },
  });
}

export async function listWorkerHandoffs(): Promise<WorkerHandoff[]> {
  return apiJson<WorkerHandoff[]>("/v1/agents/workers/handoffs");
}

export async function approveWorkerHandoff(id: string): Promise<unknown> {
  return apiJson(`/v1/agents/workers/handoffs/${id}/approve`, { method: "POST" });
}

// --- Selector de modelos del chat --------------------------------------------

export type { ChatModelCatalog, ChatModelInfo } from "./types";

/**
 * `GET /v1/models/chat` — catálogo único del selector (routers/models.py).
 * Es la ÚNICA fuente de la lista: la web, la app de escritorio y iOS leen de
 * aquí para que agregar un modelo sea editar `config/modelos.yml` y nada más.
 */
export async function getChatModels(): Promise<ChatModelCatalog> {
  return apiJson<ChatModelCatalog>("/v1/models/chat");
}

/**
 * `PUT /v1/conversations/{id}/model` — fija modelo y esfuerzo de ESA
 * conversación (no de la credencial del tenant: `PATCH
 * /v1/credentials/llm/models` sigue en 409 a propósito). Las dos claves se
 * mandan SIEMPRE porque el backend escribe ambas; `model: null` vuelve a
 * automático. Devuelve el estado tal cual quedó persistido, que es lo que la
 * UI debe adoptar en vez de confiar en su optimista.
 */
export async function setConversationModel(
  conversationId: string,
  model: string | null,
  effort: string | null,
): Promise<{ model: string | null; effort: string | null }> {
  return apiJson<{ model: string | null; effort: string | null }>(
    `/v1/conversations/${conversationId}/model`,
    { method: "PUT", body: { model, effort } },
  );
}

// --- Chat en streaming (SSE, §10.7) -------------------------------------------

async function streamSse(
  path: string,
  body: unknown,
  onEvent: (event: AgentEvent) => void,
  signal?: AbortSignal,
  headers?: HeadersInit,
): Promise<void> {
  const requestHeaders = new Headers(headers);
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

/** `POST /v1/conversations/{id}/messages` — arranca un turno del agente. */
export function sendMessageStream(
  conversationId: string,
  text: string,
  onEvent: (event: AgentEvent) => void,
  signal?: AbortSignal,
  attachments: string[] = [],
  idempotencyKey?: string,
): Promise<void> {
  return streamSse(
    `/v1/conversations/${conversationId}/messages`,
    buildChatMessageInput(text, attachments),
    onEvent,
    signal,
    idempotencyKey ? { "Idempotency-Key": idempotencyKey } : undefined,
  );
}

/** Encola un mensaje mientras el turno activo sigue en el servidor (`202`). */
export async function queueChatMessage(
  conversationId: string,
  text: string,
  attachments: string[] = [],
  idempotencyKey?: string,
): Promise<{ status: string; position: number; pending?: number }> {
  const headers = new Headers({ Accept: "application/json" });
  if (idempotencyKey) headers.set("Idempotency-Key", idempotencyKey);
  const res = await authedFetch(`/v1/conversations/${conversationId}/messages`, {
    method: "POST",
    headers,
    body: JSON.stringify(buildChatMessageInput(text, attachments)),
  });
  if (res.status === 202) {
    return res.json() as Promise<{ status: string; position: number; pending?: number }>;
  }
  const { message, detail } = await extractErrorMessage(res);
  throw new ApiError(res.status, message, detail);
}

/** `POST /v1/conversations/{id}/confirm` — aprueba/rechaza una tool pendiente. */
export function confirmToolCallStream(
  conversationId: string,
  toolCallId: string,
  approved: boolean,
  onEvent: (event: AgentEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  return streamSse(
    `/v1/conversations/${conversationId}/confirm`,
    { tool_call_id: toolCallId, approved },
    onEvent,
    signal,
  );
}

// --- Memoria -----------------------------------------------------------------

export async function listMemory(q?: string, k?: number, namespace?: string): Promise<MemoryItem[]> {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (k) params.set("k", String(k));
  if (namespace) params.set("namespace", namespace);
  const qs = params.toString();
  return apiJson<MemoryItem[]>(`/v1/memory${qs ? `?${qs}` : ""}`);
}

/**
 * Entrada de la memoria de un worker persistente (`GET /v1/memory?namespace=
 * agent:<id>`). A diferencia del espacio plano `user`, el backend devuelve pares
 * `key`/`value` del dict JSONB `persistent_agents.memory` — no hay `id`, `kind`
 * ni `source_trust`, y por eso tampoco hay un `DELETE /v1/memory/{id}` que la
 * borre (ese endpoint apunta a la tabla `memory_items` por uuid).
 */
export interface AgentMemoryEntry {
  key: string;
  value: unknown;
  namespace: string;
}

export async function listAgentMemory(agentId: string): Promise<AgentMemoryEntry[]> {
  return apiJson<AgentMemoryEntry[]>(
    `/v1/memory?namespace=${encodeURIComponent(`agent:${agentId}`)}`,
  );
}

export async function addMemory(input: {
  kind?: string;
  content: string;
  importance?: number;
  source?: string;
  namespace?: string;
  source_trust?: string;
}): Promise<MemoryItem> {
  return apiJson<MemoryItem>("/v1/memory", { method: "POST", body: input });
}

/**
 * Sugerencia de memoria propuesta por el agente (`GET /v1/memory/suggestions`):
 * el backend observa hábitos y propone guardarlos. Nada se persiste hasta que
 * el usuario elige "Guardar" (que pasa por `addMemory`, arriba).
 */
export interface MemorySuggestion {
  text: string;
  source?: string | null;
  scope?: string | null;
  confidence?: number | null;
}

export async function listMemorySuggestions(): Promise<MemorySuggestion[]> {
  return apiJson<MemorySuggestion[]>("/v1/memory/suggestions");
}

export async function deleteMemory(id: string): Promise<void> {
  await apiJson<void>(`/v1/memory/${id}`, { method: "DELETE" });
}

export async function previewImportMemoria(texto: string): Promise<MemoryImportItem[]> {
  return apiJson<MemoryImportItem[]>("/v1/memory/import/preview", { method: "POST", body: { texto } });
}

export async function confirmImportMemoria(items: MemoryImportItem[]): Promise<MemoryItem[]> {
  return apiJson<MemoryItem[]>("/v1/memory/import/confirm", { method: "POST", body: { items } });
}

// --- Privacidad -------------------------------------------------------------

export interface PrivacyCenterStatus {
  version: string;
  controls: {
    export: { available: boolean; method: string; path: string };
    erase_memory: { available: boolean; method: string; path: string };
    erase_account: { available: boolean; status: string };
  };
}

export interface AccountDeletionPreflight {
  format: "edecan-account-deletion-preflight.v1";
  ready: boolean;
  blockers: Array<{ code: string; message: string }>;
  mutated: false;
}

export async function getPrivacyCenter(): Promise<PrivacyCenterStatus> {
  return apiJson<PrivacyCenterStatus>("/v1/privacy");
}

export async function getAccountDeletionPreflight(): Promise<AccountDeletionPreflight> {
  return apiJson<AccountDeletionPreflight>("/v1/privacy/account/preflight");
}

export async function exportMyData(): Promise<Record<string, unknown>> {
  return apiJson<Record<string, unknown>>("/v1/privacy/export");
}

export async function eraseAllMemory(): Promise<{ deleted: number }> {
  return apiJson<{ deleted: number }>("/v1/memory", { method: "DELETE" });
}

export interface ProviderHealthSnapshot {
  available: boolean;
  error_rate: number;
  avg_latency: number;
  total_calls: number;
  total_successes: number;
  total_failures: number;
  consecutive_failures: number;
}

export interface ProviderHealthEvent {
  provider: string;
  status: "success" | "failure" | "rate_limited" | string;
  latency: number;
  at: number;
}

export interface ProviderHealthResponse {
  format: "edecan-provider-health.v1";
  status: "ok" | "degraded" | string;
  providers: Record<string, ProviderHealthSnapshot>;
  recent_events: ProviderHealthEvent[];
}

export async function getProviderHealth(): Promise<ProviderHealthResponse> {
  return apiJson<ProviderHealthResponse>("/v1/health/providers");
}

export async function submitFeedback(input: {
  kind: "thumb_up" | "thumb_down" | "correction" | "report_problem";
  category?: string;
  detail?: string;
  conversation_id?: string;
  message_id?: string;
}): Promise<{ accepted: boolean; feedback_ref: string }> {
  return apiJson<{ accepted: boolean; feedback_ref: string }>("/v1/feedback", {
    method: "POST",
    body: input,
  });
}

export async function undoLastAction(): Promise<{ tool_name: string; inverse_op: unknown }> {
  return apiJson<{ tool_name: string; inverse_op: unknown }>("/v1/me/actions/undo", {
    method: "POST",
  });
}

export async function deleteMyAccount(input: {
  password: string;
  confirmation: string;
  totp_code?: string;
}): Promise<{ deleted: boolean }> {
  return apiJson<{ deleted: boolean }>("/v1/privacy/account", {
    method: "DELETE",
    body: input,
  });
}

// --- Conectores (§10.8) --------------------------------------------------------

export async function listConnectors(): Promise<ConnectorListItem[]> {
  return apiJson<ConnectorListItem[]>("/v1/connectors");
}

export async function getConnectorAuthorizeUrl(key: string): Promise<{ url: string }> {
  return apiJson<{ url: string }>(`/v1/connectors/${key}/authorize`);
}

/**
 * `PUT /v1/connectors/{key}/app-credentials` — la app OAuth PROPIA del
 * tenant (client_id/client_secret que registró en la consola del
 * proveedor), requisito previo a `getConnectorAuthorizeUrl` (§10.2: nunca
 * hay una app compartida de la plataforma).
 */
export async function putConnectorAppCredentials(
  key: string,
  input: { client_id: string; client_secret?: string },
): Promise<void> {
  await apiJson<void>(`/v1/connectors/${key}/app-credentials`, { method: "PUT", body: input });
}

export async function deleteConnectorAppCredentials(key: string): Promise<void> {
  await apiJson<void>(`/v1/connectors/${key}/app-credentials`, { method: "DELETE" });
}

export async function disconnectConnector(key: string, accountId: string): Promise<void> {
  await apiJson<void>(`/v1/connectors/${key}/${accountId}`, { method: "DELETE" });
}

/** `PUT /v1/connectors/twilio/credentials` — Twilio no es OAuth (Account SID + Auth Token). */
export async function connectTwilioCredentials(input: {
  account_sid: string;
  auth_token: string;
  phone_number: string;
}): Promise<void> {
  await apiJson<void>("/v1/connectors/twilio/credentials", { method: "PUT", body: input });
}

/**
 * `PUT /v1/connectors/{key}/credentials` — Telegram/Discord no son OAuth
 * (`key` ∈ `"telegram" | "discord"`, ver `BOT_TOKEN_CONNECTOR_KEYS` en
 * `edecan_api/routers/connectors.py`): cada tenant pega el token de su propio
 * bot.
 */
export async function connectBotTokenCredentials(key: string, input: { bot_token: string }): Promise<void> {
  await apiJson<void>(`/v1/connectors/${key}/credentials`, { method: "PUT", body: input });
}

/**
 * `PUT /v1/connectors/whatsapp/credentials` — WhatsApp Business Platform
 * tampoco es OAuth (access token permanente de la app de Meta del tenant +
 * `phone_number_id` de su número ya verificado).
 */
export async function connectWhatsappCredentials(input: {
  access_token: string;
  phone_number_id: string;
}): Promise<void> {
  await apiJson<void>("/v1/connectors/whatsapp/credentials", { method: "PUT", body: input });
}

// --- Consentimientos de telefonía OSS ----------------------------------------

/**
 * `POST /v1/consents` registra evidencia verificable en el OSS. Sin una fila
 * vigente, el asistente bloquea cualquier llamada saliente a ese número.
 */
export async function grantConsent(input: {
  phone_e164: string;
  kind: "sms" | "voice";
  source: string;
}): Promise<ConsentOut> {
  return apiJson<ConsentOut>("/v1/consents", { method: "POST", body: input });
}

/** Personas reutilizables para las llamadas salientes del asistente. */
export async function listPhoneAgentTemplates(): Promise<PhoneAgentTemplate[]> {
  return apiJson<PhoneAgentTemplate[]>("/v1/phone/agent-templates");
}

export async function createPhoneAgentTemplate(
  input: PhoneAgentTemplateInput,
): Promise<PhoneAgentTemplate> {
  return apiJson<PhoneAgentTemplate>("/v1/phone/agent-templates", {
    method: "POST",
    body: input,
  });
}

export async function updatePhoneAgentTemplate(
  id: string,
  input: PhoneAgentTemplateInput,
): Promise<PhoneAgentTemplate> {
  return apiJson<PhoneAgentTemplate>(`/v1/phone/agent-templates/${id}`, {
    method: "PUT",
    body: input,
  });
}

export async function deletePhoneAgentTemplate(id: string): Promise<void> {
  await apiJson<void>(`/v1/phone/agent-templates/${id}`, { method: "DELETE" });
}

export async function exportPhoneAgentTemplates(): Promise<PhoneAgentTemplateBundle> {
  return apiJson<PhoneAgentTemplateBundle>("/v1/phone/agent-templates/export");
}

export async function importPhoneAgentTemplates(
  bundle: PhoneAgentTemplateBundle,
): Promise<PhoneAgentTemplateImportResult> {
  return apiJson<PhoneAgentTemplateImportResult>("/v1/phone/agent-templates/import", {
    method: "POST",
    body: bundle,
  });
}

/** Llamadas del asistente, incluidas entrantes, salientes y borradores por confirmar. */
export async function listPhoneCalls(): Promise<PhoneCall[]> {
  return apiJson<PhoneCall[]>("/v1/phone/calls");
}

// --- Push nativo BYO (APNs/FCM) ---------------------------------------------

export async function getPushStatus(): Promise<PushStatus> {
  return apiJson<PushStatus>("/v1/devices/push/status");
}

export async function getPushPreferences(): Promise<PushPreferences> {
  return apiJson<PushPreferences>("/v1/devices/push/preferences");
}

export async function updatePushPreferences(
  patch: Partial<PushPreferences>,
): Promise<PushPreferences> {
  return apiJson<PushPreferences>("/v1/devices/push/preferences", {
    method: "PUT",
    body: patch,
  });
}

export async function savePushCredentials(input: PushCredentialsInput): Promise<void> {
  await apiJson<void>("/v1/devices/push/credentials", {
    method: "PUT",
    body: input,
  });
}

export async function disconnectPushCredentials(): Promise<void> {
  await apiJson<void>("/v1/devices/push/credentials", { method: "DELETE" });
}

export async function setupIncomingCalls(): Promise<{
  status: "ready";
  phone_number: string;
  phone_numbers?: string[];
  agent_name: string;
  agent_template_name: string;
}> {
  return apiJson("/v1/phone/incoming/setup", { method: "POST" });
}

/** Prepara una llamada y devuelve el borrador verificable. Nunca llama por sí sola. */
export async function preparePhoneCall(input: {
  to_e164: string;
  recipient_name: string;
  goal?: string;
  agent_template_id?: string;
  conversation_id?: string;
}): Promise<PhoneCall> {
  return apiJson<PhoneCall>("/v1/phone/calls/prepare", {
    method: "POST",
    body: input,
  });
}

export async function confirmPhoneCall(call: PhoneCall): Promise<PhoneCall> {
  if (!call.recipient_name || !call.agent?.template_id) {
    throw new Error(
      "La llamada no tiene una persona y un agente verificables. Prepárala de nuevo.",
    );
  }
  return apiJson<PhoneCall>(`/v1/phone/calls/${call.id}/confirm`, {
    method: "POST",
    body: {
      expected_to_e164: call.to_e164,
      expected_recipient_name: call.recipient_name,
      expected_goal: call.goal,
      expected_agent_template_id: call.agent?.template_id,
      confirmed_destination: true,
      confirmed_recipient: true,
      confirmed_goal: true,
      confirmed_agent: true,
    },
  });
}

export async function cancelPhoneCall(id: string): Promise<PhoneCall> {
  return apiJson<PhoneCall>(`/v1/phone/calls/${id}`, { method: "DELETE" });
}

export type LlamadaActiva = {
  activa: boolean;
  call_id?: string;
  sid?: string;
  numero?: string;
  direccion?: "in" | "out";
  status?: string;
  transcript?: Array<{ quien: "agente" | "cliente"; texto: string; ts?: number | string }>;
  call?: PhoneCall;
};

export async function getLlamadaActiva(): Promise<LlamadaActiva> {
  return apiJson<LlamadaActiva>("/v1/phone/calls/activa");
}

export async function susurrarEnLlamada(callId: string, texto: string): Promise<void> {
  await apiJson(`/v1/phone/calls/${callId}/susurro`, {
    method: "POST",
    body: { text: texto },
  });
}

// --- Archivos ------------------------------------------------------------------

export async function listFiles(): Promise<FileOut[]> {
  return apiJson<FileOut[]>("/v1/files");
}

export async function getFile(id: string): Promise<FileOut> {
  return apiJson<FileOut>(`/v1/files/${id}`);
}

export async function downloadFile(id: string): Promise<Blob> {
  const res = await authedFetch(`/v1/files/${encodeURIComponent(id)}/download`);
  if (!res.ok) {
    const { message, detail } = await extractErrorMessage(res);
    throw new ApiError(res.status, message, detail);
  }
  return res.blob();
}

export async function uploadFile(file: File, signal?: AbortSignal): Promise<FileOut> {
  const formData = new FormData();
  formData.append("file", file);
  const res = await authedFetch("/v1/files", { method: "POST", body: formData, signal });
  return parseJsonOrThrow<FileOut>(res);
}

// --- Studio visual -------------------------------------------------------------

/** Contrato autenticado y tenant-isolated del editor visual avanzado. */
export async function runStudioAction(input: StudioActionInput): Promise<StudioActionResponse> {
  return apiJson<StudioActionResponse>("/v1/content/studio/actions", {
    method: "POST",
    body: input,
  });
}

export interface SocialContentArtifact {
  file_id: string;
  filename: string;
  mime: string | null;
}

export interface SocialContentResult {
  status: "ready";
  platform: "linkedin" | "x";
  copy: string;
  parts: string[];
  alt_text: string;
  offline_visual: boolean;
  visual_warning: string;
  sources: Array<{ title: string; url: string; snippet: string }>;
  artifacts: SocialContentArtifact[];
  requires_human_confirmation: boolean;
}

export interface SocialEditorialProfile {
  platform: "linkedin" | "x";
  configured: boolean;
  version: number;
  purpose: string;
  audience: string;
  voice: string;
  content_pillars: string[];
  preferred_formats: string[];
  visual_identity: string;
  image_rules: string;
  calls_to_action: string;
  avoid: string;
  notes: string;
}

export async function getSocialEditorialProfile(
  platform: "linkedin" | "x" = "linkedin",
): Promise<SocialEditorialProfile> {
  return apiJson<SocialEditorialProfile>(
    `/v1/content/social/profile?platform=${encodeURIComponent(platform)}`,
  );
}

export async function updateSocialEditorialProfile(
  platform: "linkedin" | "x",
  input: Omit<SocialEditorialProfile, "platform" | "configured" | "version">,
): Promise<SocialEditorialProfile> {
  return apiJson<SocialEditorialProfile>(
    `/v1/content/social/profile?platform=${encodeURIComponent(platform)}`,
    { method: "PUT", body: input },
  );
}

export async function createSocialContent(input: {
  platform: "linkedin" | "x";
  topic: string;
  objective: string;
  tone: string;
  with_image: boolean;
}): Promise<SocialContentResult> {
  return apiJson<SocialContentResult>("/v1/content/social", {
    method: "POST",
    body: input,
  });
}

export async function publishLinkedInContent(input: {
  text: string;
  image_file_id?: string;
  alt_text?: string;
  confirmed: true;
}): Promise<{
  status: "published";
  platform: "linkedin";
  provider_id: string | null;
  /** Si el post se pudo RELEER en LinkedIn después de crearlo. Un `2xx` no prueba
   *  que exista: con un token sin permiso de organización, LinkedIn responde 201
   *  con un id válido y no crea nada. Ver `verify_post` en
   *  `packages/connectors/edecan_connectors/social/linkedin.py`.
   *  El tercer caso del backend (`not_found`) nunca llega acá: se convierte en error. */
  verified?: "confirmed" | "unknown";
  verification_note?: string;
}> {
  return apiJson("/v1/content/social/publish", {
    method: "POST",
    body: { platform: "linkedin", ...input },
  });
}

// --- Recordatorios ---------------------------------------------------------------

export async function listReminders(): Promise<Reminder[]> {
  return apiJson<Reminder[]>("/v1/reminders");
}

export async function createReminder(input: {
  due_at: string;
  message: string;
  rrule?: string | null;
  channel?: string;
}): Promise<Reminder> {
  return apiJson<Reminder>("/v1/reminders", { method: "POST", body: input });
}

export async function updateReminder(id: string, patch: Partial<Reminder>): Promise<Reminder> {
  return apiJson<Reminder>(`/v1/reminders/${id}`, { method: "PUT", body: patch });
}

export async function deleteReminder(id: string): Promise<void> {
  await apiJson<void>(`/v1/reminders/${id}`, { method: "DELETE" });
}

// --- Contactos ---------------------------------------------------------------------

export async function listContacts(q?: string): Promise<Contact[]> {
  const qs = q ? `?q=${encodeURIComponent(q)}` : "";
  return apiJson<Contact[]>(`/v1/contacts${qs}`);
}

export async function createContact(input: {
  nombre: string;
  emails?: string[];
  phones?: string[];
  empresa?: string | null;
  notas?: string | null;
  tags?: string[];
}): Promise<Contact> {
  return apiJson<Contact>("/v1/contacts", { method: "POST", body: input });
}

export async function updateContact(id: string, patch: Partial<Contact>): Promise<Contact> {
  return apiJson<Contact>(`/v1/contacts/${id}`, { method: "PUT", body: patch });
}

export async function deleteContact(id: string): Promise<void> {
  await apiJson<void>(`/v1/contacts/${id}`, { method: "DELETE" });
}

// --- Contactos: importar desde Google / iCloud ------------------------------------

/** `POST /v1/contacts/import/google` — reusa la cuenta `google` ya conectada
 * en Conectores (Gmail/Calendar); si no existe, el backend responde 400. */
export async function importContactsGoogle(): Promise<ContactsImportResult> {
  return apiJson<ContactsImportResult>("/v1/contacts/import/google", { method: "POST" });
}

export async function getICloudContactsStatus(): Promise<ICloudStatus> {
  return apiJson<ICloudStatus>("/v1/contacts/import/icloud/status");
}

export async function putICloudCredentials(
  appleId: string,
  appSpecificPassword: string,
): Promise<void> {
  await apiJson<void>("/v1/contacts/import/icloud/credentials", {
    method: "PUT",
    body: { apple_id: appleId, app_specific_password: appSpecificPassword },
  });
}

export async function deleteICloudCredentials(): Promise<void> {
  await apiJson<void>("/v1/contacts/import/icloud/credentials", { method: "DELETE" });
}

export async function importContactsICloud(): Promise<ICloudContactsImportResult> {
  return apiJson<ICloudContactsImportResult>("/v1/contacts/import/icloud", { method: "POST" });
}

// --- Finanzas -------------------------------------------------------------------------

export async function listTransactions(mes?: string): Promise<Transaction[]> {
  const qs = mes ? `?mes=${encodeURIComponent(mes)}` : "";
  return apiJson<Transaction[]>(`/v1/finance/transactions${qs}`);
}

export async function createTransaction(input: {
  fecha: string;
  monto: number | string;
  moneda?: string;
  categoria?: string | null;
  descripcion?: string | null;
  cuenta?: string | null;
}): Promise<Transaction> {
  return apiJson<Transaction>("/v1/finance/transactions", { method: "POST", body: input });
}

export async function updateTransaction(id: string, patch: Partial<Transaction>): Promise<Transaction> {
  return apiJson<Transaction>(`/v1/finance/transactions/${id}`, { method: "PUT", body: patch });
}

export async function deleteTransaction(id: string): Promise<void> {
  await apiJson<void>(`/v1/finance/transactions/${id}`, { method: "DELETE" });
}

export async function getFinanceSummary(mes: string): Promise<FinanceSummary> {
  return apiJson<FinanceSummary>(`/v1/finance/summary?mes=${encodeURIComponent(mes)}`);
}

export async function getStripeStatus(): Promise<StripeStatus> {
  return apiJson<StripeStatus>("/v1/finance/stripe/status");
}

export async function putStripeCredentials(apiKey: string): Promise<void> {
  await apiJson<void>("/v1/finance/stripe/credentials", {
    method: "PUT",
    body: { api_key: apiKey },
  });
}

export async function deleteStripeCredentials(): Promise<void> {
  await apiJson<void>("/v1/finance/stripe/credentials", { method: "DELETE" });
}

export async function syncStripeTransactions(): Promise<StripeSyncResult> {
  return apiJson<StripeSyncResult>("/v1/finance/stripe/sync", { method: "POST" });
}

// --- Voz web (§4, §10.9) -----------------------------------------------------------------

export async function transcribeAudio(blob: Blob, language?: string): Promise<{ text: string }> {
  const formData = new FormData();
  formData.append("audio", blob, "audio.webm");
  if (language) formData.append("language", language);
  const res = await authedFetch("/v1/voice/transcribe", { method: "POST", body: formData });
  return parseJsonOrThrow<{ text: string }>(res);
}

export async function speakText(text: string, voiceId?: string | null): Promise<Blob> {
  const res = await authedFetch("/v1/voice/speak", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, voice_id: voiceId ?? null }),
  });
  if (!res.ok) {
    const { message, detail } = await extractErrorMessage(res);
    throw new ApiError(res.status, message, detail);
  }
  return res.blob();
}

/** Chunk MPEG/WAV de `POST /v1/voice/speak/stream`. El fetch arranca al
 * construir el iterable (no al primer `next`), así se puede prefetch de la
 * siguiente oración mientras suena la actual. */
export type SpeakStreamChunk = { chunk: Uint8Array; mime: string };

export function speakTextStream(
  text: string,
  voiceId?: string | null,
  signal?: AbortSignal,
): AsyncIterable<SpeakStreamChunk> {
  const key = `${voiceId ?? ""}\u0001${text}`;
  const cached = ttsAudioCache.get(key);
  if (cached) {
    // Replay desde el caché: sin llamada a `/v1/voice/speak/stream`. Se
    // emiten los mismos trozos `{chunk, mime}` que el stream original, así
    // que el player incremental los consume idéntico.
    const parts = cached.parts;
    const mime = cached.mime;
    return {
      [Symbol.asyncIterator](): AsyncIterator<SpeakStreamChunk> {
        return (async function* () {
          for (const part of parts) yield { chunk: part, mime };
        })();
      },
    };
  }
  const responsePromise = authedFetch("/v1/voice/speak/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, voice_id: voiceId ?? null }),
    signal,
  });
  const parts: Uint8Array[] = [];
  let mime = "audio/mpeg";
  return {
    [Symbol.asyncIterator](): AsyncIterator<SpeakStreamChunk> {
      const source = readSpeakStream(responsePromise);
      return (async function* () {
        for await (const chunk of source) {
          parts.push(chunk.chunk);
          mime = chunk.mime;
          yield chunk;
        }
        // Caché solo si el stream terminó completo (no abortado a mitad):
        // un audio parcial no debe reutilizarse como si fuera el entero.
        if (!signal?.aborted) {
          const bytes = parts.reduce((sum, part) => sum + part.byteLength, 0);
          ttsAudioCache.set(key, { parts, mime });
          ttsAudioCacheBytes += bytes;
          while (ttsAudioCacheBytes > TTS_AUDIO_CACHE_MAX_BYTES && ttsAudioCache.size > 1) {
            const oldestKey = ttsAudioCache.keys().next().value;
            if (oldestKey === undefined || oldestKey === key) break;
            const oldest = ttsAudioCache.get(oldestKey);
            if (oldest) {
              ttsAudioCacheBytes -= oldest.parts.reduce((sum, part) => sum + part.byteLength, 0);
            }
            ttsAudioCache.delete(oldestKey);
          }
        }
      })();
    },
  };
}

// Caché en memoria del audio TTS por `voz+texto` (~60 MB tope): reproducir el
// mismo mensaje otra vez NO vuelve a pedir el audio a la API.
const ttsAudioCache = new Map<string, { parts: Uint8Array[]; mime: string }>();
const TTS_AUDIO_CACHE_MAX_BYTES = 60 * 1024 * 1024;
let ttsAudioCacheBytes = 0;

async function* readSpeakStream(
  responsePromise: Promise<Response>,
): AsyncGenerator<SpeakStreamChunk> {
  const res = await responsePromise;
  if (!res.ok) {
    const { message, detail } = await extractErrorMessage(res);
    throw new ApiError(res.status, message, detail);
  }
  if (!res.body) {
    throw new ApiError(res.status || 502, "El servidor no envió un cuerpo de audio.");
  }
  const mime = res.headers.get("content-type")?.split(";")[0]?.trim() || "audio/mpeg";
  const reader = res.body.getReader();
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      if (value && value.byteLength > 0) {
        yield { chunk: value, mime };
      }
    }
  } finally {
    try {
      reader.releaseLock();
    } catch {
      // El lector ya se liberó si el consumidor abortó a mitad de stream.
    }
  }
}

// --- Companion de escritorio (§10.12) ------------------------------------------------------

export async function getCompanionPairCode(): Promise<{ code: string }> {
  return apiJson<{ code: string }>("/v1/companion/pair-code", { method: "POST" });
}

/** QR temporal para vincular la app móvil sin escribir ni copiar secretos. */
export async function createDevicePairing(): Promise<DevicePairingOut> {
  return apiJson<DevicePairingOut>("/v1/devices/pairing", { method: "POST" });
}

// --- Uso y facturación --------------------------------------------------------------------

export async function getUsage(): Promise<UsageOut> {
  return apiJson<UsageOut>("/v1/usage");
}

export async function getBillingPortalUrl(): Promise<{ url: string }> {
  return apiJson<{ url: string }>("/v1/billing/portal", { method: "POST" });
}
