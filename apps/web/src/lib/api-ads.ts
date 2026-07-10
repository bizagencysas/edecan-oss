/**
 * Cliente HTTP de `/v1/ads/*` (Ads: Meta Marketing API oficial, bring-your-own,
 * `ARCHITECTURE.md` §13, WP-V4-07; `apps/api/edecan_api/routers/ads.py`;
 * ver `docs/ads.md` para el flujo completo del guardrail de dinero).
 *
 * Vertical slice propio (ROADMAP_V2.md §7.10): `lib/api.ts` es compartido y no
 * se edita, así que este archivo importa de ahí lo que ya expone
 * (`API_BASE_URL`, `ApiError`) y calca su manejo de autenticación (Bearer +
 * reintento tras refrescar en 401, incluido el gate de TOTP) para el resto —
 * mismo patrón EXACTO documentado en `HOTFIXES_PENDIENTES.md` punto 2 y ya
 * duplicado a propósito en `lib/api-misiones.ts`/`api-remoto.ts`/
 * `api-perfil.ts`/`api-ide.ts`/`api-negocios.ts`/`api-automatizaciones.ts`/
 * `api-reuniones.ts`/`api-mcp.ts` (ver el docstring de cualquiera de esos
 * archivos para el mismo razonamiento completo).
 *
 * Única diferencia deliberada con `lib/api.ts`: el dedupe de refresh
 * concurrente (`refreshInFlight`/`totpPromptInFlight`) vive en variables de
 * módulo LOCALES, no compartidas con `lib/api.ts` — mismo trade-off ya
 * documentado en los archivos de arriba (en el peor caso, un 401 acá y otro
 * en `lib/api.ts` exactamente al mismo tiempo dispara dos
 * `POST /v1/auth/refresh` en vez de uno solo deduplicado; ambos funcionan
 * igual, no es un bug de corrección).
 */

import { API_BASE_URL, ApiError } from "./api";
import { clearTokens, getAccessToken, getRefreshToken, setTokens } from "./tokens";
import type { TokenPair } from "./types";

// ---------------------------------------------------------------------------
// Tipos (espejan `edecan_api.routers.ads` — `AdsCredentialsIn`/`AdsStatusOut`
// y las filas de `ad_drafts`, `ARCHITECTURE.md` §13.b)
// ---------------------------------------------------------------------------

/** Flag de plan que gatea TODO `/v1/ads/*` (`_require_tools_ads`,
 * `edecan_schemas.plans.FLAG_TOOLS_ADS = "tools.ads"`) — `False` únicamente
 * en `hosted_basic`. No hace falta chequearlo antes de renderizar la página:
 * un tenant sin el flag recibe un 403 con el mensaje exacto de Meta/plan, que
 * esta página ya muestra como cualquier otro error (mismo criterio que
 * `/app/viajes` con `tools.travel`). */
export const FLAG_TOOLS_ADS = "tools.ads";

export interface AdsStatus {
  configured: boolean;
  ad_account_id: string | null;
  nombre_cuenta: string | null;
  moneda: string | null;
  reachable: boolean | null;
}

export interface PutAdsCredentialsInput {
  access_token: string;
  ad_account_id: string;
  validate?: boolean;
}

/** Campaña tal cual la devuelve Meta (`GET /act_{id}/campaigns`) o el
 * `StubAdsProvider` offline — el router NO declara `response_model` para
 * `GET /resumen` (`dict[str, Any]` puro, ver `edecan_ads.providers.AdsProvider`),
 * así que este tipo se queda deliberadamente abierto (`[key: string]: unknown`)
 * en vez de fingir una forma más estricta de la que el backend garantiza. */
export interface AdCampana {
  id?: string;
  name?: string;
  status?: string;
  objective?: string;
  daily_budget?: string | number | null;
  [key: string]: unknown;
}

/** Métricas agregadas del período (`spend`/`impressions`/`clicks`/`cpc`/`ctr`)
 * — mismo criterio abierto que `AdCampana`. */
export interface AdsMetricas {
  spend?: string | number;
  impressions?: string | number;
  clicks?: string | number;
  cpc?: string | number;
  ctr?: string | number;
  date_preset?: string;
  [key: string]: unknown;
}

export interface AdsResumen {
  campanas: AdCampana[];
  metricas: AdsMetricas;
  periodo: string;
}

/** `status` de un `ad_draft` — CHECK exacto de la migración `0006_v4_expansion`
 * (`ARCHITECTURE.md` §13.b): nace SIEMPRE `draft`, ninguna fila publica/gasta
 * nada real por sí sola. */
export type AdDraftStatus = "draft" | "confirmed" | "pushed" | "error" | "cancelled";

/** Únicos estados desde los que `POST /borradores/{id}/cancelar` acepta la
 * cancelación — espeja LITERAL `_ESTADOS_CANCELABLES` en
 * `apps/api/edecan_api/routers/ads.py` (frozenset `{"draft", "confirmed", "error"}`).
 * `"pushed"` queda deliberadamente fuera: esa campaña ya existe en Meta, se
 * gestiona desde su Ads Manager, no desde acá. */
export const ESTADOS_CANCELABLES: readonly AdDraftStatus[] = ["draft", "confirmed", "error"];

/** Único estado desde el que `POST /borradores/{id}/confirmar` no devuelve
 * `409` (`if draft["status"] != "draft": raise 409`, mismo archivo). */
export const ESTADO_CONFIRMABLE: AdDraftStatus = "draft";

export interface AdDraft {
  id: string;
  tenant_id: string;
  user_id: string;
  provider: string;
  nombre: string;
  objetivo: string;
  presupuesto_diario: string | number | null;
  moneda: string;
  payload: Record<string, unknown>;
  status: AdDraftStatus;
  external_id: string | null;
  error: string | null;
  confirmed_at: string | null;
  pushed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface AdDraftAccionResultado {
  borrador: AdDraft | null;
  mensaje: string;
}

// ---------------------------------------------------------------------------
// Fetch autenticado con refresh-on-401 (calca lib/api.ts, ver docstring)
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// PUT/DELETE /v1/ads/credentials, GET /v1/ads/status
// ---------------------------------------------------------------------------

export async function getAdsStatus(): Promise<AdsStatus> {
  return apiJson<AdsStatus>("/v1/ads/status");
}

export async function putAdsCredentials(input: PutAdsCredentialsInput): Promise<void> {
  await apiJson<void>("/v1/ads/credentials", {
    method: "PUT",
    body: { ...input, validate: input.validate ?? true },
  });
}

export async function deleteAdsCredentials(): Promise<void> {
  await apiJson<void>("/v1/ads/credentials", { method: "DELETE" });
}

// ---------------------------------------------------------------------------
// GET /v1/ads/resumen
// ---------------------------------------------------------------------------

export async function getAdsResumen(periodo = "last_30d"): Promise<AdsResumen> {
  return apiJson<AdsResumen>(`/v1/ads/resumen?periodo=${encodeURIComponent(periodo)}`);
}

// ---------------------------------------------------------------------------
// Borradores (`ad_drafts`)
// ---------------------------------------------------------------------------

export async function listAdDrafts(): Promise<AdDraft[]> {
  return apiJson<AdDraft[]>("/v1/ads/borradores");
}

/** `POST /borradores/{id}/confirmar` — SIEMPRE precedido, en la UI, de un
 * modal de doble confirmación (mismo criterio que `/app/ordenes`, ver
 * `page.tsx`). Empuja la campaña a Meta **SIEMPRE en pausa** — nunca activa
 * gasto por su cuenta (`docs/ads.md`, guardrail de dinero). */
export async function confirmarAdDraft(id: string): Promise<AdDraftAccionResultado> {
  return apiJson<AdDraftAccionResultado>(`/v1/ads/borradores/${encodeURIComponent(id)}/confirmar`, {
    method: "POST",
  });
}

export async function cancelarAdDraft(id: string): Promise<AdDraftAccionResultado> {
  return apiJson<AdDraftAccionResultado>(`/v1/ads/borradores/${encodeURIComponent(id)}/cancelar`, {
    method: "POST",
  });
}

export { ApiError };
