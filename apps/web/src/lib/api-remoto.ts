/**
 * Cliente HTTP de `apps/api/edecan_api/routers/remote.py` (`/v1/remote/*`):
 * vista remota (`ROADMAP_V2.md` §5 WP-V2-09) + control remoto de teclado/
 * mouse, `kind="control"` (WP-V4-10, "fase 2" — ver `docs/control-remoto.md`).
 *
 * `lib/api.ts` es compartido y no se toca (`ROADMAP_V2.md` §7.10): este
 * archivo importa de ahí solo lo que SÍ está exportado (`API_BASE_URL`,
 * `ApiError`) y replica localmente el mismo patrón de autenticación
 * (`Authorization: Bearer <access_token>` + un reintento tras refrescar en
 * 401) porque `authedFetch`/`apiJson` siguen siendo privados. La rotación sí
 * usa `session-refresh`, compartido con todos los vertical slices: el
 * backend consume cada refresh token una sola vez y no admite carreras.
 *
 * Tipos propios (`RemoteSession`, `RemoteFrame`) en vez de `lib/types.ts`
 * por el mismo motivo: ese archivo tampoco está en la lista de rutas que
 * este paquete de trabajo puede tocar.
 */

import { API_BASE_URL, ApiError } from "./api";
import { recoverSessionAfterUnauthorized, isRefreshResultCurrent } from "./session-refresh";
import { getAccessToken } from "./tokens";

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
  /** PNG/JPEG/WebP codificado en base64. */
  image_b64: string;
  width: number;
  height: number;
  mime?: "image/png" | "image/jpeg" | "image/webp";
  origin_x?: number;
  origin_y?: number;
  /** Copia de `frames_count` de la sesión al momento de este frame. */
  seq: number;
  /** Modo observado por el companion para esta captura. */
  capture_mode?:
    | "screen_capture_kit_stream_probe_ready"
    | "screen_capture_kit_ready"
    | "screencapture_fallback"
    | string;
}

// ---------------------------------------------------------------------------
// Input remoto (WP-V4-10) — mismo vocabulario EXACTO que
// `edecan_api.routers.remote.PointerAccion`/`MouseButton`/`SpecialKey` y
// `edecan_companion.actions._POINTER_ACTIONS`/`_MOUSE_BUTTONS`/`_SPECIAL_KEYS`.
// ---------------------------------------------------------------------------

export type PointerAccion =
  | "move" | "click" | "double_click" | "right_click"
  | "mouse_down" | "mouse_up" | "drag" | "scroll";
export type MouseButton = "left" | "right" | "middle";
export type SpecialKey =
  | "enter"
  | "tab"
  | "escape"
  | "backspace"
  | "arrow_up"
  | "arrow_down"
  | "arrow_left"
  | "arrow_right"
  | "delete_forward"
  | "home" | "end" | "page_up" | "page_down" | "space"
  | "a" | "c" | "v" | "x" | "z" | "s";
export type KeyModifier = "command" | "control" | "option" | "shift";

export interface PointerInputPayload {
  tipo: "pointer";
  x: number;
  y: number;
  accion: PointerAccion;
  button?: MouseButton;
  start_x?: number;
  start_y?: number;
  delta_x?: number;
  delta_y?: number;
}

/** Exactamente uno de `texto`/`tecla` — el backend (`KeyInputIn`) rechaza con
 * 422 si vienen ambos o ninguno; estos dos tipos lo reflejan en TypeScript. */
export type KeyInputPayload =
  | { tipo: "key"; texto: string; tecla?: undefined }
  | { tipo: "key"; tecla: SpecialKey; texto?: undefined; modifiers?: KeyModifier[] };

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

export interface RemoteStreamClient {
  close(): void;
}

/** Backoff determinista y acotado para cortes transitorios del visor. */
export function remoteStreamRetryDelay(attempt: number): number {
  const normalized = Number.isFinite(attempt)
    ? Math.max(0, Math.floor(attempt))
    : 0;
  return Math.min(5000, 250 * 2 ** Math.min(normalized, 5));
}

/** Abre el stream persistente de frames; el bearer viaja en el primer frame. */
export function openRemoteStream(
  sessionId: string,
  onFrame: (frame: RemoteFrame) => void,
  onError: (error: Error) => void,
): RemoteStreamClient {
  const base = new URL(API_BASE_URL);
  base.protocol = base.protocol === "https:" ? "wss:" : "ws:";
  let closedByCaller = false;
  let socket: WebSocket | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  let retryAttempt = 0;
  const connect = () => {
    if (closedByCaller) return;
    socket = new WebSocket(`${base.origin}/v1/remote/sessions/${sessionId}/stream`);
    socket.onopen = () => {
      const token = getAccessToken();
      socket?.send(JSON.stringify({ type: "authenticate", token }));
    };
    socket.onmessage = (event) => {
      try {
        const message = JSON.parse(String(event.data)) as { type?: string; frame?: RemoteFrame };
        // El servidor envía `ready` después de autenticar. No reiniciamos el
        // backoff en `onopen`: un socket que abre y muere durante el handshake
        // no es una conexión sana y, de lo contrario, produciría un loop de
        // reconexiones cada 250 ms. Un `ready` o un frame sí prueba progreso.
        if (message.type === "ready") retryAttempt = 0;
        if (message.type === "frame") {
          retryAttempt = 0;
          onFrame(message as RemoteFrame);
        }
        if (message.type === "error") onError(new Error("El stream remoto devolvió un error."));
      } catch {
        onError(new Error("El stream remoto devolvió un frame inválido."));
      }
    };
    socket.onerror = () => {
      // `onclose` decide si reintenta; no mostramos un error transitorio aquí.
    };
    socket.onclose = (event) => {
      socket = null;
      if (closedByCaller || event.code === 1000) return;
      if ([4401, 4403, 4404, 4409].includes(event.code)) {
        onError(new Error(`El stream remoto se cerró (${event.code}).`));
        return;
      }
      const delay = remoteStreamRetryDelay(retryAttempt);
      retryAttempt += 1;
      retryTimer = setTimeout(connect, delay);
    };
  };
  connect();
  return {
    close() {
      closedByCaller = true;
      if (retryTimer !== null) clearTimeout(retryTimer);
      if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
        socket.close(1000, "viewer closed");
      }
    },
  };
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
