/**
 * Cliente HTTP de `apps/api/edecan_api/routers/negocios.py` (`/v1/negocios/*` —
 * facturación ligera + KPIs de negocio, `ROADMAP_V2.md` §5 P1 WP-V2-12).
 *
 * Mismo motivo de duplicación que `lib/api-remoto.ts`/`lib/api-ide.ts` (ver su docstring):
 * `lib/api.ts` está fuera de la lista de archivos que este paquete de trabajo puede tocar,
 * y sus helpers de fetch autenticado (`authedFetch`/`apiJson`) son privados de ese módulo —
 * así que este archivo replica localmente el mismo patrón (Bearer + un reintento tras
 * refrescar el token en 401). Tipos propios (`Invoice`/`NegociosKpis`/...) en vez de
 * `lib/types.ts` por el mismo motivo.
 */

import { API_BASE_URL, ApiError } from "./api";
import { recoverSessionAfterUnauthorized, isRefreshResultCurrent } from "./session-refresh";
import { getAccessToken } from "./tokens";

// ---------------------------------------------------------------------------
// Tipos (`edecan_business` / `routers/negocios.py`)
// ---------------------------------------------------------------------------

export type InvoiceStatus = "draft" | "sent" | "paid" | "void";

export interface InvoiceItem {
  id: string;
  descripcion: string;
  cantidad: string;
  precio_unitario: string;
  total: string;
}

export interface Invoice {
  id: string;
  numero: string;
  cliente_nombre: string;
  cliente_email: string | null;
  moneda: string;
  subtotal: string;
  impuestos: string;
  total: string;
  status: InvoiceStatus;
  due_date: string | null;
  pdf_file_id: string | null;
  notas: string;
  created_at: string;
  updated_at: string;
  items?: InvoiceItem[];
  file_id?: string;
  filename?: string;
}

export interface CanalVenta {
  canal: string;
  total: number;
}

export interface ActividadItem {
  tipo: "factura" | "transaccion";
  id: string;
  fecha: string;
  descripcion: string;
  monto: number;
  moneda: string;
  status: InvoiceStatus | null;
}

export interface NegociosKpis {
  mes: string;
  ingresos: number;
  gastos: number;
  beneficio: number;
  nuevos_clientes: number;
  facturado: number;
  cobrado: number;
  por_canal: CanalVenta[];
  actividad: ActividadItem[];
}

export interface InvoiceItemInput {
  descripcion: string;
  cantidad: number | string;
  precio_unitario: number | string;
}

export interface InvoiceCreateInput {
  cliente_nombre: string;
  items: InvoiceItemInput[];
  impuestos_pct?: number | string;
  due_date?: string | null;
  cliente_email?: string | null;
  notas?: string;
  moneda?: string;
}

// ---------------------------------------------------------------------------
// Auth (mismo patrón que `lib/api.ts` / `lib/api-remoto.ts`)
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

// ---------------------------------------------------------------------------
// Fetchers (`/v1/negocios/*`)
// ---------------------------------------------------------------------------

export async function getNegociosKpis(mes?: string): Promise<NegociosKpis> {
  const qs = mes ? `?mes=${encodeURIComponent(mes)}` : "";
  return apiJson<NegociosKpis>(`/v1/negocios/kpis${qs}`);
}

export async function listFacturas(status?: InvoiceStatus): Promise<Invoice[]> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  return apiJson<Invoice[]>(`/v1/negocios/facturas${qs}`);
}

export async function createFactura(input: InvoiceCreateInput): Promise<Invoice> {
  return apiJson<Invoice>("/v1/negocios/facturas", { method: "POST", ...jsonBody(input) });
}

export async function getFactura(id: string): Promise<Invoice> {
  return apiJson<Invoice>(`/v1/negocios/facturas/${id}`);
}

/** `POST /v1/negocios/facturas/{id}/estado` — `draft` nunca es un destino válido. */
export async function setFacturaEstado(
  id: string,
  status: Exclude<InvoiceStatus, "draft">,
): Promise<Invoice> {
  return apiJson<Invoice>(`/v1/negocios/facturas/${id}/estado`, {
    method: "POST",
    ...jsonBody({ status }),
  });
}

export { ApiError };
