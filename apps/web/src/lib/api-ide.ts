/**
 * Cliente HTTP de `apps/api/edecan_api/routers/ide.py` (`/v1/ide/*`, IDE
 * embebido sobre el companion de escritorio — `ARCHITECTURE.md` §10.12,
 * `ROADMAP_V2.md` §7.6/§7.8, WP-V2-08).
 *
 * `lib/api.ts` es compartido y no se toca (`ROADMAP_V2.md` §7.10): este
 * archivo importa de ahí solo lo que SÍ está exportado (`API_BASE_URL`,
 * `ApiError`) y replica localmente el mismo patrón de autenticación
 * (`Authorization: Bearer <access_token>` + un reintento tras refrescar en
 * 401) porque `authedFetch`/`apiJson` siguen siendo privados. La rotación sí
 * usa `session-refresh`, compartido con todos los vertical slices.
 *
 * Tipos propios (`IdeTreeNode`, `IdeFile`, ...) en vez de `lib/types.ts` por
 * el mismo motivo: ese archivo tampoco está en la lista de rutas que este
 * paquete de trabajo puede tocar.
 */

import { API_BASE_URL, ApiError } from "./api";
import { recoverSessionAfterUnauthorized, isRefreshResultCurrent } from "./session-refresh";
import { getAccessToken, getDesktopCapability } from "./tokens";

/** `edecan_schemas.plans.FLAG_COMPANION_IDE` (`ROADMAP_V2.md` §7.2). */
export const FLAG_COMPANION_IDE = "companion.ide";

// ---------------------------------------------------------------------------
// Tipos (formas reales de `apps/api/edecan_api/routers/ide.py`)
// ---------------------------------------------------------------------------

export interface IdeStatus {
  connected: boolean;
}

export interface IdeTreeNode {
  name: string;
  is_dir: boolean;
  /** Solo presente en archivos (`null` si `stat()` falló). */
  size_bytes?: number | null;
  /**
   * Solo presente en carpetas: `null` = todavía no expandida (llegó al
   * límite de profundidad, o es un symlink que escapa del sandbox), lista
   * (posiblemente vacía) = ya expandida.
   */
  children?: IdeTreeNode[] | null;
}

export interface IdeTree {
  path: string;
  entries: IdeTreeNode[];
  truncated: boolean;
}

export interface IdeFile {
  path: string;
  content: string;
  encoding: "utf-8" | "base64";
  size_bytes: number;
}

export interface IdeWriteResult {
  path: string;
  bytes_written: number;
}

export interface IdeEditResult {
  path: string;
  replacements: number;
  bytes_written: number;
}

export interface IdeRunResult {
  stdout: string;
  stderr: string;
  exit_code: number;
  truncated: boolean;
}

export interface IdeSearchMatch {
  path: string;
  line: number;
  texto: string;
}

export interface IdeSearchResult {
  query: string;
  matches: IdeSearchMatch[];
  truncated: boolean;
}

export interface IdeWorkspace {
  id: string;
  name: string;
  path: string;
  active: boolean;
  created_at: string;
  available?: boolean;
  is_git_repository?: boolean;
}

export interface IdeWorkspaces {
  workspaces: IdeWorkspace[];
}

export interface IdeModelOption {
  id: string;
  nombre: string;
  descripcion?: string;
  insignia?: string;
  contexto_ventana?: number;
  capacidades?: string[];
}

export interface IdeModels {
  models: IdeModelOption[];
}

export type IdeSessionKind = "agent" | "terminal";
export type IdeSessionStatus =
  | "starting"
  | "running"
  | "completed"
  | "failed"
  | "closed"
  | "cancelled"
  | "interrupted";

export interface IdeSession {
  id: string;
  kind: IdeSessionKind;
  workspace_id: string;
  workspace_name: string;
  status: IdeSessionStatus | string;
  started_at: string;
  ended_at: string | null;
  exit_code: number | null;
  command?: string[] | null;
  provider?: string | null;
  model?: string | null;
  title?: string | null;
  conversation_id?: string | null;
}

export interface IdeSessions {
  sessions: IdeSession[];
}

/**
 * Respuesta de `POST /v1/ide/agents` desde que se puede escribirle al agente
 * mientras trabaja (`lib/ide-cola.ts`).
 *
 * Sigue siendo la sesión de siempre. `queued` solo aparece cuando el mensaje
 * NO arrancó un turno: lo arma `routers/ide.py::post_agent` con lo que
 * devuelve `Session.enqueue_user_message`, y su `position` es 1-based (1 = es
 * el próximo que el agente va a leer). Con el agente libre, el motor responde
 * igual que siempre y este campo no viene.
 */
export interface IdeAgentQueued {
  id: string;
  position: number;
  pending: number;
  max_pending: number;
  queued_at: string;
}

export interface IdeAgentStart extends IdeSession {
  queued?: IdeAgentQueued | null;
}

export interface IdeSessionEvent {
  cursor: number;
  type: string;
  text: string;
  stream?: string | null;
  timestamp: string;
  /**
   * Canal deliberado de UI: bloques de `edecan_schemas.ide_blocks` (tabla,
   * gráfica) que el agente acuñó con `mostrar_tabla`/`mostrar_grafica`. Sin
   * tipar acá a propósito -- `components/ide/ide-blocks.ts` lo valida antes de
   * dibujar nada, igual que `lib/chat-blocks.ts` con los bloques del chat.
   * Ausente en casi todos los eventos; `text` siempre trae el equivalente.
   */
  presentation?: unknown;
}

export interface IdeSessionRead {
  session: IdeSession;
  events: IdeSessionEvent[];
  next_cursor: number;
  has_more?: boolean;
}

export interface IdeAgentAttachment {
  name: string;
  media_type: "image/jpeg" | "image/png" | "image/webp" | "image/gif";
  data: string;
}

export interface IdeGitFile {
  path: string;
  index_status: string;
  worktree_status: string;
  original_path?: string | null;
}

export interface IdeGitStatus {
  branch?: string | null;
  upstream?: string | null;
  ahead: number;
  behind: number;
  files: IdeGitFile[];
}

export interface IdeGitDiff {
  text: string;
  truncated: boolean;
}

export interface IdeGitCommit {
  hash: string;
  short_hash: string;
  author: string;
  email: string;
  timestamp: string;
  subject: string;
}

export interface IdeGitLog {
  commits: IdeGitCommit[];
}

// ---------------------------------------------------------------------------
// Referencias "@" (`ide_referencias.ReferenceService`, 1.1 del plan de
// paridad) y revisión de diffs por turno (`ide_checkpoints.CheckpointStore` +
// `components/ide/DiffReview.tsx`, 1.2). Espejo exacto de lo que devuelve
// `edecan_companion.ide_runtime.IDERuntime.dispatch` para las acciones
// `ide_reference_search` / `ide_agent_diff` / `ide_agent_cost`.
// ---------------------------------------------------------------------------

export type IdeReferenceKind = "file" | "folder" | "symbol";

export interface IdeReferenceMatch {
  type: IdeReferenceKind;
  path: string;
  name: string;
  symbol_kind: "function" | "class" | null;
  line: number | null;
}

export interface IdeReferenceSearchResult {
  prefix: string;
  matches: IdeReferenceMatch[];
  total_matches: number;
  index_truncated: boolean;
}

export type IdeDiffFileKind = "added" | "modified" | "deleted" | "unavailable";

export interface IdeDiffFile {
  path: string;
  kind: IdeDiffFileKind;
  before_content: string | null;
  after_content: string | null;
  unavailable_reason: string | null;
}

export interface IdeAgentDiff {
  checkpoint_id: string | null;
  sealed: boolean;
  files: IdeDiffFile[];
}

/** Espejo de `ide_costos.TaskCost.resumen()`. */
export interface IdeAgentCost {
  total_acciones: number;
  total_eventos: number;
  total_salidas: number;
  duracion_segundos: number;
  duracion_humana: string;
  tokens: {
    entrada: number;
    salida: number;
    total: number;
    estimados: boolean;
  };
  costo_usd: number | null;
  modelo: string | null;
  por_herramienta: Array<{
    nombre: string;
    acciones: number;
    tokens_estimados: number;
    tokens_reales: number | null;
    segundos: number;
    porcentaje_tokens: number;
    porcentaje_tiempo: number;
  }>;
  bucles: Array<{
    herramientas: string[];
    patron: string;
    repeticiones_del_ciclo: number;
    acciones_involucradas: number;
    indice_inicio: number;
    indice_fin: number;
    hubo_cambios_en_el_tramo: boolean;
  }>;
  comparacion: {
    tareas_comparadas: number;
    duracion_tipica_segundos: number;
    acciones_tipicas: number;
    tokens_tipicos: number;
    factor_duracion: number;
    factor_acciones: number;
    factor_tokens: number;
    fuera_de_lo_normal: boolean;
    motivo: string;
  } | null;
  senales: string[];
  advertencias: string[];
}

/** Espejo de `SemanticSearchService.search()` (`ide_busqueda_semantica.py`, 2.2). */
export interface IdeSemanticMatch {
  path: string;
  start_line: number;
  end_line: number;
  excerpt: string;
  score: number | null;
}

export interface IdeSemanticSearchResult {
  query: string;
  mode: "semantico" | "texto";
  matches: IdeSemanticMatch[];
  total_indexed_chunks: number;
  index_truncated: boolean;
  degraded_reason: string | null;
}

export interface IdeSemanticSearchStatus {
  workspace_id: string;
  embeddings_available: boolean;
  state: "indexing" | "ready" | "sin_indexar";
  index_exists: boolean;
  total_chunks: number;
  total_files: number;
  truncated: boolean;
  built_at: string | null;
}

/** Espejo de `MemoryNote.to_json()` (`ide_memoria.py`, 2.3). */
export interface IdeMemoryNote {
  id: string;
  workspace_id: string;
  kind: "convencion" | "ubicacion" | "error_evitar" | "decision";
  content: string;
  importance: number;
  created_at: string;
  last_used_at: string;
  use_count: number;
  // El porqué de una ADR (9.2). Opcionales porque `to_json()` NO serializa las
  // claves vacías: un recuerdo que no es "decision" -- y todo lo guardado antes
  // de que estos campos existieran -- llega sin ellas, no con ellas en null.
  alternativas?: string[];
  por_que_no?: string;
  se_invalida_si?: string;
}

/**
 * Espejo de `HechoVerificado.to_json()` + los campos que agrega
 * `ConocimientoStore._con_frescura()` (`apps/companion/edecan_companion/ide_conocimiento.py`).
 *
 * IMPORTANTE (verificado leyendo el código, no asumido): a diferencia de la
 * memoria de arriba, `ConocimientoStore` todavía NO tiene ruta HTTP -- no
 * hay ningún endpoint de conocimiento en `routers/ide.py` ni ningún
 * `action` correspondiente en `ide_runtime.py` (ese archivo solo despacha
 * `ide_memory_list`/`ide_memory_forget`, nada con "conocimiento" ni
 * "hecho"). El store del companion, con `listar()`/`olvidar()` ya
 * escritos y probados (`tests/test_ide_conocimiento.py`), está listo para
 * que alguien lo cablee -- ver el propio docstring de `ide_conocimiento.py`
 * ("en desarrollo en paralelo, no importa nada de routers/ide.py").
 *
 * `getIdeKnowledgeFacts`/`deleteIdeKnowledgeFact` de abajo llaman a las
 * rutas que ESE cableado debería exponer (mismo patrón REST que
 * `/memory`), para que el día que exista baste con agregar el router +
 * dispatch -- ningún archivo de este paquete de trabajo toca
 * `routers/ide.py` ni `ide_runtime.py`. Mientras tanto, ambas llamadas
 * devuelven 404 (ruta inexistente) y quien las use debe tratar ese 404
 * como "todavía no conectado", no como un error real -- ver
 * `MemoryKnowledgePanel.tsx`.
 */
export type IdeKnowledgeFactKind =
  | "version_vigente"
  | "practica_recomendada"
  | "cambio_deprecado"
  | "limite_o_cuota";

export interface IdeKnowledgeFact {
  id: string;
  workspace_id: string;
  kind: IdeKnowledgeFactKind;
  tema: string;
  contenido: string;
  fuente_url: string;
  verified_at: string;
  created_at: string;
  veces_reverificado: number;
  /** Calculados al leer, no guardados: ver `_con_frescura`. */
  edad_dias: number;
  ttl_dias: number;
  vigente: boolean;
}

/**
 * Espejo de `ProjectRegistry._snapshot_project()`
 * (`apps/companion/edecan_companion/ide_projects.py`). Mismas claves que
 * `IdeProjectSummary` en `components/ide/ProjectSidebar.tsx` -- ese
 * componente declara su propio contrato porque no depende de este archivo,
 * pero la forma real la define el companion, así que vive documentada acá.
 */
export interface IdeProject {
  id: string;
  name: string;
  workspace_id: string;
  workspace_name: string | null;
  workspace_path: string | null;
  workspace_available: boolean;
  conversation_count: number;
  created_at: string;
  updated_at: string;
}

/** Espejo de las filas de conversación que persiste `ProjectRegistry`. */
export interface IdeConversation {
  id: string;
  project_id: string | null;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface IdeProjectDeleteResult {
  deleted_project_id: string;
  conversations_deleted: boolean;
  affected_conversation_ids: string[];
}

export interface IdeConversationDeleteResult {
  deleted_conversation_id: string;
  closed_session_ids: string[];
}

// ---------------------------------------------------------------------------
// Auth (mismo patrón que `lib/api.ts` / `lib/api-remoto.ts`, ver docstring)
// ---------------------------------------------------------------------------

// `/v1/auth/refresh` exige `totp_code` si la cuenta tiene 2FA activo (mismo
// gate que `/login`, ver `auth.py::refresh`, ~L196-207). Replica acá el
// manejo de `lib/api.ts::tryRefreshWithTotpPrompt` (HOTFIXES_PENDIENTES.md
// #2) para no forzar un logout duro cada ~30 min a usuarios con TOTP activo.
async function rawFetch(path: string, init: RequestInit): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = getAccessToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const desktopCapability = getDesktopCapability();
  if (desktopCapability) {
    headers.set("X-Edecan-Desktop-Capability", desktopCapability);
  }
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
// Fetchers (`/v1/ide/*`, ver `apps/api/edecan_api/routers/ide.py`)
// ---------------------------------------------------------------------------

/** `GET /v1/ide/status` — `{connected: false}` si no hay companion emparejado (nunca lanza por eso). */
export async function getIdeStatus(): Promise<IdeStatus> {
  return apiJson<IdeStatus>("/v1/ide/status");
}

/**
 * `GET /v1/ide/tree` — árbol del sandbox del companion. Pensado para "lazy
 * por carpeta": llama con `path` de la carpeta que se está expandiendo y
 * `maxDepth=1` para traer solo sus hijos inmediatos.
 */
export async function getIdeTree(
  path?: string,
  maxDepth?: number,
  maxEntries?: number,
): Promise<IdeTree> {
  const params = new URLSearchParams();
  if (path) params.set("path", path);
  if (maxDepth !== undefined) params.set("max_depth", String(maxDepth));
  if (maxEntries !== undefined) params.set("max_entries", String(maxEntries));
  const qs = params.toString();
  return apiJson<IdeTree>(`/v1/ide/tree${qs ? `?${qs}` : ""}`);
}

/** `GET /v1/ide/file?path=` */
export async function getIdeFile(path: string): Promise<IdeFile> {
  return apiJson<IdeFile>(`/v1/ide/file?path=${encodeURIComponent(path)}`);
}

/** `PUT /v1/ide/file` — reemplaza el contenido completo del archivo (crea carpetas padre si hacen falta). */
export async function putIdeFile(path: string, content: string): Promise<IdeWriteResult> {
  return apiJson<IdeWriteResult>("/v1/ide/file", {
    method: "PUT",
    ...jsonBody({ path, content }),
  });
}

/** `POST /v1/ide/edit` — edición quirúrgica: `old_string` debe ser único salvo `replace_all`. */
export async function postIdeEdit(input: {
  path: string;
  old_string: string;
  new_string: string;
  replace_all?: boolean;
}): Promise<IdeEditResult> {
  return apiJson<IdeEditResult>("/v1/ide/edit", { method: "POST", ...jsonBody(input) });
}

/** `POST /v1/ide/run` — corre `command` con el `allowed_commands` que configuró el dueño del equipo. */
export async function postIdeRun(command: string): Promise<IdeRunResult> {
  return apiJson<IdeRunResult>("/v1/ide/run", { method: "POST", ...jsonBody({ command }) });
}

/** `POST /v1/ide/search` — substring case-insensitive, línea por línea. */
export async function postIdeSearch(query: string, path?: string): Promise<IdeSearchResult> {
  return apiJson<IdeSearchResult>("/v1/ide/search", {
    method: "POST",
    ...jsonBody({ query, path }),
  });
}

// ---------------------------------------------------------------------------
// Workspaces y sesiones persistentes. Estas rutas nunca exponen una ruta del
// Mac a terceros: el API solo reenvía IDs opacos al companion emparejado.
// ---------------------------------------------------------------------------

export async function getIdeWorkspaces(): Promise<IdeWorkspace[]> {
  return (await apiJson<IdeWorkspaces>("/v1/ide/workspaces")).workspaces;
}

export async function createIdeWorkspace(path: string, name?: string): Promise<IdeWorkspace> {
  return apiJson<IdeWorkspace>("/v1/ide/workspaces", {
    method: "POST",
    ...jsonBody({ path, name: name?.trim() || undefined }),
  });
}

export async function pickIdeWorkspace(name?: string): Promise<{
  cancelled: boolean;
  workspace?: IdeWorkspace;
}> {
  return apiJson("/v1/ide/workspaces/pick", {
    method: "POST",
    ...jsonBody({ name: name?.trim() || undefined, activate: true }),
  });
}

export async function cloneIdeWorkspace(input: {
  parent_workspace_id: string;
  url: string;
  name?: string;
  branch?: string;
  depth?: number;
}): Promise<{ workspace: IdeWorkspace; source_host?: string }> {
  return apiJson("/v1/ide/workspaces/clone", {
    method: "POST",
    ...jsonBody({ ...input, activate: true }),
  });
}

export async function activateIdeWorkspace(workspaceId: string): Promise<IdeWorkspace> {
  return apiJson<IdeWorkspace>(
    `/v1/ide/workspaces/${encodeURIComponent(workspaceId)}/activate`,
    { method: "POST" },
  );
}

export async function getIdeWorkspaceTree(
  workspaceId: string,
  path?: string,
  maxDepth = 3,
  maxEntries = 700,
): Promise<IdeTree> {
  const params = new URLSearchParams({
    max_depth: String(maxDepth),
    max_entries: String(maxEntries),
  });
  if (path) params.set("path", path);
  return apiJson<IdeTree>(
    `/v1/ide/workspaces/${encodeURIComponent(workspaceId)}/tree?${params.toString()}`,
  );
}

export async function getIdeWorkspaceFile(
  workspaceId: string,
  path: string,
): Promise<IdeFile> {
  return apiJson<IdeFile>(
    `/v1/ide/workspaces/${encodeURIComponent(workspaceId)}/file?path=${encodeURIComponent(path)}`,
  );
}

export async function putIdeWorkspaceFile(
  workspaceId: string,
  path: string,
  content: string,
): Promise<IdeWriteResult> {
  return apiJson<IdeWriteResult>(
    `/v1/ide/workspaces/${encodeURIComponent(workspaceId)}/file`,
    { method: "PUT", ...jsonBody({ path, content }) },
  );
}

export async function getIdeAgents(workspaceId?: string): Promise<IdeSession[]> {
  const query = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : "";
  return (await apiJson<IdeSessions>(`/v1/ide/agents${query}`)).sessions;
}

/**
 * Manda un mensaje a una conversación de agente. Desde el cambio de cola, esta
 * llamada YA NO falla porque haya un turno en curso: en ese caso el motor
 * responde la misma sesión con el objeto `queued` y el mensaje entra en la
 * vuelta siguiente (ver `IdeAgentStart` y `lib/ide-cola.ts`). Sigue fallando
 * con 422 en los dos casos en que el motor dice que no: la cola llena (cinco
 * mensajes sin leer) y un plan esperando aprobación en esa conversación.
 */
export async function createIdeAgent(input: {
  workspace_id: string;
  prompt: string;
  model?: string;
  title?: string;
  conversation_id?: string;
  attachments?: IdeAgentAttachment[];
}): Promise<IdeAgentStart> {
  return apiJson<IdeAgentStart>("/v1/ide/agents", {
    method: "POST",
    ...jsonBody({ ...input, provider: "workers_ai" }),
  });
}

export async function readIdeAgent(sessionId: string, cursor = 0): Promise<IdeSessionRead> {
  return apiJson<IdeSessionRead>(
    `/v1/ide/agents/${encodeURIComponent(sessionId)}?cursor=${Math.max(0, cursor)}`,
  );
}

export async function cancelIdeAgent(sessionId: string): Promise<void> {
  await apiJson(`/v1/ide/agents/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
}

export async function confirmIdeAgentMcp(
  sessionId: string,
  callId: string,
  approved: boolean,
): Promise<{ accepted: boolean; approved: boolean }> {
  return apiJson(
    `/v1/ide/agents/${encodeURIComponent(sessionId)}/mcp/${encodeURIComponent(callId)}/confirm`,
    {
      method: "POST",
      ...jsonBody({ approved }),
    },
  );
}

export async function getIdeModels(): Promise<IdeModelOption[]> {
  return (await apiJson<IdeModels>("/v1/ide/models")).models;
}

/**
 * `POST /v1/ide/agents/{id}/model` -- cambia el modelo de una sesión ya
 * viva (`SessionManager.set_modelo_agente`, cable que faltaba: encargo
 * "Hecho 2"). Con sesión viva, `estado-ide.ts` llama a esto EN VEZ de solo
 * guardar la preferencia local, así el cambio se aplica sin esperar al
 * próximo mensaje. El esfuerzo vigente de la sesión no se toca -- lo
 * conserva `set_modelo_agente` del lado del companion.
 */
export async function setIdeAgentModel(sessionId: string, model: string): Promise<{ model: string }> {
  return apiJson<{ model: string }>(`/v1/ide/agents/${encodeURIComponent(sessionId)}/model`, {
    method: "POST",
    ...jsonBody({ model }),
  });
}

export async function getIdeTerminals(workspaceId?: string): Promise<IdeSession[]> {
  const query = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : "";
  return (await apiJson<IdeSessions>(`/v1/ide/terminals${query}`)).sessions;
}

export async function createIdeTerminal(input: {
  workspace_id: string;
  title?: string;
  argv?: string[];
}): Promise<IdeSession> {
  return apiJson<IdeSession>("/v1/ide/terminals", {
    method: "POST",
    ...jsonBody(input),
  });
}

export async function readIdeTerminal(sessionId: string, cursor = 0): Promise<IdeSessionRead> {
  return apiJson<IdeSessionRead>(
    `/v1/ide/terminals/${encodeURIComponent(sessionId)}?cursor=${Math.max(0, cursor)}`,
  );
}

export async function sendIdeTerminalInput(sessionId: string, data: string): Promise<void> {
  await apiJson(`/v1/ide/terminals/${encodeURIComponent(sessionId)}/input`, {
    method: "POST",
    ...jsonBody({ data }),
  });
}

export async function closeIdeTerminal(sessionId: string): Promise<void> {
  await apiJson(`/v1/ide/terminals/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
}

export async function getIdeGitStatus(workspaceId: string): Promise<IdeGitStatus> {
  return apiJson<IdeGitStatus>(
    `/v1/ide/workspaces/${encodeURIComponent(workspaceId)}/git/status`,
  );
}

export async function getIdeGitDiff(
  workspaceId: string,
  staged = false,
  path?: string,
): Promise<IdeGitDiff> {
  const params = new URLSearchParams({ staged: String(staged) });
  if (path) params.set("path", path);
  return apiJson<IdeGitDiff>(
    `/v1/ide/workspaces/${encodeURIComponent(workspaceId)}/git/diff?${params.toString()}`,
  );
}

export async function getIdeGitLog(workspaceId: string): Promise<IdeGitLog> {
  return apiJson<IdeGitLog>(
    `/v1/ide/workspaces/${encodeURIComponent(workspaceId)}/git/log`,
  );
}

/** `GET /v1/ide/workspaces/{id}/references` — menú `@` del compositor. */
export async function getIdeReferences(
  workspaceId: string,
  prefix: string,
  options?: { kinds?: IdeReferenceKind[]; limit?: number },
): Promise<IdeReferenceSearchResult> {
  const params = new URLSearchParams({ prefix });
  if (options?.kinds?.length) params.set("kinds", options.kinds.join(","));
  if (options?.limit) params.set("limit", String(options.limit));
  return apiJson<IdeReferenceSearchResult>(
    `/v1/ide/workspaces/${encodeURIComponent(workspaceId)}/references?${params.toString()}`,
  );
}

/** `POST /v1/ide/workspaces/{id}/search/semantic` — búsqueda por significado (2.2). */
export async function postIdeSemanticSearch(
  workspaceId: string,
  query: string,
  k = 10,
): Promise<IdeSemanticSearchResult> {
  return apiJson<IdeSemanticSearchResult>(
    `/v1/ide/workspaces/${encodeURIComponent(workspaceId)}/search/semantic`,
    { method: "POST", ...jsonBody({ query, k }) },
  );
}

/** `GET /v1/ide/workspaces/{id}/search/semantic/status` — estado del índice semántico. */
export async function getIdeSemanticSearchStatus(
  workspaceId: string,
): Promise<IdeSemanticSearchStatus> {
  return apiJson<IdeSemanticSearchStatus>(
    `/v1/ide/workspaces/${encodeURIComponent(workspaceId)}/search/semantic/status`,
  );
}

/** `POST /v1/ide/workspaces/{id}/search/semantic/reindex` — fuerza un reindexado en segundo plano. */
export async function postIdeSemanticSearchReindex(
  workspaceId: string,
): Promise<IdeSemanticSearchStatus> {
  return apiJson<IdeSemanticSearchStatus>(
    `/v1/ide/workspaces/${encodeURIComponent(workspaceId)}/search/semantic/reindex`,
    { method: "POST" },
  );
}

/** `GET /v1/ide/workspaces/{id}/memory` — memoria persistente del proyecto (2.3). */
export async function getIdeMemoryNotes(workspaceId: string): Promise<IdeMemoryNote[]> {
  return (
    await apiJson<{ notes: IdeMemoryNote[] }>(
      `/v1/ide/workspaces/${encodeURIComponent(workspaceId)}/memory`,
    )
  ).notes;
}

/** `DELETE /v1/ide/workspaces/{id}/memory/{note_id}` — borra un recuerdo puntual. */
export async function deleteIdeMemoryNote(
  workspaceId: string,
  noteId: string,
): Promise<{ deleted_note_id: string }> {
  return apiJson(
    `/v1/ide/workspaces/${encodeURIComponent(workspaceId)}/memory/${encodeURIComponent(noteId)}`,
    { method: "DELETE" },
  );
}

/**
 * `GET /v1/ide/workspaces/{id}/knowledge` — conocimiento verificado, GLOBAL
 * a todos los workspaces (`ConocimientoStore._hechos_de`, ver el docstring
 * de `IdeKnowledgeFact` arriba). Todavía sin ruta real en el backend: hoy
 * devuelve 404 (nada de este paquete de trabajo puede tocar
 * `routers/ide.py`/`ide_runtime.py` para agregarla) -- quien llame esto
 * debe tratar un `ApiError` con `status === 404` como "backend pendiente",
 * no como una falla real. `workspaceId` solo se manda porque el store lo
 * exige para validar que el workspace existe, no porque el resultado se
 * filtre por él.
 */
export async function getIdeKnowledgeFacts(workspaceId: string): Promise<IdeKnowledgeFact[]> {
  return (
    await apiJson<{ facts: IdeKnowledgeFact[] }>(
      `/v1/ide/workspaces/${encodeURIComponent(workspaceId)}/knowledge`,
    )
  ).facts;
}

/** `DELETE /v1/ide/workspaces/{id}/knowledge/{fact_id}` — borra un hecho verificado puntual (`ConocimientoStore.olvidar`). */
export async function deleteIdeKnowledgeFact(
  workspaceId: string,
  factId: string,
): Promise<{ deleted_hecho_id: string }> {
  return apiJson(
    `/v1/ide/workspaces/${encodeURIComponent(workspaceId)}/knowledge/${encodeURIComponent(factId)}`,
    { method: "DELETE" },
  );
}

/** `GET /v1/ide/agents/{id}/diff` — archivos del último turno, para revisar y aceptar/rechazar. */
export async function getIdeAgentDiff(sessionId: string): Promise<IdeAgentDiff> {
  return apiJson<IdeAgentDiff>(`/v1/ide/agents/${encodeURIComponent(sessionId)}/diff`);
}

/** `POST /v1/ide/agents/{id}/diff/reject` — deshace un solo archivo restaurando su checkpoint. */
export async function rejectIdeAgentDiffFile(
  sessionId: string,
  path: string,
): Promise<{ checkpoint_id: string; restored: string[]; deleted: string[]; unchanged: string[]; conflicts: string[] }> {
  return apiJson(`/v1/ide/agents/${encodeURIComponent(sessionId)}/diff/reject`, {
    method: "POST",
    ...jsonBody({ path }),
  });
}

/** `GET /v1/ide/agents/{id}/cost` — contabilidad de costo del último turno. */
export async function getIdeAgentCost(sessionId: string): Promise<IdeAgentCost> {
  return apiJson<IdeAgentCost>(`/v1/ide/agents/${encodeURIComponent(sessionId)}/cost`);
}

// ---------------------------------------------------------------------------
// Planes propuestos por el agente: aprobar, editar y rechazar
// (`routers/ide.py` sección "Planes", `ide_sessions.approve_plan/edit_plan/
// reject_plan`). Espejo de `ide_plan.Plan.public()` / `PlanStep.public()`.
// ---------------------------------------------------------------------------

export interface IdePlanStepPublic {
  id: string;
  description: string;
  status: string;
  note: string | null;
}

export interface IdePlanPublic {
  id: string;
  session_id: string;
  goal: string;
  status: string;
  current_step_index: number | null;
  steps: IdePlanStepPublic[];
  error: string | null;
  created_at: string;
  updated_at: string;
}

/**
 * `POST /v1/ide/agents/{id}/plan/{plan_id}/approve` — arranca la ejecución
 * REAL del plan: reparte los pasos entre subagentes que ESCRIBEN archivos
 * (`ide_sessions.approve_plan` → `ide_reparto`/`ide_equipo`). No es una
 * lectura — quien llame esto debe haber confirmado con la persona antes.
 */
export async function approveIdeAgentPlan(
  sessionId: string,
  planId: string,
): Promise<{ plan: IdePlanPublic }> {
  return apiJson(
    `/v1/ide/agents/${encodeURIComponent(sessionId)}/plan/${encodeURIComponent(planId)}/approve`,
    { method: "POST" },
  );
}

/** `POST /v1/ide/agents/{id}/plan/{plan_id}/edit` — corrige el desglose mientras el plan sigue `proposed`. */
export async function editIdeAgentPlan(
  sessionId: string,
  planId: string,
  steps: string[],
): Promise<{ plan: IdePlanPublic }> {
  return apiJson(
    `/v1/ide/agents/${encodeURIComponent(sessionId)}/plan/${encodeURIComponent(planId)}/edit`,
    { method: "POST", ...jsonBody({ steps }) },
  );
}

/** `POST /v1/ide/agents/{id}/plan/{plan_id}/reject` — descarta el plan y deja la sesión libre para el siguiente mensaje. */
export async function rejectIdeAgentPlan(
  sessionId: string,
  planId: string,
  reason?: string,
): Promise<{ plan: IdePlanPublic }> {
  return apiJson(
    `/v1/ide/agents/${encodeURIComponent(sessionId)}/plan/${encodeURIComponent(planId)}/reject`,
    { method: "POST", ...jsonBody({ reason }) },
  );
}

// ---------------------------------------------------------------------------
// Preguntas del agente a la persona ("que la IA me hable", encargo punto 5):
// opencode las manda por `question.v2.asked`, el companion las traduce a un
// evento de sesión `agent_question` (`ide_opencode_eventos.py::traducir_pregunta`)
// con este payload exacto en `event.text` -- espejo 1:1, no una forma propia.
// Igual que el plan (comentario de `AgentThread.tsx`), los endpoints REST que
// resuelven la pregunta (`.../question/{id}/reply|reject`) siguen el mismo
// patrón que `.../plan/{id}/approve|reject` y `.../mcp/{id}/confirm` de
// arriba, pero routers/ide.py TODAVÍA no los expone -- cablearlos es trabajo
// de otro workflow (companion/API, fuera de mis archivos). Responder o
// rechazar antes de que existan da un error real (capturado y mostrado, no
// una falsa confirmación) en vez de fingir que ya funciona.
// ---------------------------------------------------------------------------

export interface IdeAgentQuestionOption {
  label: string;
  description: string;
}

export interface IdeAgentQuestionItem {
  question: string;
  header: string;
  options: IdeAgentQuestionOption[];
  multiple: boolean | null;
  custom: boolean | null;
}

export interface IdeAgentQuestion {
  request_id: string;
  session_id: string;
  questions: IdeAgentQuestionItem[];
  tool?: { message_id: string; call_id: string } | null;
}

/** `POST /v1/ide/agents/{id}/question/{request_id}/reply` — una respuesta por cada pregunta de la solicitud, en el mismo orden.
 *
 * El companion (`ide_agent_question_answer`) espera `respuestas` como lista DE
 * LISTAS: una lista por pregunta, porque opencode admite preguntas de opción
 * múltiple. Esta tarjeta todavía recoge una sola respuesta por pregunta, así
 * que cada una se envuelve en su propia lista aquí -- el contrato del cable no
 * se recorta para que encaje con lo que hoy pinta la interfaz. */
export async function answerIdeAgentQuestion(
  sessionId: string,
  requestId: string,
  answers: string[],
): Promise<void> {
  await apiJson(
    `/v1/ide/agents/${encodeURIComponent(sessionId)}/question/${encodeURIComponent(requestId)}/reply`,
    { method: "POST", ...jsonBody({ respuestas: answers.map((respuesta) => [respuesta]) }) },
  );
}

// ---------------------------------------------------------------------------
// Permisos del agente (modos Manual / Aceptar ediciones)
//
// opencode PAUSA el turno esperando un `reply` cuando el modo exige aprobación.
// Sin estas dos funciones la persona ve el aviso y no tiene con qué contestar:
// el modo Manual sería una trampa, no un freno.
// ---------------------------------------------------------------------------

export interface IdeAgentPermission {
  request_id: string;
  session_id: string;
  /** La acción que pide permiso, tal como la nombra opencode (`edit`, `bash`, …). */
  action: string;
  /** Archivos, rutas o comandos concretos que abarca. */
  resources: string[];
  /** `false` cuando recordar la decisión no está permitido (acción peligrosa). */
  puede_recordar?: boolean | null;
}

/** `GET /v1/ide/agents/{id}/permission` — lo que el turno dejó esperando. */
export async function listIdeAgentPermissions(
  sessionId: string,
): Promise<{ permisos: IdeAgentPermission[] }> {
  return apiJson<{ permisos: IdeAgentPermission[] }>(
    `/v1/ide/agents/${encodeURIComponent(sessionId)}/permission`,
  );
}

/** `POST /v1/ide/agents/{id}/permission/{request_id}/answer` — el MISMO turno continúa. */
export async function answerIdeAgentPermission(
  sessionId: string,
  requestId: string,
  opciones: { conceder: boolean; recordar?: boolean; mensaje?: string },
): Promise<void> {
  await apiJson(
    `/v1/ide/agents/${encodeURIComponent(sessionId)}/permission/${encodeURIComponent(requestId)}/answer`,
    {
      method: "POST",
      ...jsonBody({
        conceder: opciones.conceder,
        recordar: opciones.recordar ?? false,
        mensaje: opciones.mensaje ?? null,
      }),
    },
  );
}

/** `POST /v1/ide/agents/{id}/question/{request_id}/reject` — la persona no quiere responder; el turno queda libre igual que rechazar un plan. */
export async function rejectIdeAgentQuestion(sessionId: string, requestId: string): Promise<void> {
  await apiJson(
    `/v1/ide/agents/${encodeURIComponent(sessionId)}/question/${encodeURIComponent(requestId)}/reject`,
    { method: "POST" },
  );
}

// ---------------------------------------------------------------------------
// Menú de comandos "/" (`edecan_companion.ide_comandos` +
// `ide_runtime._despachar_comando`). Convive con el menú "@" de arriba: se
// dispara con "/" al inicio del compositor, no con "@".
// ---------------------------------------------------------------------------

export type IdeCommandArgMode = "ninguno" | "opcional" | "obligatorio";

/** Espejo de un `ComandoIDE.public()`-like row (`ide_comandos.listar_comandos`). */
export interface IdeCommandSpec {
  nombre: string;
  alias: string[];
  /** `nombre` + `alias`, en ese orden — cada uno es una entrada propia del menú. */
  nombres: string[];
  descripcion: string;
  argumentos: IdeCommandArgMode;
  destructivo: boolean;
}

export interface IdeCommandList {
  comandos: IdeCommandSpec[];
  /** Texto de `/help`, ya generado por el companion — nunca escrito a mano acá. */
  ayuda: string;
}

/**
 * Resultado de `POST /commands/execute`. Casi todos los campos son
 * opcionales porque cada comando llena solo los que le aplican — ver
 * `edecan_companion.ide_runtime._despachar_comando` para el detalle de cada
 * uno. `ok` es del COMANDO (¿se pudo resolver y ejecutar?), no de la
 * llamada HTTP en sí (esa ya habría lanzado antes de llegar acá).
 */
export interface IdeCommandResult {
  ok: boolean;
  comando?: string;
  error?: string;
  /** Solo presente cuando `ok` es `false` por un nombre de comando desconocido. */
  sugerencia?: string | null;
  /** El comando es destructivo y todavía no llegó `confirmed: true`. */
  requiere_confirmacion?: boolean;
  destructivo?: boolean;
  mensaje?: string;
  data?: Record<string, unknown>;
  /** `/model <id>`: el frontend debe aplicar este id como modelo activo. */
  set_model?: string | null;
  /** `/copy`: texto listo para `navigator.clipboard.writeText(...)`. */
  copy_text?: string | null;
  /** `/export`: contenido listo para descargar como archivo. */
  download?: { filename: string; content: string } | null;
  /** `/review`/`/simplify`: texto para precargar en el compositor. */
  prefill_prompt?: string | null;
  /** Si además de precargar el prompt, hay que enviarlo de una vez. */
  auto_send?: boolean;
  /** `/clear`/`/branch`: la conversación recién creada. */
  nueva_conversacion_id?: string;
  /** `/resume <id>`: sesión de agente a la que hay que volver a apuntar la UI. */
  reanudar_session_id?: string;
  /** El comando respondió con información de solo lectura (no ejecutó nada real
   * localmente): p. ej. `/usage`, `/memory`, `/mcp`, `/voice`, `/remote-control`,
   * `/workflows`, `/config`, `/batch`, `/btw`. La UI lo muestra distinto (nota
   * atenuada) en vez de darlo a entender como un resultado completo. */
  limitado?: boolean;
}

/** `GET /v1/ide/commands` — registro completo para el menú "/" y `/help`. */
export async function getIdeCommands(): Promise<IdeCommandList> {
  return apiJson<IdeCommandList>("/v1/ide/commands");
}

/** `POST /v1/ide/commands/execute` — resuelve y ejecuta una línea "/...". */
export async function executeIdeCommand(
  text: string,
  options?: {
    workspaceId?: string;
    conversationId?: string;
    projectId?: string;
    sessionId?: string;
    confirmed?: boolean;
  },
): Promise<IdeCommandResult> {
  return apiJson<IdeCommandResult>("/v1/ide/commands/execute", {
    method: "POST",
    ...jsonBody({
      text,
      workspace_id: options?.workspaceId,
      conversation_id: options?.conversationId,
      project_id: options?.projectId,
      session_id: options?.sessionId,
      confirmed: options?.confirmed ?? false,
    }),
  });
}

// ---------------------------------------------------------------------------
// Proyectos y conversaciones (`ide_projects.ProjectRegistry`, ver
// `routers/ide.py` sección "Proyectos y conversaciones"). Agrupan sesiones de
// agente bajo una carpeta, estilo "proyectos como carpetas" -- lo que
// consume `components/ide/ProjectSidebar.tsx`.
// ---------------------------------------------------------------------------

export async function getIdeProjects(): Promise<IdeProject[]> {
  return (await apiJson<{ projects: IdeProject[] }>("/v1/ide/projects")).projects;
}

export async function createIdeProject(input: {
  name: string;
  workspace_id: string;
}): Promise<IdeProject> {
  return apiJson<IdeProject>("/v1/ide/projects", { method: "POST", ...jsonBody(input) });
}

export async function renameIdeProject(projectId: string, name: string): Promise<IdeProject> {
  return apiJson<IdeProject>(`/v1/ide/projects/${encodeURIComponent(projectId)}/rename`, {
    method: "POST",
    ...jsonBody({ name }),
  });
}

/**
 * `conversations`: "keep" (default del backend) desasigna las conversaciones
 * del proyecto a "sin proyecto"; "delete" las borra también. Nunca toca la
 * carpeta del repo en disco.
 */
export async function deleteIdeProject(
  projectId: string,
  conversations: "keep" | "delete" = "keep",
): Promise<IdeProjectDeleteResult> {
  return apiJson<IdeProjectDeleteResult>(
    `/v1/ide/projects/${encodeURIComponent(projectId)}?conversations=${conversations}`,
    { method: "DELETE" },
  );
}

export async function getIdeConversations(params?: {
  project_id?: string;
  only_unassigned?: boolean;
}): Promise<IdeConversation[]> {
  const query = new URLSearchParams();
  if (params?.project_id) query.set("project_id", params.project_id);
  if (params?.only_unassigned) query.set("only_unassigned", "true");
  const qs = query.toString();
  return (
    await apiJson<{ conversations: IdeConversation[] }>(`/v1/ide/conversations${qs ? `?${qs}` : ""}`)
  ).conversations;
}

/**
 * `project_id: null` o ausente crea una conversación "sin proyecto". El
 * `id` que devuelve el companion es a propósito el mismo valor que hay que
 * mandar como `conversation_id` en `createIdeAgent` para que
 * `ide_sessions.SessionManager` continúe (o arranque) el hilo correcto.
 */
export async function createIdeConversation(input: {
  project_id?: string | null;
  title?: string;
}): Promise<IdeConversation> {
  return apiJson<IdeConversation>("/v1/ide/conversations", {
    method: "POST",
    ...jsonBody({ project_id: input.project_id ?? undefined, title: input.title }),
  });
}

export async function renameIdeConversation(
  conversationId: string,
  title: string,
): Promise<IdeConversation> {
  return apiJson<IdeConversation>(
    `/v1/ide/conversations/${encodeURIComponent(conversationId)}/rename`,
    { method: "POST", ...jsonBody({ title }) },
  );
}

/** `projectId: null` desasigna la conversación de todo proyecto ("sin proyecto"). */
export async function moveIdeConversation(
  conversationId: string,
  projectId: string | null,
): Promise<IdeConversation> {
  return apiJson<IdeConversation>(
    `/v1/ide/conversations/${encodeURIComponent(conversationId)}/move`,
    { method: "POST", ...jsonBody({ project_id: projectId }) },
  );
}

export async function deleteIdeConversation(
  conversationId: string,
): Promise<IdeConversationDeleteResult> {
  return apiJson<IdeConversationDeleteResult>(
    `/v1/ide/conversations/${encodeURIComponent(conversationId)}`,
    { method: "DELETE" },
  );
}

export { ApiError };
