/**
 * Cliente HTTP de `apps/api/edecan_api/routers/perfil.py` (`/v1/perfil/*`,
 * perfil vivo — `ROADMAP_V2.md` §21/§7.4 WP-V2-13).
 *
 * `lib/api.ts` es compartido y no se toca (`ROADMAP_V2.md` §7.10): este
 * archivo importa de ahí solo lo que SÍ está exportado (`API_BASE_URL`,
 * `ApiError`) y replica localmente el mismo patrón de autenticación
 * (`Authorization: Bearer <access_token>` + un reintento tras refrescar en
 * 401) porque `authedFetch`/`apiJson` siguen siendo privados. La rotación sí
 * usa `session-refresh`, compartido con todos los vertical slices. Tipos
 * propios (`PerfilVivo`, `DatosPerfil`)
 * en vez de `lib/types.ts` por el mismo motivo: ese archivo tampoco está en
 * la lista de rutas que este paquete de trabajo puede tocar.
 */

import { API_BASE_URL, ApiError } from "./api";
import { recoverSessionAfterUnauthorized, isRefreshResultCurrent } from "./session-refresh";
import { getAccessToken } from "./tokens";

export interface IdentidadPerfil {
  nombre_preferido: string;
  nombre_completo: string;
  pronombres: string;
  fecha_nacimiento: string;
  pais: string;
  ciudad: string;
  zona_horaria: string;
  ocupacion: string;
  idioma_preferido: string;
  forma_de_trato: string;
  biografia: string;
}

/** Identidad declarada más las seis categorías que Edecán aprende. */
export interface DatosPerfil {
  identidad: IdentidadPerfil;
  gustos: string[];
  proyectos: string[];
  metas: string[];
  relaciones: string[];
  empresas: string[];
  habitos: string[];
}

export type CategoriaPerfil = Exclude<keyof DatosPerfil, "identidad">;

export const CATEGORIAS_PERFIL: Array<{ campo: CategoriaPerfil; label: string }> = [
  { campo: "gustos", label: "Gustos" },
  { campo: "proyectos", label: "Proyectos" },
  { campo: "metas", label: "Metas" },
  { campo: "relaciones", label: "Relaciones" },
  { campo: "empresas", label: "Empresas" },
  { campo: "habitos", label: "Hábitos" },
];

export interface PerfilVivo {
  resumen: string;
  datos: DatosPerfil;
  /** `0` = todavía no existe (esqueleto vacío, `GET` sin fila en `user_profiles`). */
  version: number;
  updated_at: string | null;
}

export interface RebuildRespuesta {
  job_id: string;
  mensaje: string;
}

// ---------------------------------------------------------------------------
// Auth (mismo patrón que `lib/api.ts`/`lib/api-remoto.ts`, ver docstring del módulo)
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
      .map((item) => (typeof item === "object" && item && "msg" in item ? String(item.msg) : String(item)))
      .join(" · ");
    return { message: message || `Error HTTP ${res.status}`, detail };
  }
  return { message: `Error HTTP ${res.status}`, detail };
}

async function apiJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (typeof init.body === "string") {
    headers.set("Content-Type", "application/json");
  }
  const res = await authedFetch(path, { ...init, headers });
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
// Fetchers (`/v1/perfil/*`, ver `apps/api/edecan_api/routers/perfil.py`)
// ---------------------------------------------------------------------------

export async function getPerfilVivo(): Promise<PerfilVivo> {
  return apiJson<PerfilVivo>("/v1/perfil");
}

export interface PerfilPatch {
  resumen?: string;
  datos?: Partial<Omit<DatosPerfil, "identidad">> & { identidad?: Partial<IdentidadPerfil> };
}

/** `PUT /v1/perfil` — patch parcial: solo lo que mandes se sobreescribe; en
 * `datos`, cada categoría también es opcional (mandas la lista COMPLETA de
 * esa categoría, no un solo item — el add/remove de un chip lo arma el
 * caller antes de llamar esta función, ver `perfil-vivo/page.tsx`). */
export async function updatePerfilVivo(patch: PerfilPatch): Promise<PerfilVivo> {
  return apiJson<PerfilVivo>("/v1/perfil", { method: "PUT", ...jsonBody(patch) });
}

export async function deletePerfilVivo(): Promise<void> {
  await apiJson<void>("/v1/perfil", { method: "DELETE" });
}

/** `POST /v1/perfil/rebuild` — encola `memory_consolidate`; el resultado no
 * es inmediato, ver `docs/perfil-vivo.md`. */
export async function rebuildPerfilVivo(): Promise<RebuildRespuesta> {
  return apiJson<RebuildRespuesta>("/v1/perfil/rebuild", { method: "POST" });
}

export { ApiError };
