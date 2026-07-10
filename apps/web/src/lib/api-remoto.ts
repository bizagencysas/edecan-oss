/**
 * Cliente HTTP de `apps/api/edecan_api/routers/remote.py` (`/v1/remote/*`):
 * vista remota (`ROADMAP_V2.md` §5 WP-V2-09) + control remoto de teclado/
 * mouse, `kind="control"` (WP-V4-10, "fase 2" — ver `docs/control-remoto.md`).
 *
 * `lib/api.ts` es compartido y no se toca (`ROADMAP_V2.md` §7.10): este
 * archivo importa de ahí solo lo que SÍ está exportado (`API_BASE_URL`,
 * `ApiError`) y replica localmente el mismo patrón de autenticación
 * (`Authorization: Bearer <access_token>` + un reintento tras refrescar en
 * 401) porque el resto de las piezas de `api.ts` (`authedFetch`, `apiJson`,
 * el `refreshInFlight` que deduplica refrescos concurrentes) son privadas de
 * ese módulo. Es una duplicación pequeña y deliberada: la alternativa —
 * exportarlas desde `api.ts` — significaría tocar un archivo que este
 * paquete de trabajo tiene prohibido modificar. Si dos pestañas/hooks llegan
 * a refrescar el token en el mismo instante (uno desde `api.ts`, otro desde
 * aquí) el resultado sigue siendo correcto (ambos piden un par nuevo válido
 * y lo guardan), solo se pierde el dedupe entre los dos módulos — no hay
 * riesgo de corrupción de sesión.
 *
 * Tipos propios (`RemoteSession`, `RemoteFrame`) en vez de `lib/types.ts`
 * por el mismo motivo: ese archivo tampoco está en la lista de rutas que
 * este paquete de trabajo puede tocar.
 */

import { API_BASE_URL, ApiError } from "./api";
import { getAccessToken, getRefreshToken, setTokens } from "./tokens";

/** `edecan_schemas.plans.FLAG_COMPANION_REMOTE_VIEW` (`ROADMAP_V2.md` §7.2). */
export const FLAG_COMPANION_REMOTE_VIEW = "companion.remote_view";
/** `edecan_schemas.plans.FLAG_COMPANION_REMOTE_INPUT` (WP-V4-10, `ARCHITECTURE.md` §13). */
export const FLAG_COMPANION_REMOTE_INPUT = "companion.remote_input";

export type RemoteSessionKind = "view" | "control";

export interface RemoteSession {
  id: string;
  tenant_id: string;
  user_id: string;
  device_id: string | null;
  /** "view" (default) o "control" (WP-V4-10) — ver `docs/control-remoto.md`. */
  kind: RemoteSessionKind | string;
  status: "pending" | "active" | "ended" | "denied" | string;
  started_at: string | null;
  ended_at: string | null;
  frames_count: number;
  created_at: string;
  updated_at: string;
}

export interface RemoteFrame {
  /** PNG codificado en base64, listo para `data:image/png;base64,${image_b64}`. */
  image_b64: string;
  width: number;
  height: number;
  /** Copia de `frames_count` de la sesión al momento de este frame. */
  seq: number;
}

// ---------------------------------------------------------------------------
// Input remoto (WP-V4-10) — mismo vocabulario EXACTO que
// `edecan_api.routers.remote.PointerAccion`/`MouseButton`/`SpecialKey` y
// `edecan_companion.actions._POINTER_ACTIONS`/`_MOUSE_BUTTONS`/`_SPECIAL_KEYS`.
// ---------------------------------------------------------------------------

export type PointerAccion = "move" | "click" | "double_click" | "right_click";
export type MouseButton = "left" | "right" | "middle";
export type SpecialKey =
  | "enter"
  | "tab"
  | "escape"
  | "backspace"
  | "arrow_up"
  | "arrow_down"
  | "arrow_left"
  | "arrow_right";

export interface PointerInputPayload {
  tipo: "pointer";
  x: number;
  y: number;
  accion: PointerAccion;
  button?: MouseButton;
}

/** Exactamente uno de `texto`/`tecla` — el backend (`KeyInputIn`) rechaza con
 * 422 si vienen ambos o ninguno; estos dos tipos lo reflejan en TypeScript. */
export type KeyInputPayload =
  | { tipo: "key"; texto: string; tecla?: undefined }
  | { tipo: "key"; tecla: SpecialKey; texto?: undefined };

export type RemoteInputPayload = PointerInputPayload | KeyInputPayload;

export interface RemoteInputResult {
  ok: true;
  result: Record<string, unknown> | null;
}

// ---------------------------------------------------------------------------
// Auth (mismo patrón que `lib/api.ts`, ver docstring del módulo)
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
// Fetchers (`/v1/remote/*`, ver `apps/api/edecan_api/routers/remote.py`)
// ---------------------------------------------------------------------------

/**
 * `POST /v1/remote/sessions` — `consent` debe ser exactamente `true` (422 si
 * no). `kind` (WP-V4-10, default `"view"` — sin cambios de comportamiento
 * para quien no lo pase) exige además el flag `companion.remote_input` si es
 * `"control"` (403 si no, ver `FLAG_COMPANION_REMOTE_INPUT`).
 */
export async function createRemoteSession(
  consent: true,
  kind: RemoteSessionKind = "view",
): Promise<RemoteSession> {
  return apiJson<RemoteSession>("/v1/remote/sessions", {
    method: "POST",
    ...jsonBody({ consent, kind }),
  });
}

export async function listRemoteSessions(): Promise<RemoteSession[]> {
  return apiJson<RemoteSession[]>("/v1/remote/sessions");
}

export async function getRemoteSession(sessionId: string): Promise<RemoteSession> {
  return apiJson<RemoteSession>(`/v1/remote/sessions/${sessionId}`);
}

/**
 * `GET /v1/remote/sessions/{id}/frame` — puede devolver `429` (pediste un
 * frame antes de que pasara `REMOTE_FRAME_MIN_INTERVAL_SECONDS`), `501` (el
 * companion todavía no soporta capturar pantalla), `403` (el usuario lo
 * denegó en el companion, o la sesión ya estaba `denied`) o `409` (la sesión
 * ya `ended`) además de los errores genéricos — todos llegan como `ApiError`
 * con `.status` y un `.message` en español listo para mostrar.
 */
export async function getRemoteFrame(sessionId: string): Promise<RemoteFrame> {
  return apiJson<RemoteFrame>(`/v1/remote/sessions/${sessionId}/frame`);
}

export async function endRemoteSession(sessionId: string): Promise<RemoteSession> {
  return apiJson<RemoteSession>(`/v1/remote/sessions/${sessionId}/end`, { method: "POST" });
}

/**
 * `POST /v1/remote/sessions/{id}/input` (WP-V4-10) — solo para sesiones
 * `kind="control"` ya `active`. Códigos de error propios además de los
 * genéricos: `403` (sesión no es de control / el usuario denegó el comando
 * en su companion), `409` (sesión todavía no activa, o ya terminó), `429`
 * (rate limit propio, mucho más laxo que el de frames), `501` (el companion
 * no soporta o tiene deshabilitado el control remoto, o corre en una
 * plataforma sin soporte), `502` (otra falla del companion — p. ej. falta el
 * permiso de Accesibilidad), `503` (companion no conectado o sin respuesta).
 */
export async function sendRemoteInput(
  sessionId: string,
  payload: RemoteInputPayload,
): Promise<RemoteInputResult> {
  return apiJson<RemoteInputResult>(`/v1/remote/sessions/${sessionId}/input`, {
    method: "POST",
    ...jsonBody(payload),
  });
}

export { ApiError };
