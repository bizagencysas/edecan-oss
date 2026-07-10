/**
 * Cliente HTTP de `apps/api/edecan_api/routers/ide.py` (`/v1/ide/*`, IDE
 * embebido sobre el companion de escritorio — `ARCHITECTURE.md` §10.12,
 * `ROADMAP_V2.md` §7.6/§7.8, WP-V2-08).
 *
 * `lib/api.ts` es compartido y no se toca (`ROADMAP_V2.md` §7.10): este
 * archivo importa de ahí solo lo que SÍ está exportado (`API_BASE_URL`,
 * `ApiError`) y replica localmente el mismo patrón de autenticación
 * (`Authorization: Bearer <access_token>` + un reintento tras refrescar en
 * 401) porque el resto de las piezas de `api.ts` (`authedFetch`, `apiJson`,
 * el `refreshInFlight` que deduplica refrescos concurrentes) son privadas de
 * ese módulo. Mismo patrón exacto que `lib/api-remoto.ts` (WP-V2-09): una
 * duplicación pequeña y deliberada, no un descuido — la alternativa sería
 * tocar un archivo que este paquete de trabajo tiene prohibido modificar.
 *
 * Tipos propios (`IdeTreeNode`, `IdeFile`, ...) en vez de `lib/types.ts` por
 * el mismo motivo: ese archivo tampoco está en la lista de rutas que este
 * paquete de trabajo puede tocar.
 */

import { API_BASE_URL, ApiError } from "./api";
import { getAccessToken, getRefreshToken, setTokens } from "./tokens";

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
const TOTP_REQUIRED_DETAIL = "Se requiere un código TOTP válido para esta cuenta.";

type RefreshResult = { ok: true } | { ok: false; totpRequired: boolean };

let refreshInFlight: Promise<RefreshResult> | null = null;
let totpPromptInFlight: Promise<boolean> | null = null;

async function rawFetch(path: string, init: RequestInit): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = getAccessToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return fetch(`${API_BASE_URL}${path}`, { ...init, headers });
}

async function tryRefresh(totpCode?: string): Promise<RefreshResult> {
  const refresh_token = getRefreshToken();
  if (!refresh_token) return { ok: false, totpRequired: false };
  if (!refreshInFlight) {
    refreshInFlight = (async (): Promise<RefreshResult> => {
      try {
        const res = await fetch(`${API_BASE_URL}/v1/auth/refresh`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token, totp_code: totpCode || undefined }),
        });
        if (!res.ok) {
          if (res.status === 401) {
            const { message } = await extractErrorMessage(res);
            return { ok: false, totpRequired: message === TOTP_REQUIRED_DETAIL };
          }
          return { ok: false, totpRequired: false };
        }
        const pair = (await res.json()) as { access_token: string; refresh_token: string };
        setTokens(pair.access_token, pair.refresh_token);
        return { ok: true };
      } catch {
        return { ok: false, totpRequired: false };
      }
    })();
  }
  const result = await refreshInFlight;
  refreshInFlight = null;
  return result;
}

/** Pide el código 2FA una sola vez (deduplicado) cuando el refresh falla
 * puntualmente por el gate de TOTP; ver `lib/api.ts::tryRefreshWithTotpPrompt`. */
async function tryRefreshWithTotpPrompt(): Promise<boolean> {
  if (typeof window === "undefined") return false;
  if (!totpPromptInFlight) {
    totpPromptInFlight = (async () => {
      const code = window.prompt(
        "Tu sesión expiró. Ingresá tu código de verificación en dos pasos (2FA) para continuar:",
      );
      if (!code || !code.trim()) return false;
      const result = await tryRefresh(code.trim());
      return result.ok;
    })();
  }
  const result = await totpPromptInFlight;
  totpPromptInFlight = null;
  return result;
}

async function authedFetch(path: string, init: RequestInit = {}): Promise<Response> {
  let res = await rawFetch(path, init);
  if (res.status === 401) {
    let result = await tryRefresh();
    if (!result.ok && result.totpRequired) {
      result = (await tryRefreshWithTotpPrompt()) ? { ok: true } : { ok: false, totpRequired: false };
    }
    if (result.ok) res = await rawFetch(path, init);
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
