/**
 * Cliente HTTP de `/v1/skills` (`edecan_api.routers.skills`, `ARCHITECTURE.md`
 * §12.a/§12.e, dueño WP-V3-04). Vertical slice propio (mismo criterio que
 * `lib/api-misiones.ts`, ver su docstring): `lib/api.ts` es compartido y no
 * se edita, así que este archivo importa de ahí `API_BASE_URL`/`ApiError` y
 * calca su manejo de autenticación (Bearer + reintento tras refrescar en
 * 401) — con el dedupe de refresh concurrente (`refreshInFlight`) en una
 * variable de módulo LOCAL, no compartida con `lib/api.ts` (mismo trade-off
 * documentado ahí: en el peor caso, una llamada de más, nunca un bug de
 * corrección).
 */

import { API_BASE_URL, ApiError } from "./api";
import { clearTokens, getAccessToken, getRefreshToken, setTokens } from "./tokens";
import type { TokenPair } from "./types";

// --- Tipos (espejan edecan_api.routers.skills) --------------------------------

/** Uno de `edecan_skills.security.TRUST_TIERS` — "indexada" si vino de un índice curado
 * (skills.sh/OpenClaw/Hermes), "sin_revisar" si se instaló directo por `owner/repo`. */
export type SkillTrustTier = "indexada" | "sin_revisar";

/** Un hallazgo de `edecan_skills.security.escanear_inyeccion` — heurístico best-effort,
 * ver `docs/skills.md` "Seguridad de skills de terceros". */
export interface SkillHallazgo {
  patron: string;
  fragmento: string;
  posicion: number;
}

export interface SkillSummary {
  id: string;
  nombre: string;
  slug: string;
  source: string;
  descripcion: string;
  version: string | null;
  enabled: boolean;
  trust_tier: SkillTrustTier;
  capabilities: string[];
  /** Subconjunto de `capabilities` que es `dangerous=True` en el repo — ya viene filtrado
   * del backend (`edecan_skills.security.CAPACIDADES_PELIGROSAS`), pinta esas en rojo. */
  capabilities_peligrosas: string[];
  created_at: string;
}

export interface SkillDetail extends SkillSummary {
  contenido: string;
  recursos: Record<string, unknown>;
  /** Solo en el detalle (necesita `contenido`) — `[]` en `SkillSummary`/la lista. */
  hallazgos: SkillHallazgo[];
  updated_at: string;
}

/** Skill esperando confirmación explícita (`acknowledge`) para activarse — ver
 * `app/skills/page.tsx` y `components/skills/InstalledSkillItem.tsx`. */
export interface PendingAcknowledge {
  skillId: string;
  mensaje: string;
}

export interface SkillSearchHit {
  nombre: string;
  source: string;
  descripcion: string;
  installs: number | null;
}

/** Fuente de un resultado de `buscar_skills`/`SkillSearchPanel` — decide `trust_tier` al
 * instalar (ver `installSkill`). `"directo"` es lo que arma el propio usuario a mano. */
export type SkillFuente = "directo" | "skills_sh" | "openclaw" | "hermes";

// --- Fetch autenticado con refresh-on-401 (calca lib/api.ts, ver docstring) -

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
        const pair = (await res.json()) as TokenPair;
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

function redirectToLogin(): void {
  if (typeof window === "undefined") return;
  clearTokens();
  if (window.location.pathname !== "/login") {
    window.location.assign("/login");
  }
}

async function authedFetch(path: string, init: RequestInit = {}): Promise<Response> {
  let res = await rawFetch(path, init);
  if (res.status === 401) {
    let result = await tryRefresh();
    if (!result.ok && result.totpRequired) {
      result = (await tryRefreshWithTotpPrompt()) ? { ok: true } : { ok: false, totpRequired: false };
    }
    if (result.ok) {
      res = await rawFetch(path, init);
    } else {
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

// --- Fetchers ----------------------------------------------------------------

/** `GET /v1/skills` — instaladas, sin `contenido` (ver docstring del router). */
export async function listSkills(): Promise<SkillSummary[]> {
  const body = await apiJson<{ skills: SkillSummary[] }>("/v1/skills");
  return body.skills;
}

/** `GET /v1/skills/{id}` — incluye `contenido` completo. */
export async function getSkill(id: string): Promise<SkillDetail> {
  return apiJson<SkillDetail>(`/v1/skills/${id}`);
}

/** `POST /v1/skills/search` — descubrimiento best-effort en el índice de skills.sh. */
export async function searchSkills(q: string): Promise<SkillSearchHit[]> {
  const body = await apiJson<{ resultados: SkillSearchHit[] }>("/v1/skills/search", {
    method: "POST",
    body: { q },
  });
  return body.resultados;
}

/**
 * `POST /v1/skills/install` — instala (o reinstala) desde `owner/repo`/URL. `fuente`
 * (default `"directo"`) decide `trust_tier`: pasa `"skills_sh"` cuando `source` viene de un
 * resultado de `SkillSearchPanel` para que quede marcada "indexada" en vez de "sin revisar".
 */
export async function installSkill(
  source: string,
  fuente: SkillFuente = "directo",
): Promise<SkillDetail> {
  return apiJson<SkillDetail>("/v1/skills/install", {
    method: "POST",
    body: { source, fuente },
  });
}

/**
 * `PUT /v1/skills/{id}` — activa/desactiva. Activar (`enabled: true`) una skill con
 * capacidades peligrosas o hallazgos de inyección devuelve `400` (`ApiError`, `err.message`
 * trae el detalle exacto de qué se está aceptando) salvo que `acknowledge` sea `true` — ver
 * `docs/skills.md` "Seguridad de skills de terceros" y el flujo de confirmación en
 * `InstalledSkillItem`.
 */
export async function setSkillEnabled(
  id: string,
  enabled: boolean,
  acknowledge = false,
): Promise<void> {
  await apiJson<void>(`/v1/skills/${id}`, { method: "PUT", body: { enabled, acknowledge } });
}

/** `DELETE /v1/skills/{id}` — desinstala. */
export async function deleteSkill(id: string): Promise<void> {
  await apiJson<void>(`/v1/skills/${id}`, { method: "DELETE" });
}

// Re-exporta `ApiError` para sus propios consumidores (`components/skills/*`,
// que importan `ApiError` desde este módulo en vez de `@/lib/api` directo) —
// mismo patrón ya usado por `api-automatizaciones.ts`/`api-ide.ts`/
// `api-remoto.ts`/`api-negocios.ts`/`api-perfil.ts` (los 5 hermanos de este
// archivo). Sin esta línea, `npx tsc --noEmit`/`next build` fallan con
// TS2459 en `InstalledSkillItem.tsx`/`SkillSearchPanel.tsx` — bloqueaba
// TODO el build del monorepo `apps/web` (normal y `NEXT_OUTPUT=export`), no
// solo las pantallas de Skills. Ajuste quirúrgico de una línea, añadido por
// WP-V3-07 (Configuración) al verificar su propio build, fuera de sus rutas
// asignadas — documentado aquí en vez de silenciarlo (ver
// `ARCHITECTURE.md` §12.a, este archivo sigue siendo dueño de WP-V3-04).
export { ApiError };
