/**
 * Cliente HTTP de `GET|PUT /v1/ide/agents/{session_id}/modo`
 * (`apps/api/edecan_api/routers/ide.py`), el control de "modo de trabajo" y
 * "esfuerzo de razonamiento" del agente del IDE.
 *
 * Archivo propio en vez de sumar esto a `lib/api-ide.ts` porque ese archivo
 * lo está tocando en paralelo el agente del plan (mismo cierre de IDE, tres
 * paquetes de trabajo en vuelo a la vez) -- ver nota en `SelectorModo.tsx`.
 * Replica el mismo patrón de auth que `api-ide.ts` (`Authorization: Bearer
 * <access_token>` + reintento tras refrescar en 401) porque `authedFetch`/
 * `apiJson` de ese archivo siguen siendo privados y no exportados.
 */

import { API_BASE_URL, ApiError } from "./api";
import { recoverSessionAfterUnauthorized, isRefreshResultCurrent } from "./session-refresh";
import { getAccessToken, getDesktopCapability } from "./tokens";

export { ApiError };

/**
 * Los tres niveles reales que acepta el backend, en el orden en que se
 * presentan ("más rápido" -> "más inteligente"). El backend además manda su
 * propia lista en `niveles` -- se usa para filtrar acá (`nivelesValidos`) en
 * vez de asumir que estos tres siempre están disponibles.
 */
export const ESFUERZO_NIVELES = ["bajo", "medio", "alto"] as const;

export type IdeEsfuerzo = (typeof ESFUERZO_NIVELES)[number];

export const ESFUERZO_ETIQUETA: Record<IdeEsfuerzo, string> = {
  bajo: "Bajo",
  medio: "Medio",
  alto: "Alto",
};

/**
 * Los cuatro modos del agente (`ide_modos.ModoAgente`, espejo exacto de
 * cadena en `ide_opencode_permisos.ModoAgente` -- ver el docstring de ese
 * módulo). Cada uno gatea DE VERDAD las herramientas nativas del turno
 * (`ide_workers_agent.WorkersIDEAgent._execute`, tabla `_TABLA_DECISION`) y,
 * bajo opencode, además decide el agente real que corre la sesión ("plan"
 * con `edit: deny`, o "build" -- `SessionManager.set_modo_agente` cambia el
 * agente EN VIVO, sin esperar al próximo turno).
 */
export const MODOS_AGENTE = ["manual", "aceptar_ediciones", "plan", "auto"] as const;

export type IdeModoAgente = (typeof MODOS_AGENTE)[number];

function esModoAgenteValido(value: string): value is IdeModoAgente {
  return (MODOS_AGENTE as readonly string[]).includes(value);
}

/** Mismo criterio que `nivelesValidos`: filtra a lo que el backend dice tener
 * (`modos_disponibles` de `GET .../modo`) en vez de asumir que los cuatro
 * siempre están ahí -- si mañana uno se retira del lado del companion, la
 * interfaz dejaría de ofrecerlo sola, no seguiría prometiéndolo. */
export function modosAgenteValidos(modos: string[] | null | undefined): IdeModoAgente[] {
  if (!modos || modos.length === 0) return [...MODOS_AGENTE];
  const permitidos = new Set(modos.filter(esModoAgenteValido));
  const filtrados = MODOS_AGENTE.filter((modo) => permitidos.has(modo));
  return filtrados.length > 0 ? filtrados : [...MODOS_AGENTE];
}

export interface IdeModo {
  /** Interruptor del motor VIEJO ("no toques archivos, solo propón" --
   * `ide_modos.ModoPlanificacionStore`). Se sigue leyendo del backend porque
   * `GET .../modo` lo manda, pero `ModoPlanificacionStore.verificar_accion_permitida`
   * no lo llama ningún despachador (verificado: no hay una sola invocación en
   * todo `apps/companion`) -- no frena nada por sí solo. El menú de Modo de
   * `SelectorModo.tsx` NO se ata a este campo; usa `modo` (abajo), que sí es
   * el que de verdad alimenta el gate de herramientas y, bajo opencode, el
   * `PuenteDePermisos`. */
  modo_plan: boolean;
  esfuerzo: IdeEsfuerzo;
  reasoning_effort: string;
  niveles: string[];
  /** El modo real -- ver `MODOS_AGENTE`. */
  modo: IdeModoAgente;
  /** Motivo opcional que la persona dejó al fijar el modo, o `null`. */
  modo_motivo: string | null;
  /** Los modos que el backend dice tener disponibles ahora mismo. */
  modos_disponibles: string[];
}

export interface IdeModoUpdate {
  esfuerzo?: IdeEsfuerzo;
  modo_plan?: boolean;
  /** Fija el modo real (`ide_runtime.py::ide_modo_set`, campo `modo`). */
  modo?: IdeModoAgente;
  motivo?: string;
}

function esEsfuerzoValido(value: string): value is IdeEsfuerzo {
  return (ESFUERZO_NIVELES as readonly string[]).includes(value);
}

/**
 * Filtra `ESFUERZO_NIVELES` a los que el backend realmente ofrece (campo
 * `niveles` de `GET .../modo`), preservando el orden "rápido -> inteligente".
 * Si el backend no mandó nada reconocible, se cae a los tres de siempre --
 * son los únicos que existen hoy en el motor, no un invento de la interfaz.
 */
export function nivelesValidos(niveles: string[] | null | undefined): IdeEsfuerzo[] {
  if (!niveles || niveles.length === 0) return [...ESFUERZO_NIVELES];
  const permitidos = new Set(niveles.filter(esEsfuerzoValido));
  const filtrados = ESFUERZO_NIVELES.filter((nivel) => permitidos.has(nivel));
  return filtrados.length > 0 ? filtrados : [...ESFUERZO_NIVELES];
}

// ---------------------------------------------------------------------------
// Auth (mismo patrón que `lib/api-ide.ts`, ver docstring del archivo)
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

// ---------------------------------------------------------------------------
// Fetchers
// ---------------------------------------------------------------------------

export async function getIdeModo(sessionId: string): Promise<IdeModo> {
  return apiJson<IdeModo>(`/v1/ide/agents/${encodeURIComponent(sessionId)}/modo`);
}

export async function putIdeModo(sessionId: string, update: IdeModoUpdate): Promise<IdeModo> {
  return apiJson<IdeModo>(`/v1/ide/agents/${encodeURIComponent(sessionId)}/modo`, {
    method: "PUT",
    body: JSON.stringify(update),
  });
}
