/**
 * Cliente HTTP de `apps/api/edecan_api/routers/automations.py` (`/v1/automations/*`,
 * `ROADMAP_V2.md` §7.6/§7.10, dueño WP-V2-07).
 *
 * `lib/api.ts` es compartido y no se toca (`ROADMAP_V2.md` §7.10): este
 * archivo importa de ahí solo lo que SÍ está exportado (`API_BASE_URL`,
 * `ApiError`) y replica localmente el mismo patrón de autenticación
 * (`Authorization: Bearer <access_token>` + un reintento tras refrescar en
 * 401) porque el resto de piezas de `api.ts` (`authedFetch`, `apiJson`, el
 * `refreshInFlight` que deduplica refrescos concurrentes) son privadas de
 * ese módulo — mismo patrón (y misma nota de duplicación deliberada) que
 * `lib/api-remoto.ts` (WP-V2-09).
 *
 * Tipos propios (`Automation`, `AutomationRun`) en vez de `lib/types.ts` por
 * el mismo motivo: ese archivo tampoco está en la lista de rutas que este
 * paquete de trabajo puede tocar.
 */

import { API_BASE_URL, ApiError } from "./api";
import { getAccessToken, getRefreshToken, setTokens } from "./tokens";

/** `edecan_schemas.plans.FLAG_AUTOMATIONS_RULES` (`ROADMAP_V2.md` §7.2). */
export const FLAG_AUTOMATIONS_RULES = "automations.rules";
/** `edecan_schemas.plans.LIMIT_AUTOMATIONS_ACTIVE` (`ROADMAP_V2.md` §7.2). */
export const LIMIT_AUTOMATIONS_ACTIVE = "limits.automations_active";

export interface ScheduleTrigger {
  kind: "schedule";
  rrule: string;
}

/** Redactado (ver `routers/automations.py::_public_automation`): nunca trae
 * `hook_secret` salvo en la respuesta puntual de `POST`/`PATCH` que lo generó
 * (ahí viaja aparte, en `Automation.hook_secret` — no en este objeto). */
export interface WebhookTriggerPublic {
  kind: "webhook";
  has_secret: boolean;
  hook_url: string;
}

export type AutomationTrigger = ScheduleTrigger | WebhookTriggerPublic;

export interface AutomationAccion {
  kind: "agent_instruction";
  instruccion: string;
  agente?: string | null;
}

export interface Automation {
  id: string;
  nombre: string;
  descripcion: string;
  trigger: AutomationTrigger;
  accion: AutomationAccion;
  enabled: boolean;
  next_run_at: string | null;
  last_run_at: string | null;
  created_at: string;
  updated_at: string;
  /** Solo presente en la respuesta del `POST`/`PATCH` que ACABA de generarlo. */
  hook_secret?: string;
}

export type AutomationRunStatus = "running" | "done" | "error" | "waiting_confirmation";

export interface AutomationRun {
  id: string;
  status: AutomationRunStatus | string;
  detalle: Record<string, unknown>;
  started_at: string | null;
  finished_at: string | null;
}

export interface AutomationTriggerIn {
  kind: "schedule" | "webhook";
  rrule?: string;
}

export interface AutomationAccionIn {
  instruccion: string;
  agente?: string;
}

export interface CreateAutomationInput {
  nombre: string;
  descripcion?: string;
  trigger: AutomationTriggerIn;
  accion: AutomationAccionIn;
  enabled?: boolean;
}

export interface UpdateAutomationInput {
  nombre?: string;
  descripcion?: string;
  trigger?: AutomationTriggerIn;
  accion?: AutomationAccionIn;
  enabled?: boolean;
}

// ---------------------------------------------------------------------------
// Auth (mismo patrón que `lib/api.ts`/`lib/api-remoto.ts`, ver docstring del módulo)
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
// Fetchers (`/v1/automations/*`, ver `apps/api/edecan_api/routers/automations.py`)
// ---------------------------------------------------------------------------

export async function listAutomations(): Promise<Automation[]> {
  return apiJson<Automation[]>("/v1/automations");
}

export async function getAutomation(automationId: string): Promise<Automation> {
  return apiJson<Automation>(`/v1/automations/${automationId}`);
}

export async function createAutomation(input: CreateAutomationInput): Promise<Automation> {
  return apiJson<Automation>("/v1/automations", { method: "POST", ...jsonBody(input) });
}

export async function updateAutomation(
  automationId: string,
  patch: UpdateAutomationInput,
): Promise<Automation> {
  return apiJson<Automation>(`/v1/automations/${automationId}`, {
    method: "PATCH",
    ...jsonBody(patch),
  });
}

export async function deleteAutomation(automationId: string): Promise<void> {
  return apiJson<void>(`/v1/automations/${automationId}`, { method: "DELETE" });
}

/** `POST /v1/automations/{id}/probar` — corre la automatización ya mismo,
 * sin esperar a su agenda ni a un webhook (funciona incluso si está desactivada). */
export async function probarAutomation(automationId: string): Promise<{ queued: boolean }> {
  return apiJson<{ queued: boolean }>(`/v1/automations/${automationId}/probar`, {
    method: "POST",
  });
}

export async function listAutomationRuns(automationId: string): Promise<AutomationRun[]> {
  return apiJson<AutomationRun[]>(`/v1/automations/${automationId}/runs`);
}

export { ApiError };
