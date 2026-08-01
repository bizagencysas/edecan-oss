/**
 * Cliente HTTP de `apps/api/edecan_api/routers/preparacion.py` (`/v1/preparacion`,
 * pantalla de preparación de requisitos de Windows).
 *
 * Mismo criterio que `lib/api-ide.ts` (que este archivo imita a propósito):
 * `authedFetch`/`apiJson` se replican localmente en vez de importarse de un
 * archivo compartido -- son privados incluso en `api-ide.ts`, y duplicar
 * ~20 líneas es más barato que acoplar dos vertical slices distintos.
 */

import { API_BASE_URL, ApiError } from "./api";
import { recoverSessionAfterUnauthorized, isRefreshResultCurrent } from "./session-refresh";
import { getAccessToken, getDesktopCapability } from "./tokens";

// ---------------------------------------------------------------------------
// Tipos (forma real de `apps/api/edecan_api/routers/preparacion.py`, que a su
// vez reenvía tal cual lo que arma `edecan_companion.preparacion`)
// ---------------------------------------------------------------------------

export type EstadoRequisito = "cumplido" | "falta" | "desconocido";

export interface RequisitoPreparacion {
  id: string;
  nombre: string;
  por_que: string;
  estado: EstadoRequisito;
  /** `false` = no hay comando de CLI que lo resuelva (p. ej. la versión de Windows). */
  instalable: boolean;
  requiere_admin: boolean;
  /** Si falta, ¿la app no puede funcionar (`true`) o solo se degrada (`false`)? */
  obligatorio: boolean;
}

export interface EstadoPreparacion {
  requisitos: RequisitoPreparacion[];
  /** Si ESTE proceso corre con permisos de administrador ahora mismo. */
  elevado: boolean;
}

export type EstadoInstalacion = "no_iniciado" | "ejecutando" | "completado" | "error";

export interface PreparacionInstalacionOut {
  id: string;
  estado: EstadoInstalacion;
  error: string | null;
}

export interface PreparacionEvento {
  cursor: number;
  type: string;
  text: string;
  timestamp: string;
}

export interface PreparacionLectura extends PreparacionInstalacionOut {
  events: PreparacionEvento[];
  next_cursor: number;
  has_more: boolean;
}

// ---------------------------------------------------------------------------
// Transporte -- copia local de `api-ide.ts::rawFetch/authedFetch/apiJson`.
// ---------------------------------------------------------------------------

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
  const res = await authedFetch(path, init);
  if (!res.ok) {
    const { message, detail } = await extractErrorMessage(res);
    throw new ApiError(res.status, message, detail);
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

// ---------------------------------------------------------------------------
// Fetchers
// ---------------------------------------------------------------------------

/** `GET /v1/preparacion` -- estado de cada requisito, sin instalar nada. */
export async function getPreparacion(): Promise<EstadoPreparacion> {
  return apiJson<EstadoPreparacion>("/v1/preparacion");
}

/** `POST /v1/preparacion/{id}/instalar` -- arranca (o reporta la ya en curso) UNA instalación. */
export async function postPreparacionInstalar(
  requisitoId: string,
): Promise<PreparacionInstalacionOut> {
  return apiJson<PreparacionInstalacionOut>(
    `/v1/preparacion/${encodeURIComponent(requisitoId)}/instalar`,
    { method: "POST" },
  );
}

/** `GET /v1/preparacion/{id}?cursor=` -- salida incremental de una instalación. */
export async function readPreparacion(
  requisitoId: string,
  cursor = 0,
): Promise<PreparacionLectura> {
  return apiJson<PreparacionLectura>(
    `/v1/preparacion/${encodeURIComponent(requisitoId)}?cursor=${Math.max(0, cursor)}`,
  );
}
