/**
 * Cliente HTTP de `/v1/mcp/*` (MCP bring-your-own, ARCHITECTURE.md §15,
 * WP-V6-07; `apps/api/edecan_api/routers/mcp.py`).
 *
 * Vertical slice propio (ROADMAP_V2.md §7.10): `lib/api.ts` es compartido y
 * no se edita, así que este archivo calca su manejo de autenticación
 * (Bearer + reintento tras refrescar en 401 + el prompt de TOTP cuando el
 * refresh silencioso lo exige) en vez de importarlo — mismo criterio que
 * `lib/api-configuracion.ts`/`lib/api-misiones.ts` (ver el docstring de
 * cabecera de `api-configuracion.ts` para el mismo razonamiento completo,
 * incluido por qué usa `??` en vez de `||` para `API_BASE_URL`).
 *
 * `getMcpServers` es tolerante a `404` (mismo criterio que las lecturas de
 * `api-configuracion.ts`): mientras el linchpin de v6 no haya montado
 * `edecan_api.routers.mcp` todavía, esa ruta no existe — la pantalla de
 * Configuración no debe verse rota por eso, se trata como "sin servidores
 * MCP todavía". Las escrituras (`PUT`/`DELETE`) sí dejan que un 404 se
 * muestre como error normal.
 */

import { ApiError } from "./api";
import { clearTokens, getAccessToken, getRefreshToken, setTokens } from "./tokens";
import type { TokenPair } from "./types";

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/+$/, "") ?? "http://localhost:8000";

export type MCPTransporte = "http" | "stdio";

export interface MCPServerOut {
  nombre: string;
  transporte: MCPTransporte | string;
  url: string | null;
  comando: string | null;
  estado: string;
}

/** Forma exacta de `PUT /v1/mcp/servers` (`MCPServerIn`). */
export interface PutMCPServerInput {
  nombre: string;
  transporte: MCPTransporte;
  url?: string;
  comando?: string;
  headers?: Record<string, string>;
  validate?: boolean;
}

export interface MCPToolOut {
  name: string;
  description: string;
}

export interface MCPToolsOut {
  tools: MCPToolOut[];
}

function isNotFound(err: unknown): boolean {
  return err instanceof ApiError && err.status === 404;
}

// --- Fetch autenticado con refresh-on-401 (calca lib/api.ts, ver docstring) -

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
        const pair = (await res.json()) as TokenPair;
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

function redirectToLogin(): void {
  if (typeof window === "undefined") return;
  clearTokens();
  if (window.location.pathname !== "/login") {
    window.location.assign("/login");
  }
}

async function authedFetch(path: string, init: RequestInit = {}): Promise<Response> {
  let res = await rawFetch(path, init);
  if (res.status === 401) {
    let result = await tryRefresh();
    if (!result.ok && result.totpRequired) {
      result = (await tryRefreshWithTotpPrompt())
        ? { ok: true }
        : { ok: false, totpRequired: false };
    }
    if (result.ok) {
      res = await rawFetch(path, init);
    } else {
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
      .map((item) =>
        typeof item === "object" && item && "msg" in item ? String(item.msg) : String(item),
      )
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

// --- Lecturas ------------------------------------------------------------

/** `GET /v1/mcp/servers` — lista `provider_config` de cada servidor, SIN
 * headers/secretos (el propio backend nunca los incluye en la respuesta). */
export async function getMcpServers(): Promise<MCPServerOut[]> {
  try {
    return await apiJson<MCPServerOut[]>("/v1/mcp/servers");
  } catch (err) {
    if (isNotFound(err)) return [];
    throw err;
  }
}

/** `GET /v1/mcp/servers/{nombre}/tools` — conecta en vivo y lista las tools
 * que expone ese servidor. Lanza `ApiError` con el detalle exacto si no se
 * pudo conectar. */
export async function getMcpServerTools(nombre: string): Promise<MCPToolsOut> {
  return await apiJson<MCPToolsOut>(`/v1/mcp/servers/${encodeURIComponent(nombre)}/tools`);
}

// --- Escrituras ------------------------------------------------------------

/** `PUT /v1/mcp/servers` → 204, o lanza `ApiError` con el detalle exacto
 * (handshake MCP rechazado, SSRF, stdio fuera de modo local, etc.) cuando
 * `validate` (default `true`) falla — "pegar y validar",
 * `DIRECCION_ACTUAL.md`. */
export async function putMcpServer(input: PutMCPServerInput): Promise<void> {
  await apiJson<void>("/v1/mcp/servers", { method: "PUT", body: input });
}

/** `DELETE /v1/mcp/servers/{nombre}` → 204 (idempotente). */
export async function deleteMcpServer(nombre: string): Promise<void> {
  await apiJson<void>(`/v1/mcp/servers/${encodeURIComponent(nombre)}`, { method: "DELETE" });
}
