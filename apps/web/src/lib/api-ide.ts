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
import { getAccessToken } from "./tokens";

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

export { ApiError };
