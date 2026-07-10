/**
 * Cliente HTTP de `apps/api/edecan_api/routers/erp.py` (`/v1/erp/*` — inventario / ERP
 * básico, `ARCHITECTURE.md` §13, WP-V4-06).
 *
 * Mismo motivo de duplicación que `lib/api-negocios.ts`/`lib/api-remoto.ts`/`lib/api-ide.ts`
 * (ver su docstring): `lib/api.ts` está fuera de la lista de archivos que este paquete de
 * trabajo puede tocar, y sus helpers de fetch autenticado (`authedFetch`/`apiJson`) son
 * privados de ese módulo — así que este archivo replica localmente el mismo patrón (Bearer +
 * un reintento tras refrescar el token en 401, incluido el gate de TOTP en `/v1/auth/refresh`
 * que documenta `HOTFIXES_PENDIENTES.md` punto 2). Tipos propios (`Producto`/
 * `ResumenInventario`/...) en vez de `lib/types.ts` por el mismo motivo.
 */

import { API_BASE_URL, ApiError } from "./api";
import { getAccessToken, getRefreshToken, setTokens } from "./tokens";

// ---------------------------------------------------------------------------
// Tipos (`edecan_business.inventory` / `routers/erp.py`)
// ---------------------------------------------------------------------------

export type MovimientoMotivo = "compra" | "venta" | "ajuste" | "merma" | "devolucion";

export interface Producto {
  id: string;
  sku: string;
  nombre: string;
  descripcion: string;
  unidad: string;
  // Columnas `numeric(14,2)`/`numeric(14,3)` con escala > 0: `fastapi.encoders.
  // jsonable_encoder` siempre las serializa como `number` (nunca `string`) — verificado
  // contra el encoder real, no asumido (a diferencia de `lib/api-negocios.ts`, que tipa sus
  // equivalentes como `string`).
  precio: number | null;
  costo: number | null;
  stock: number;
  stock_minimo: number;
  activo: boolean;
  created_at: string;
  updated_at: string;
}

export interface ProductoCreateInput {
  sku: string;
  nombre: string;
  descripcion?: string;
  unidad?: string;
  precio?: number | string | null;
  costo?: number | string | null;
  stock_minimo?: number | string;
}

/** `PATCH` parcial: solo se mandan las claves presentes (`edecan_business.inventory.
 * editar_producto` solo toca lo que viene en el cuerpo) — `activo: false` es la única vía
 * para desactivar un producto (no hay un `DELETE`/ruta dedicada, ver el docstring del
 * router). */
export interface ProductoUpdateInput {
  nombre?: string;
  descripcion?: string;
  unidad?: string;
  precio?: number | string | null;
  costo?: number | string | null;
  stock_minimo?: number | string;
  activo?: boolean;
}

export interface MovimientoInput {
  delta: number | string;
  motivo: MovimientoMotivo;
  nota?: string;
  ref?: string | null;
}

export interface StockMove {
  id: string;
  tenant_id: string;
  user_id: string;
  product_id: string;
  delta: number;
  motivo: MovimientoMotivo;
  nota: string;
  ref: string | null;
  created_at: string;
  updated_at: string;
}

/** `POST /productos/{id}/movimientos` devuelve el movimiento CON el producto ya actualizado
 * (mismo shape que `edecan_business.inventory.registrar_movimiento`) — evita un segundo
 * round-trip solo para refrescar el stock. */
export interface MovimientoResultado extends StockMove {
  producto: Producto;
}

export interface StockBajoItem {
  id: string;
  sku: string;
  nombre: string;
  stock: number;
  stock_minimo: number;
}

export interface ResumenInventario {
  total_skus: number;
  valor_costo: number;
  valor_precio: number;
  stock_bajo: StockBajoItem[];
}

// ---------------------------------------------------------------------------
// Auth (mismo patrón que `lib/api.ts` / `lib/api-negocios.ts`)
// ---------------------------------------------------------------------------

// `/v1/auth/refresh` exige `totp_code` si la cuenta tiene 2FA activo (mismo
// gate que `/login`, ver `auth.py::refresh`). Replica acá el manejo de
// `lib/api.ts::tryRefreshWithTotpPrompt` (HOTFIXES_PENDIENTES.md #2) para no
// forzar un logout duro cada ~30 min a usuarios con TOTP activo.
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
// Fetchers (`/v1/erp/*`)
// ---------------------------------------------------------------------------

export async function listProductos(params?: { activo?: boolean; q?: string }): Promise<Producto[]> {
  const qs = new URLSearchParams();
  if (params?.activo !== undefined) qs.set("activo", String(params.activo));
  if (params?.q) qs.set("q", params.q);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return apiJson<Producto[]>(`/v1/erp/productos${suffix}`);
}

export async function createProducto(input: ProductoCreateInput): Promise<Producto> {
  return apiJson<Producto>("/v1/erp/productos", { method: "POST", ...jsonBody(input) });
}

export async function updateProducto(id: string, input: ProductoUpdateInput): Promise<Producto> {
  return apiJson<Producto>(`/v1/erp/productos/${id}`, { method: "PATCH", ...jsonBody(input) });
}

/** Atajo sobre `updateProducto` — no hay una ruta HTTP dedicada de "desactivar" (ver el
 * docstring de `edecan_api.routers.erp`): `PATCH {"activo": false}` es la única vía. */
export async function desactivarProducto(id: string): Promise<Producto> {
  return updateProducto(id, { activo: false });
}

export async function registrarMovimiento(
  id: string,
  input: MovimientoInput,
): Promise<MovimientoResultado> {
  return apiJson<MovimientoResultado>(`/v1/erp/productos/${id}/movimientos`, {
    method: "POST",
    ...jsonBody(input),
  });
}

export async function getResumenInventario(): Promise<ResumenInventario> {
  return apiJson<ResumenInventario>("/v1/erp/resumen");
}

export { ApiError };
