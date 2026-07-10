/**
 * Cliente HTTP de `apps/api/edecan_api/routers/voz_avanzada.py` (`/v1/voz/*` —
 * voces del tenant, clonación autorizada, y podcasts — WP-V5-10 + WP-V6-04).
 * Ver el docstring de ese router para el contrato completo.
 *
 * Mismo motivo de duplicación que `lib/api-negocios.ts`/`lib/api-inventario.ts`
 * (ver su docstring): `lib/api.ts` está fuera de la lista de archivos que este
 * paquete de trabajo puede tocar, y sus helpers de fetch autenticado
 * (`authedFetch`/`apiJson`) son privados de ese módulo — así que este archivo
 * replica localmente el mismo patrón (Bearer + un reintento tras refrescar el
 * token en 401, incluido el gate de TOTP en `/v1/auth/refresh` que documenta
 * `HOTFIXES_PENDIENTES.md` punto 2). Tipos propios (`VozDisponible`/`VozClon`/
 * ...) en vez de `lib/types.ts` por el mismo motivo.
 *
 * ## Descarga de un podcast `done` (WP-V6-04) — límite conocido, documentado
 *
 * `getFile` calca `lib/api.ts::getFile` (`GET /v1/files/{id}`) tal como pide
 * el paquete de trabajo — pero, igual que en `/app/archivos`, esa ruta SOLO
 * devuelve metadata (`filename`/`mime`/`size_bytes`/...), nunca los bytes del
 * archivo: `apps/api/edecan_api/routers/files.py` no expone ningún endpoint
 * de descarga real (ni URL prefirmada de S3 ni streaming) hoy, para NINGÚN
 * archivo del producto — ni siquiera los que genera `crear_documento`/
 * `crear_presentacion`/`crear_pdf`/`generar_imagen`, que tienen exactamente
 * la misma limitación y tampoco tienen un botón de descarga en ningún lado.
 * `apps/api/edecan_api/routers/files.py` está fuera de las rutas que este
 * paquete de trabajo puede tocar, así que `PodcastsTab` (`components/voz/`)
 * usa `getFile` para confirmar que el archivo existe y navega a `/app/archivos`
 * (la página que SÍ lista el archivo, con el mismo `listFiles`/`getFile` que
 * usa hoy) en vez de inventar un mecanismo de descarga nuevo.
 */

import { API_BASE_URL, ApiError } from "./api";
import { getAccessToken, getRefreshToken, setTokens } from "./tokens";

// ---------------------------------------------------------------------------
// Tipos (`edecan_voice.cloning` / `routers/voz_avanzada.py`)
// ---------------------------------------------------------------------------

export type VozCategoria = "premade" | "cloned" | "generated" | "professional";

export interface VozDisponible {
  voice_id: string;
  nombre: string;
  categoria: VozCategoria | string;
  preview_url: string | null;
}

export type VozClonStatus = "attested" | "revoked";

export interface VozClon {
  id: string;
  voice_name: string;
  attestation: boolean;
  status: VozClonStatus;
  consent_file_id: string;
  provider_voice_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface CrearClonInput {
  nombre: string;
  /** Debe venir de un checkbox marcado explícitamente por la persona — nunca `true` por defecto. */
  attestation: boolean;
  descripcion?: string;
  /** Grabación OBLIGATORIA del consentimiento de la persona cuya voz se va a clonar. */
  consentimiento: File;
  /** 1 a 5 muestras de audio de la voz a clonar. */
  muestras: File[];
}

// ---------------------------------------------------------------------------
// Tipos — Podcasts (`POST/GET /v1/voz/podcasts*`, WP-V6-04)
// ---------------------------------------------------------------------------

export type PodcastStatus = "pending" | "running" | "done" | "error";

export interface PodcastGuionSegmentoIn {
  texto: string;
  /** `voice_id` de ElevenLabs para este segmento; vacío = voz por defecto. */
  voz?: string;
}

export interface PodcastGuionSegmento {
  texto: string;
  voz: string | null;
}

export interface Podcast {
  id: string;
  titulo: string;
  guion: PodcastGuionSegmento[];
  status: PodcastStatus;
  file_id: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface CrearPodcastInput {
  titulo: string;
  guion: PodcastGuionSegmentoIn[];
}

/** `GET /v1/files/{id}` (`apps/api/edecan_api/routers/files.py`) — ver el
 * docstring del módulo ("Descarga de un podcast done"): solo metadata. */
export interface PodcastFileOut {
  id: string;
  filename: string;
  mime: string;
  size_bytes: number;
  status: string;
  s3_key: string;
  created_at: string;
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

/** POST multipart/form-data autenticado: NUNCA fija `Content-Type` a mano —
 * `fetch` calcula el boundary correcto solo si `body` es un `FormData` crudo. */
async function apiFormData<T>(path: string, formData: FormData): Promise<T> {
  const res = await authedFetch(path, { method: "POST", body: formData });
  if (!res.ok) {
    const { message, detail } = await extractErrorMessage(res);
    throw new ApiError(res.status, message, detail);
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

// ---------------------------------------------------------------------------
// Fetchers (`/v1/voz/*`)
// ---------------------------------------------------------------------------

export async function listVoces(): Promise<VozDisponible[]> {
  return apiJson<VozDisponible[]>("/v1/voz/voces");
}

export async function listClones(): Promise<VozClon[]> {
  return apiJson<VozClon[]>("/v1/voz/clones");
}

export async function crearClon(input: CrearClonInput): Promise<VozClon> {
  const formData = new FormData();
  formData.append("nombre", input.nombre);
  formData.append("attestation", input.attestation ? "true" : "false");
  if (input.descripcion) formData.append("descripcion", input.descripcion);
  formData.append("consentimiento", input.consentimiento);
  for (const muestra of input.muestras) {
    formData.append("muestras", muestra);
  }
  return apiFormData<VozClon>("/v1/voz/clones", formData);
}

export async function revocarClon(id: string): Promise<VozClon> {
  return apiJson<VozClon>(`/v1/voz/clones/${id}`, { method: "DELETE" });
}

// ---------------------------------------------------------------------------
// Fetchers — Podcasts (`/v1/voz/podcasts*`, WP-V6-04)
// ---------------------------------------------------------------------------

export async function crearPodcast(input: CrearPodcastInput): Promise<Podcast> {
  return apiJson<Podcast>("/v1/voz/podcasts", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function listPodcasts(): Promise<Podcast[]> {
  return apiJson<Podcast[]>("/v1/voz/podcasts");
}

export async function getPodcast(id: string): Promise<Podcast> {
  return apiJson<Podcast>(`/v1/voz/podcasts/${id}`);
}

/** `GET /v1/files/{id}` — ver docstring del módulo ("Descarga de un podcast done"). */
export async function getFile(id: string): Promise<PodcastFileOut> {
  return apiJson<PodcastFileOut>(`/v1/files/${id}`);
}

export { ApiError };
