/**
 * Cliente HTTP de `apps/api/edecan_api/routers/viajes.py` (`/v1/viajes/*` — vuelos y
 * hoteles mediante la capacidad nativa de Edecán (Kiwi/Trivago/Skiplagged,
 * sin API key) + rastreo de paquetes vía AfterShip. Las rutas históricas de
 * Amadeus se conservan únicamente por compatibilidad.
 *
 * Mismo motivo de duplicación que `lib/api-negocios.ts`/`lib/api-remoto.ts`/
 * `lib/api-ide.ts` (ver su docstring): `lib/api.ts` está fuera de la lista de archivos
 * que este paquete de trabajo puede tocar, y sus helpers de fetch autenticado
 * (`authedFetch`/`apiJson`) son privados de ese módulo — así que este archivo replica
 * localmente el mismo patrón (Bearer + un reintento tras refrescar el token en 401,
 * incluido el gate de TOTP). Tipos propios en vez de `lib/types.ts` por el mismo motivo.
 */

import { API_BASE_URL, ApiError } from "./api";
import { recoverSessionAfterUnauthorized, isRefreshResultCurrent } from "./session-refresh";
import { getAccessToken } from "./tokens";

// ---------------------------------------------------------------------------
// Tipos (`edecan_travel` / `routers/viajes.py`)
// ---------------------------------------------------------------------------

export type ViajesEnvironment = "test" | "production";

export interface ViajesCredentialsInput {
  api_key: string;
  api_secret: string;
  environment?: ViajesEnvironment;
  validate?: boolean;
}

export interface RastreoCredentialsInput {
  api_key: string;
  validate?: boolean;
}

export interface ViajesStatus {
  travel: { configured: boolean; environment: ViajesEnvironment | null };
  tracking: { configured: boolean };
}

export interface VueloOferta {
  id: string;
  aerolinea: string;
  salida: string | null;
  llegada: string | null;
  origen: string | null;
  destino: string | null;
  escalas: number;
  precio_total: string;
  moneda: string;
  booking_url?: string | null;
}

export interface HotelOferta {
  id: string;
  nombre: string;
  rating: string | null;
  precio_total: string;
  moneda: string;
  checkin: string | null;
  checkout: string | null;
  booking_url?: string | null;
}

export interface CheckpointRastreo {
  fecha: string | null;
  mensaje: string;
  lugar: string | null;
}

export interface RastreoPaqueteOut {
  estado: string;
  courier: string | null;
  checkpoints: CheckpointRastreo[];
  entrega_estimada: string | null;
}

export interface BuscarVuelosInput {
  origen: string;
  destino: string;
  fecha: string;
  adultos?: number;
  max_resultados?: number;
}

export interface BuscarHotelesInput {
  ciudad: string;
  checkin: string;
  checkout: string;
  adultos?: number;
}

// ---------------------------------------------------------------------------
// Auth (mismo patrón que `lib/api.ts` / `lib/api-negocios.ts`)
// ---------------------------------------------------------------------------

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
  if (Array.isArray(raw)) {
    const message = raw
      .map((item) =>
        typeof item === "object" && item && "msg" in item
          ? String((item as { msg: unknown }).msg)
          : String(item),
      )
      .join(" · ");
    return { message: message || `Error HTTP ${res.status}`, detail };
  }
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

function queryString(params: Record<string, string | number | undefined>): string {
  const usp = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") usp.set(key, String(value));
  }
  const s = usp.toString();
  return s ? `?${s}` : "";
}

// ---------------------------------------------------------------------------
// Credenciales heredadas de Amadeus + AfterShip. Vuelos/hoteles ya no
// requieren credenciales; estas rutas permanecen para instalaciones viejas.
// ---------------------------------------------------------------------------

export async function putViajesCredentials(input: ViajesCredentialsInput): Promise<void> {
  await apiJson<void>("/v1/viajes/credentials", { method: "PUT", ...jsonBody(input) });
}

export async function deleteViajesCredentials(): Promise<void> {
  await apiJson<void>("/v1/viajes/credentials", { method: "DELETE" });
}

export async function putRastreoCredentials(input: RastreoCredentialsInput): Promise<void> {
  await apiJson<void>("/v1/viajes/rastreo/credentials", { method: "PUT", ...jsonBody(input) });
}

export async function deleteRastreoCredentials(): Promise<void> {
  await apiJson<void>("/v1/viajes/rastreo/credentials", { method: "DELETE" });
}

export async function getViajesStatus(): Promise<ViajesStatus> {
  return apiJson<ViajesStatus>("/v1/viajes/status");
}

// ---------------------------------------------------------------------------
// Búsqueda / rastreo — proxies de solo lectura.
// ---------------------------------------------------------------------------

export async function buscarVuelos(input: BuscarVuelosInput): Promise<{ ofertas: VueloOferta[] }> {
  const qs = queryString({
    origen: input.origen,
    destino: input.destino,
    fecha: input.fecha,
    adultos: input.adultos,
    max_resultados: input.max_resultados,
  });
  return apiJson<{ ofertas: VueloOferta[] }>(`/v1/viajes/buscar/vuelos${qs}`);
}

export async function buscarHoteles(input: BuscarHotelesInput): Promise<{ ofertas: HotelOferta[] }> {
  const qs = queryString({
    ciudad: input.ciudad,
    checkin: input.checkin,
    checkout: input.checkout,
    adultos: input.adultos,
  });
  return apiJson<{ ofertas: HotelOferta[] }>(`/v1/viajes/buscar/hoteles${qs}`);
}

export async function rastrearPaquete(
  numero: string,
  courierSlug?: string,
): Promise<RastreoPaqueteOut> {
  const qs = queryString({ courier_slug: courierSlug });
  return apiJson<RastreoPaqueteOut>(`/v1/viajes/rastreo/${encodeURIComponent(numero)}${qs}`);
}

export { ApiError };
