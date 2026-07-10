/**
 * Cliente HTTP de `/v1/missions` (`edecan_api.routers.missions`, ROADMAP_V2.md
 * §7.4, §7.6, §7.9). Vertical slice propio (ROADMAP_V2.md §7.10): `lib/api.ts`
 * es compartido y no se edita, así que este archivo importa de ahí lo que ya
 * expone (`API_BASE_URL`, `ApiError`) y calca su manejo de autenticación
 * (Bearer + reintento tras refrescar en 401) para el resto.
 *
 * Única diferencia deliberada con `lib/api.ts`: el dedupe de refresh
 * concurrente (`refreshInFlight`) vive en una variable de módulo LOCAL, no
 * compartida con `lib/api.ts` — en el peor caso (un 401 en este archivo y
 * otro en `lib/api.ts` exactamente al mismo tiempo) se dispararían dos
 * `POST /v1/auth/refresh` en vez de uno solo deduplicado; ambos igual
 * funcionan (el segundo token sigue siendo válido), así que no es un bug de
 * corrección, solo una llamada de más en un caso límite raro.
 */

import { API_BASE_URL, ApiError } from "./api";
import { clearTokens, getAccessToken, getRefreshToken, setTokens } from "./tokens";
import type { TokenPair } from "./types";

// --- Tipos (espejan edecan_schemas.missions.MissionOut/MissionStepOut) -----

export type MissionStatus =
  | "planning"
  | "running"
  | "waiting_confirmation"
  | "done"
  | "error"
  | "cancelled";

export type MissionStepStatus =
  | "pending"
  | "running"
  | "waiting_confirmation"
  | "done"
  | "error"
  | "skipped";

export interface MissionPlanStep {
  seq: number;
  agente: string;
  instruccion: string;
}

export interface Mission {
  id: string;
  tenant_id: string;
  user_id: string;
  objetivo: string;
  status: MissionStatus;
  plan: MissionPlanStep[] | null;
  resultado: string | null;
  presupuesto: { max_steps?: number } & Record<string, unknown>;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface MissionStep {
  id: string;
  tenant_id: string;
  mission_id: string;
  seq: number;
  agente: string;
  instruccion: string;
  status: MissionStepStatus;
  resultado: string | null;
  usage: ({ pending_tool_call?: { id: string; name: string; args: Record<string, unknown> } } & Record<
    string,
    unknown
  >) | null;
  created_at: string;
  updated_at: string;
}

export interface MissionDetail {
  mission: Mission;
  steps: MissionStep[];
}

// --- Detalle enriquecido: GET /v1/missions/{id}/detalle (WP-V6-10) --------
// Espeja `edecan_api.routers.missions.MissionDetalleOut`/`MissionStepDetalleOut`/
// `MissionAgregadosOut`. Endpoint ADITIVO: `Mission`/`MissionStep`/`MissionDetail`
// y `getMission()` de arriba siguen intactos, sin usarse en la página (que
// ahora consume el detalle enriquecido) pero disponibles por si algo más los
// necesita — mismo criterio "aditivo, no rompe nada" que el backend.

/** Uso/timing de un paso — mismo shape que `edecan_agents.orchestrator`
 * persiste en `agent_steps.usage` (tokens de `edecan_llm.base.Usage` cuando
 * el paso terminó `done`; `pending_tool_call` mientras espera confirmación;
 * `started_at`/`finished_at` en cualquier guardado terminal desde WP-V6-10). */
export type MissionStepUsage =
  | ({
      pending_tool_call?: { id: string; name: string; args: Record<string, unknown> };
      started_at?: string;
      finished_at?: string;
    } & Record<string, unknown>)
  | null;

/** Paso enriquecido de `GET /v1/missions/{id}/detalle` (WP-V6-10) — mismos
 * campos base que `MissionStep` salvo que `resultado` llega recortado del
 * lado del servidor (`resultado_truncado`, cap ~2000 caracteres con sufijo) y
 * se agregan `started`/`finished` (extraídos de `usage`, `null` si el paso
 * corrió antes de este WP o todavía no terminó). */
export interface MissionStepDetalle {
  seq: number;
  agente: string;
  instruccion: string;
  status: MissionStepStatus;
  resultado_truncado: string | null;
  usage: MissionStepUsage;
  started: string | null;
  finished: string | null;
}

/** Totales calculados por el servidor sobre los pasos de la misión — ver
 * `edecan_api.routers.missions._calcular_agregados`. `pasos_por_status`
 * siempre trae las 6 claves de `MissionStepStatus` (en 0 si el status no
 * aparece entre los pasos de la misión). */
export interface MissionAgregados {
  tokens_totales_por_tipo: Record<string, number>;
  pasos_por_status: Record<string, number>;
}

/** `GET /v1/missions/{id}/detalle` (WP-V6-10): superset observabilidad de
 * `MissionDetail` — mismo `mission` (su `presupuesto` ya trae
 * `replans_usados` cuando `Orchestrator.run` replaneó al menos una vez, sin
 * campo nuevo inventado), pasos enriquecidos y agregados de tokens/status. */
export interface MissionDetalle {
  mission: Mission;
  steps: MissionStepDetalle[];
  agregados: MissionAgregados;
}

/** Igual a `edecan_agents.orchestrator.MAX_REPLANS_PER_MISSION` — duplicado
 * aquí como literal porque el backend nunca lo expone en la API (`missions.py`
 * declara explícito en su docstring "NUNCA importa edecan_agents"; ver
 * también `docs/agentes.md` sección "Observabilidad de misiones"), mismo
 * criterio que otros literales ya duplicados entre capas en este repo (p. ej.
 * `DEFAULT_MAX_STEPS`, en `orchestrator.py` y en `missions.py`). Si cambia en
 * el backend, hay que actualizar este valor también. */
export const MAX_REPLANS_PER_MISSION = 1;

/** Misiones aún en curso: la UI hace *polling* mientras el status esté en este conjunto. */
export const ACTIVE_MISSION_STATUSES: readonly MissionStatus[] = ["planning", "running"];

export const FLAG_AGENTS_MISSIONS = "agents.missions";

// --- Fetch autenticado con refresh-on-401 (calca lib/api.ts, ver docstring) -

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
      result = (await tryRefreshWithTotpPrompt()) ? { ok: true } : { ok: false, totpRequired: false };
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

// --- Fetchers ----------------------------------------------------------------

export async function listMissions(): Promise<Mission[]> {
  return apiJson<Mission[]>("/v1/missions");
}

export async function createMission(objetivo: string): Promise<Mission> {
  return apiJson<Mission>("/v1/missions", { method: "POST", body: { objetivo } });
}

export async function getMission(id: string): Promise<MissionDetail> {
  return apiJson<MissionDetail>(`/v1/missions/${id}`);
}

/** Detalle enriquecido (WP-V6-10): mismo dato base que `getMission`, más
 * `usage`/`started`/`finished` por paso y `agregados` de tokens/status. */
export async function getMissionDetalle(id: string): Promise<MissionDetalle> {
  return apiJson<MissionDetalle>(`/v1/missions/${id}/detalle`);
}

export async function confirmMission(id: string, approved: boolean): Promise<Mission> {
  return apiJson<Mission>(`/v1/missions/${id}/confirm`, { method: "POST", body: { approved } });
}

export async function cancelMission(id: string): Promise<Mission> {
  return apiJson<Mission>(`/v1/missions/${id}/cancel`, { method: "POST" });
}
