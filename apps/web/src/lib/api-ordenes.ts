/**
 * Fetchers de `/v1/commerce/*` (ARCHITECTURE.md §10.14 "vertical slice", ROADMAP_V2.md
 * §7.10 y §7 WP-V2-10). Usa el mismo coordinador de rotación que el resto de
 * clientes para que una expiración normal no expulse al usuario de Órdenes.
 *
 * Ver `apps/api/edecan_api/routers/commerce.py` para el contrato exacto — en particular:
 * NO existe un `POST /v1/commerce/orders` (crear una orden es exclusivo de las tools del
 * agente, `preparar_pago`/`preparar_orden`, nunca de un formulario web directo — doble gate,
 * ver `docs/dinero-real.md`). Esta página solo lista/confirma/cancela lo que el chat ya
 * dejó como borrador, y gestiona presupuestos.
 */

import { API_BASE_URL, ApiError } from "./api";
import { recoverSessionAfterUnauthorized, isRefreshResultCurrent } from "./session-refresh";
import { getAccessToken, hasSession } from "./tokens";

// --- Tipos -------------------------------------------------------------------------

export type OrdenKind = "payment" | "purchase" | "trade";
export type OrdenStatus = "draft" | "confirmed" | "executed_paper" | "cancelled" | "expired";

export interface OrdenCotizacion {
  precio: number;
  moneda: string;
  fuente: string;
  ts?: string;
}

export interface OrdenMeta {
  beneficiario?: string | null;
  cotizacion?: OrdenCotizacion;
  payment_link?: string;
  error?: string;
  ejecucion?: {
    precio: number;
    monto_total: number;
    holding_cantidad: number;
    holding_costo_promedio: number;
  };
  [key: string]: unknown;
}

export interface Orden {
  id: string;
  tenant_id: string;
  user_id: string;
  kind: OrdenKind;
  status: OrdenStatus;
  descripcion: string;
  monto: string | number | null;
  moneda: string;
  simbolo: string | null;
  lado: "buy" | "sell" | null;
  cantidad: string | number | null;
  meta: OrdenMeta;
  confirmed_at: string | null;
  executed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface OrdenAccionResultado {
  order: Orden;
  mensaje: string;
}

export interface Holding {
  id: string;
  simbolo: string;
  cantidad: string | number;
  costo_promedio: string | number;
  moneda: string;
  kind: string;
}

export interface PresupuestoEstado {
  id: string;
  categoria: string;
  monto_mensual: string | number;
  moneda: string;
  gastado: string | number;
  pct: number;
  alerta: boolean;
  mes: string;
}

// --- Bajo nivel: fetch autenticado (mismo coordinador que `lib/api.ts`) ------------------

function redirectToLogin(): void {
  if (typeof window === "undefined" || hasSession()) return;
  if (window.location.pathname !== "/login") {
    window.location.assign("/login/");
  }
}

async function authedFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const request = async (): Promise<Response> => {
    const headers = new Headers(init.headers);
    const token = getAccessToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    return fetch(`${API_BASE_URL}${path}`, { ...init, headers });
  };

  let res = await request();
  if (res.status !== 401) return res;

  const result = await recoverSessionAfterUnauthorized(API_BASE_URL);
  if (isRefreshResultCurrent(result)) {
    res = await request();
  } else if (!result.ok && result.reason === "invalid") {
    redirectToLogin();
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
  if (!text) return undefined as T;
  return JSON.parse(text) as T;
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

// --- Órdenes -------------------------------------------------------------------------

export async function listOrdenes(status?: OrdenStatus): Promise<Orden[]> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  return apiJson<Orden[]>(`/v1/commerce/orders${qs}`);
}

export async function getOrden(id: string): Promise<Orden> {
  return apiJson<Orden>(`/v1/commerce/orders/${id}`);
}

/** `POST /orders/{id}/confirm` — SIEMPRE precedido, en la UI, del modal de doble
 * confirmación (ver `page.tsx`). No mueve dinero real: ejecuta en modo paper (trade) o
 * genera un enlace de pago placeholder que el humano debe abrir y aprobar (payment). */
export async function confirmOrden(id: string): Promise<OrdenAccionResultado> {
  return apiJson<OrdenAccionResultado>(`/v1/commerce/orders/${id}/confirm`, { method: "POST" });
}

export async function cancelOrden(id: string): Promise<OrdenAccionResultado> {
  return apiJson<OrdenAccionResultado>(`/v1/commerce/orders/${id}/cancel`, { method: "POST" });
}

// --- Holdings (paper, solo lectura) -------------------------------------------------------

export async function listHoldings(): Promise<Holding[]> {
  return apiJson<Holding[]>("/v1/commerce/holdings");
}

// --- Presupuestos ------------------------------------------------------------------------

export async function listPresupuestos(): Promise<PresupuestoEstado[]> {
  return apiJson<PresupuestoEstado[]>("/v1/commerce/budgets");
}

export async function fijarPresupuesto(input: {
  categoria: string;
  monto_mensual: number;
  moneda?: string;
}): Promise<{ id: string; categoria: string; monto_mensual: string | number; moneda: string }> {
  return apiJson("/v1/commerce/budgets", { method: "PUT", body: input });
}
