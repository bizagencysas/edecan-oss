/**
 * Cliente HTTP de `apps/api/edecan_api/routers/rrhh.py` (`/v1/rrhh/*` — RRHH: empleados,
 * ausencias y nómina en borrador, `ARCHITECTURE.md` §14, WP-V5-08).
 *
 * Mismo motivo de duplicación que `lib/api-negocios.ts`/`lib/api-inventario.ts`/
 * `lib/api-remoto.ts`/`lib/api-ide.ts` (ver su docstring): `lib/api.ts` está fuera de la lista
 * de archivos que este paquete de trabajo puede tocar, y sus helpers de fetch autenticado
 * (`authedFetch`/`apiJson`) son privados de ese módulo — así que este archivo replica
 * localmente el mismo patrón (Bearer + un reintento tras refrescar el token en 401, incluido
 * el gate de TOTP en `/v1/auth/refresh` que documenta `HOTFIXES_PENDIENTES.md` punto 2).
 * Tipos propios (`Empleado`/`Ausencia`/`Nomina`/...) en vez de `lib/types.ts` por el mismo
 * motivo.
 */

import { API_BASE_URL, ApiError } from "./api";
import { getAccessToken, getRefreshToken, setTokens } from "./tokens";

// ---------------------------------------------------------------------------
// Tipos (`edecan_business.rrhh` / `routers/rrhh.py`)
// ---------------------------------------------------------------------------

export type EmpleadoStatus = "active" | "inactive";
export type AusenciaKind = "vacaciones" | "enfermedad" | "permiso" | "otro";
export type AusenciaStatus = "pending" | "approved" | "rejected" | "cancelled";
export type NominaStatus = "draft" | "approved" | "paid" | "cancelled";

export interface Empleado {
  id: string;
  nombre: string;
  email: string | null;
  puesto: string;
  // Columnas `numeric(14,2)` sin `response_model` de Pydantic: `fastapi.encoders.
  // jsonable_encoder` las serializa como `number` (verificado contra el encoder real) —
  // mismo criterio que `lib/api-inventario.ts`, a diferencia de `lib/api-negocios.ts`.
  salario_mensual: number | null;
  moneda: string;
  fecha_ingreso: string | null;
  status: EmpleadoStatus;
  created_at: string;
  updated_at: string;
}

export interface EmpleadoCreateInput {
  nombre: string;
  email?: string | null;
  puesto?: string;
  salario_mensual?: number | string | null;
  moneda?: string;
  fecha_ingreso?: string | null;
  status?: EmpleadoStatus;
}

/** `PATCH` parcial: solo se mandan las claves presentes (`edecan_business.rrhh.
 * editar_empleado` solo toca lo que viene en el cuerpo). */
export interface EmpleadoUpdateInput {
  nombre?: string;
  email?: string | null;
  puesto?: string;
  salario_mensual?: number | string | null;
  moneda?: string;
  fecha_ingreso?: string | null;
  status?: EmpleadoStatus;
}

export interface Ausencia {
  id: string;
  employee_id: string;
  kind: AusenciaKind;
  desde: string;
  hasta: string;
  status: AusenciaStatus;
  notas: string;
  created_at: string;
  updated_at: string;
}

export interface AusenciaCreateInput {
  employee_id: string;
  kind: AusenciaKind;
  desde: string;
  hasta: string;
  notas?: string;
}

export interface PayrollItem {
  id: string;
  payroll_run_id: string;
  employee_id: string;
  empleado_nombre: string;
  bruto: number;
  deducciones: number;
  neto: number;
}

export interface Nomina {
  id: string;
  periodo: string;
  status: NominaStatus;
  // Única columna persistida (el NETO de la corrida) — ver `docs/rrhh.md`.
  total: number;
  moneda: string;
  notas: string;
  approved_at: string | null;
  created_at: string;
  updated_at: string;
  // Calculados (nunca persistidos) por `edecan_business.rrhh`: solo vienen en la respuesta
  // de `POST /nominas` y `GET /nominas/{id}`, no en `GET /nominas` (listado sin items).
  total_bruto?: number;
  total_deducciones?: number;
  items?: PayrollItem[];
}

export interface NominaCreateInput {
  periodo: string;
  deducciones_pct?: number | string | null;
  moneda?: string;
  notas?: string;
}

export interface NominaAccionResultado {
  nomina: Nomina;
  mensaje: string;
}

// ---------------------------------------------------------------------------
// Auth (mismo patrón que `lib/api.ts` / `lib/api-inventario.ts`)
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
// Fetchers — Empleados
// ---------------------------------------------------------------------------

export async function listEmpleados(params?: {
  status?: EmpleadoStatus;
  q?: string;
}): Promise<Empleado[]> {
  const qs = new URLSearchParams();
  if (params?.status) qs.set("status", params.status);
  if (params?.q) qs.set("q", params.q);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return apiJson<Empleado[]>(`/v1/rrhh/empleados${suffix}`);
}

export async function createEmpleado(input: EmpleadoCreateInput): Promise<Empleado> {
  return apiJson<Empleado>("/v1/rrhh/empleados", { method: "POST", ...jsonBody(input) });
}

export async function updateEmpleado(id: string, input: EmpleadoUpdateInput): Promise<Empleado> {
  return apiJson<Empleado>(`/v1/rrhh/empleados/${id}`, { method: "PATCH", ...jsonBody(input) });
}

/** Atajo sobre `updateEmpleado` — no hay una ruta HTTP dedicada de "desactivar/reactivar"
 * (mismo criterio que `desactivarProducto` en `lib/api-inventario.ts`): `PATCH
 * {"status": "..."}` es la única vía. */
export async function setEmpleadoStatus(id: string, status: EmpleadoStatus): Promise<Empleado> {
  return updateEmpleado(id, { status });
}

// ---------------------------------------------------------------------------
// Fetchers — Ausencias
// ---------------------------------------------------------------------------

export async function listAusencias(params?: {
  employee_id?: string;
  status?: AusenciaStatus;
}): Promise<Ausencia[]> {
  const qs = new URLSearchParams();
  if (params?.employee_id) qs.set("employee_id", params.employee_id);
  if (params?.status) qs.set("status", params.status);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return apiJson<Ausencia[]>(`/v1/rrhh/ausencias${suffix}`);
}

export async function createAusencia(input: AusenciaCreateInput): Promise<Ausencia> {
  return apiJson<Ausencia>("/v1/rrhh/ausencias", { method: "POST", ...jsonBody(input) });
}

export async function resolverAusencia(
  id: string,
  accion: "aprobar" | "rechazar",
): Promise<Ausencia> {
  return apiJson<Ausencia>(`/v1/rrhh/ausencias/${id}`, {
    method: "PATCH",
    ...jsonBody({ accion }),
  });
}

// ---------------------------------------------------------------------------
// Fetchers — Nómina
// ---------------------------------------------------------------------------

export async function listNominas(params?: { status?: NominaStatus }): Promise<Nomina[]> {
  const qs = params?.status ? `?status=${encodeURIComponent(params.status)}` : "";
  return apiJson<Nomina[]>(`/v1/rrhh/nominas${qs}`);
}

/** Genera el borrador completo (`calcular_nomina`) — NO mueve dinero, ver `docs/rrhh.md`. */
export async function createNomina(input: NominaCreateInput): Promise<Nomina> {
  return apiJson<Nomina>("/v1/rrhh/nominas", { method: "POST", ...jsonBody(input) });
}

export async function getNomina(id: string): Promise<Nomina> {
  return apiJson<Nomina>(`/v1/rrhh/nominas/${id}`);
}

/** Exige confirmación explícita — el backend responde `400` sin `{"confirmar": true}` (ver
 * `docs/rrhh.md`, guardrail de dinero). Esto NO mueve dinero real: solo registra la nómina
 * como aprobada; el pago lo hace la persona usuaria por su propio medio. */
export async function aprobarNomina(id: string): Promise<NominaAccionResultado> {
  return apiJson<NominaAccionResultado>(`/v1/rrhh/nominas/${id}/aprobar`, {
    method: "POST",
    ...jsonBody({ confirmar: true }),
  });
}

export async function cancelarNomina(id: string): Promise<NominaAccionResultado> {
  return apiJson<NominaAccionResultado>(`/v1/rrhh/nominas/${id}/cancelar`, { method: "POST" });
}

export { ApiError };
