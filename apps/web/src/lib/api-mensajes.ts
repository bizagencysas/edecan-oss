/**
 * Cliente HTTP de `/v1/mensajes` (`edecan_api.routers.mensajes`, `ARCHITECTURE.md` §13,
 * WP-V4-11). Vertical slice propio (mismo criterio que `lib/api-skills.ts`, ver su
 * docstring): `lib/api.ts` es compartido y no se edita, así que este archivo importa de ahí
 * `API_BASE_URL`/`ApiError` y calca su manejo de autenticación (Bearer + reintento tras
 * refrescar en 401, incluido el prompt de TOTP si la cuenta lo tiene activado) — con dedupe
 * global en `session-refresh`, compartido con todos los demás vertical slices.
 *
 * Ver `docs/mensajeria.md` sección "Bandeja unificada (web)" para el contrato completo
 * (asimetrías por canal, formato de `fecha`, bring-your-own).
 */

import { API_BASE_URL, ApiError } from "./api";
import { recoverSessionAfterUnauthorized, isRefreshResultCurrent } from "./session-refresh";
import { getAccessToken, hasSession } from "./tokens";

// --- Tipos (espejan edecan_api.routers.mensajes) -------------------------------

/** Flag de plan que gatea todo este router — mismo string que `enviar_mensaje`/
 * `leer_mensajes` (`edecan_schemas.plans.FLAG_CONNECTORS_MESSAGING`). */
export const FLAG_CONNECTORS_MESSAGING = "connectors.messaging";

export const CANALES_MENSAJERIA = ["telegram", "discord", "slack", "whatsapp"] as const;
export type CanalMensajeria = (typeof CANALES_MENSAJERIA)[number];

export interface CanalEstado {
  canal: CanalMensajeria;
  conectado: boolean;
  /** `false` únicamente para `"whatsapp"` — la Cloud API de Meta no soporta lectura en v3
   * (requiere webhooks entrantes con URL pública), ver `docs/mensajeria.md`. */
  puede_leer: boolean;
}

export interface MensajeItem {
  canal: CanalMensajeria;
  remitente: string;
  texto: string;
  /** Formato crudo, DISTINTO por canal a propósito — ver `docs/mensajeria.md`
   * "Formato de fecha". Nunca reinterpretado del lado del servidor. */
  fecha: string;
  chat_id: string;
}

export interface EnviarMensajeResultado {
  canal: CanalMensajeria;
  destinatario: string;
  resultado: Record<string, unknown>;
}

// --- Fetch autenticado con refresh-on-401 (calca lib/api-skills.ts, ver docstring) --------

async function rawFetch(path: string, init: RequestInit): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = getAccessToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return fetch(`${API_BASE_URL}${path}`, { ...init, headers });
}

function redirectToLogin(): void {
  if (typeof window === "undefined" || hasSession()) return;
  if (window.location.pathname !== "/login") {
    window.location.assign("/login");
  }
}

async function authedFetch(path: string, init: RequestInit = {}): Promise<Response> {
  let res = await rawFetch(path, init);
  if (res.status === 401) {
    const result = await recoverSessionAfterUnauthorized(API_BASE_URL);
    if (isRefreshResultCurrent(result)) {
      res = await rawFetch(path, init);
    } else if (!result.ok && result.reason === "invalid") {
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

// --- Fetchers --------------------------------------------------------------------

/** `GET /v1/mensajes/canales` — estado de las 4 plataformas para el tenant actual. */
export async function listCanales(): Promise<CanalEstado[]> {
  return apiJson<CanalEstado[]>("/v1/mensajes/canales");
}

/** `GET /v1/mensajes` — últimos mensajes de un canal ya conectado. `origen` es obligatorio
 * salvo en Telegram (ver `docs/mensajeria.md`). */
export async function listMensajes(params: {
  canal: CanalMensajeria;
  origen?: string;
  limite?: number;
}): Promise<MensajeItem[]> {
  const qs = new URLSearchParams({ canal: params.canal });
  if (params.origen) qs.set("origen", params.origen);
  if (params.limite) qs.set("limite", String(params.limite));
  return apiJson<MensajeItem[]>(`/v1/mensajes?${qs.toString()}`);
}

/** `POST /v1/mensajes/enviar` — envía un mensaje real vía el cliente oficial del canal. */
export async function enviarMensaje(input: {
  canal: CanalMensajeria;
  destinatario: string;
  texto: string;
}): Promise<EnviarMensajeResultado> {
  return apiJson<EnviarMensajeResultado>("/v1/mensajes/enviar", { method: "POST", body: input });
}

// Re-exporta `ApiError` para sus propios consumidores (`components/mensajes/*`, que la
// importan desde este módulo en vez de `@/lib/api` directo) — mismo patrón que
// `api-skills.ts`/`api-automatizaciones.ts`/`api-ide.ts`/`api-remoto.ts`/`api-negocios.ts`/
// `api-perfil.ts`/`api-misiones.ts`/`api-configuracion.ts`.
export { ApiError };
