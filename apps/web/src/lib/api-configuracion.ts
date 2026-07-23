/**
 * Cliente HTTP de `/v1/credentials` y `/v1/setup/*` (DIRECCION_ACTUAL.md
 * "Principio de UX no negociable: configuración de pocos clicks"; contratos
 * pinned en ARCHITECTURE.md §12, construidos EN PARALELO por WP-V3-02/05 —
 * este archivo codea contra las formas exactas que el work package de esa
 * pantalla recibió por escrito. Vertical slice propio (ROADMAP_V2.md §7.10):
 * `lib/api.ts` es compartido y no se edita, así que este archivo calca su
 * manejo de autenticación (Bearer + reintento tras refrescar en 401 + el
 * prompt de TOTP cuando el refresh silencioso lo exige) en vez de importarlo,
 * exactamente como ya hace `lib/api-misiones.ts` — ver su docstring para el
 * mismo razonamiento, que aplica aquí sin cambios.
 *
 * Dos diferencias deliberadas adicionales frente a `lib/api.ts`/`api-misiones.ts`:
 *
 * 1. **`API_BASE_URL` local con fallback `??`, no `||`.** `lib/api.ts` resuelve
 *    `process.env.NEXT_PUBLIC_API_URL?.replace(/\/+$/, "") || "http://localhost:8000"`.
 *    Ese `||` hace que un `NEXT_PUBLIC_API_URL=""` (vacío a propósito) NO se
 *    respete: `"" || "http://localhost:8000"` evalúa a `"http://localhost:8000"`
 *    porque `""` es falsy en JS (confirmado en consola: `""|| "x" → "x"`,
 *    `"" ?? "x" → ""`). Eso rompe el build de escritorio (`next.config.mjs`,
 *    `NEXT_OUTPUT=export`): ahí el build pasa `NEXT_PUBLIC_API_URL=''` a
 *    propósito para que el fetch quede relativo (`'' + '/v1/credentials'` =
 *    same-origin, servido por el mismo backend local empaquetado — ver
 *    `docs/primeros-pasos.md`), y con `||` esa intención se perdería en
 *    silencio. Este archivo usa `??` para que un vacío explícito se respete
 *    y solo caiga al default cuando la variable ni siquiera está definida.
 *    Nota: el resto de `api-*.ts` que importan `API_BASE_URL` desde
 *    `lib/api.ts` (p. ej. `api-misiones.ts`) siguen con `||` — no es un bug
 *    de esta pantalla arreglarlo ahí (archivo compartido, ver regla de
 *    "un dueño por archivo"), y en la práctica sigue funcionando en modo
 *    escritorio porque el backend empaquetado escucha en el mismo
 *    `localhost:8000` por defecto (ARCHITECTURE.md §10.14). Si algún día eso
 *    cambia, `lib/api.ts` necesitará el mismo `??` — documentado también en
 *    `docs/primeros-pasos.md`.
 * 2. **Tolerancia a 404 en las lecturas** (`getCredentials`/`getSetupStatus`/
 *    `getSetupDetect`): mientras WP-V3-02/05 no hayan montado
 *    `edecan_api.routers.credentials`/`setup`, estas rutas no existen todavía
 *    y devuelven 404 de Starlette. Esta pantalla NO debe verse rota por eso
 *    (ni en desarrollo mientras aterrizan en paralelo, ni en un self-host
 *    viejo que no las tenga): un 404 en estas tres lecturas se trata como
 *    "nada conectado todavía" en vez de tumbar la página con un error rojo.
 *    Las escrituras (PUT/DELETE) SÍ dejan que un 404 se muestre como error
 *    normal — ahí silenciarlo sería engañoso (parecería que conectó cuando
 *    no pasó nada).
 *
 * Las formas de `PUT /v1/credentials/voice/{stt|tts}` no llegaron pinned en
 * el encargo de este work package (solo la ruta), así que `PutVoiceCredentialInput`
 * de abajo es la forma que ESTA pantalla asume/exige — documentado aquí para
 * que quien aterrice el router del otro lado lo tenga por escrito.
 */

import { recoverSessionAfterUnauthorized, isRefreshResultCurrent } from "./session-refresh";
import { getAccessToken, hasSession } from "./tokens";
import { ApiError } from "./api";

// --- Base URL (ver punto 1 del docstring de cabecera) -----------------------

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/+$/, "") ?? "http://localhost:8000";

// --- Tipos: LLM (ARCHITECTURE.md §12, DIRECCION_ACTUAL.md "Nuevo requisito:
// conectar el LLM vía CLI local") ---------------------------------------------

/** `"vertex"` cubre tanto el modo API key de Gemini como el de cuenta de servicio GCP (`extra.mode`). */
export type LlmKind = "anthropic" | "openai_compat" | "vertex" | "claude_cli" | "codex_cli" | "ollama";

export const LLM_KIND_LABELS: Record<LlmKind, string> = {
  anthropic: "Anthropic",
  openai_compat: "Compatible con OpenAI",
  vertex: "Vertex AI / Gemini",
  claude_cli: "Claude CLI",
  codex_cli: "Codex CLI",
  ollama: "Ollama (local)",
};

export interface LlmCredentialStatus {
  kind: LlmKind;
  model_principal: string | null;
  model_rapido: string | null;
  base_url: string | null;
  /** Nunca la key real — p. ej. `"sk-ant-…ab12"`. */
  masked: string | null;
}

export interface LlmModelsOut {
  kind: LlmKind;
  model_principal: string | null;
  model_rapido: string | null;
  models: string[];
  manual_allowed: boolean;
  capabilities_managed_by_edecan: boolean;
  discovery_error: string | null;
}

export interface VoiceSttStatus {
  provider: string;
  masked: string | null;
}

export interface VoiceTtsStatus {
  provider: string;
  voice_id: string | null;
  masked: string | null;
}

// --- Tipos: Imágenes y búsqueda web (auditoría "riesgo-legal-tos": antes de
// esto `edecan_creative.providers`/`edecan_toolkit.research` solo leían
// IMAGES_API_KEY/BRAVE_API_KEY/TAVILY_API_KEY de plataforma, sin ningún
// mecanismo bring-your-own — ver `_images_out`/`_search_out` en
// `apps/api/edecan_api/routers/credentials.py` para el shape exacto). -------

export interface ImagesCredentialStatus {
  base_url: string | null;
  model: string | null;
  /** Nunca la key real — p. ej. `"…ab12"`. */
  masked: string | null;
}

export type SearchProviderKind = "brave" | "tavily";

export interface SearchCredentialStatus {
  provider: SearchProviderKind | string;
  masked: string | null;
}

export interface CredentialsOut {
  llm: LlmCredentialStatus | null;
  voice_stt: VoiceSttStatus | null;
  voice_tts: VoiceTtsStatus | null;
  images: ImagesCredentialStatus | null;
  search: SearchCredentialStatus | null;
}

export interface SetupStatus {
  local_mode: boolean;
  llm_configured: boolean;
  /** `tenants.onboarding_completed_at` (migración 0009) — fuente de verdad
   * de si este tenant ya pasó por el wizard de primer arranque, en vez del
   * viejo flag `edecan_wizard_done` que solo vivía en `localStorage`. */
  onboarding_completed: boolean;
  version: string;
}

export interface CliDetection {
  installed: boolean;
  path: string | null;
  version: string | null;
}

export interface OllamaDetection {
  running: boolean;
  base_url: string | null;
  models: string[];
}

export interface SetupDetect {
  local_mode: boolean;
  claude_cli: CliDetection;
  codex_cli: CliDetection;
  ollama: OllamaDetection;
}

export interface PutLlmCredentialInput {
  kind: LlmKind;
  api_key?: string;
  base_url?: string;
  model_principal?: string;
  model_rapido?: string;
  extra?: Record<string, unknown>;
  validate?: boolean;
}

/**
 * Forma asumida por esta pantalla para `PUT /v1/credentials/voice/{stt|tts}`
 * (ver punto final del docstring de cabecera). `provider` es `"deepgram"`
 * para STT; `"elevenlabs"` o `"polly"` para TTS. Polly no manda `api_key`
 * (usa las credenciales AWS ya disponibles en la instancia, igual que hoy —
 * ARCHITECTURE.md §10.2 `POLLY_VOICE`); solo fija la voz preferida del tenant.
 */
export interface PutVoiceCredentialInput {
  provider: string;
  api_key?: string;
  voice_id?: string;
  extra?: Record<string, unknown>;
  validate?: boolean;
}

/** Forma exacta de `PUT /v1/credentials/images` (`ImagesCredentialsIn`): los tres campos son obligatorios. */
export interface PutImagesCredentialInput {
  base_url: string;
  api_key: string;
  model: string;
  validate?: boolean;
}

/** Forma exacta de `PUT /v1/credentials/search` (`SearchCredentialsIn`). */
export interface PutSearchCredentialInput {
  provider: SearchProviderKind;
  api_key: string;
  validate?: boolean;
}

const CREDENTIALS_EMPTY: CredentialsOut = {
  llm: null,
  voice_stt: null,
  voice_tts: null,
  images: null,
  search: null,
};
const SETUP_STATUS_EMPTY: SetupStatus = {
  local_mode: false,
  llm_configured: false,
  onboarding_completed: false,
  version: "",
};
const SETUP_DETECT_EMPTY: SetupDetect = {
  local_mode: false,
  claude_cli: { installed: false, path: null, version: null },
  codex_cli: { installed: false, path: null, version: null },
  ollama: { running: false, base_url: null, models: [] },
};

// --- Tipos: Casa inteligente / Home Assistant (ARCHITECTURE.md §12.a, §12.b;
// docs/casa-inteligente.md; apps/api/edecan_api/routers/smarthome.py). Router
// v3 montado defensivamente igual que `credentials`/`setup`/`skills`, así que
// `getSmarthomeStatus` sigue el mismo criterio de tolerancia a 404 que las
// lecturas de arriba. ---------------------------------------------------------

export interface SmarthomeStatus {
  configured: boolean;
  base_url: string | null;
  reachable: boolean | null;
}

/** Forma exacta de `PUT /v1/smarthome/credentials` (`SmarthomeCredentialsIn`). */
export interface PutSmarthomeCredentialInput {
  base_url: string;
  token: string;
  validate?: boolean;
}

const SMARTHOME_STATUS_EMPTY: SmarthomeStatus = { configured: false, base_url: null, reachable: null };

function isNotFound(err: unknown): boolean {
  return err instanceof ApiError && err.status === 404;
}

// --- Fetch autenticado con refresh-on-401 (calca lib/api.ts, ver docstring) -

async function rawFetch(path: string, init: RequestInit): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = getAccessToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return fetch(`${API_BASE_URL}${path}`, { ...init, headers });
}

function redirectToLogin(): void {
  if (typeof window === "undefined" || hasSession()) return;
  if (window.location.pathname !== "/login") {
    window.location.assign("/login/");
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

// --- Lecturas (tolerantes a 404, ver punto 2 del docstring de cabecera) -----

export async function getCredentials(): Promise<CredentialsOut> {
  try {
    return await apiJson<CredentialsOut>("/v1/credentials");
  } catch (err) {
    if (isNotFound(err)) return CREDENTIALS_EMPTY;
    throw err;
  }
}

export async function getSetupStatus(): Promise<SetupStatus> {
  try {
    return await apiJson<SetupStatus>("/v1/setup/status");
  } catch (err) {
    if (isNotFound(err)) return SETUP_STATUS_EMPTY;
    throw err;
  }
}

/** `PUT /v1/setup/complete` → 204. Marca el tenant actual como "ya pasó el
 * wizard de primer arranque" en el backend (ver docstring de `SetupStatus`). */
export async function putSetupComplete(): Promise<void> {
  await apiJson<void>("/v1/setup/complete", { method: "PUT" });
}

let setupDetectInFlight: Promise<SetupDetect> | null = null;

async function fetchSetupDetect(): Promise<SetupDetect> {
  try {
    return await apiJson<SetupDetect>("/v1/setup/detect");
  } catch (err) {
    if (isNotFound(err)) return SETUP_DETECT_EMPTY;
    throw err;
  }
}

/** Comparte una detección en vuelo. React Strict Mode monta los efectos dos
 * veces en desarrollo y dos subprocesos CLI simultáneos son trabajo inútil;
 * además, una respuesta tardía no debe ocultar un resultado válido. */
export function getSetupDetect(): Promise<SetupDetect> {
  if (setupDetectInFlight === null) {
    setupDetectInFlight = fetchSetupDetect().finally(() => {
      setupDetectInFlight = null;
    });
  }
  return setupDetectInFlight;
}

export async function getSmarthomeStatus(): Promise<SmarthomeStatus> {
  try {
    return await apiJson<SmarthomeStatus>("/v1/smarthome/status");
  } catch (err) {
    if (isNotFound(err)) return SMARTHOME_STATUS_EMPTY;
    throw err;
  }
}

// --- Escrituras: LLM ----------------------------------------------------------

/** `PUT /v1/credentials/llm` → 204, o lanza `ApiError` con el `detail` exacto del proveedor en un 400. */
export async function putLlmCredential(input: PutLlmCredentialInput): Promise<void> {
  await apiJson<void>("/v1/credentials/llm", { method: "PUT", body: input });
}

export async function deleteLlmCredential(): Promise<void> {
  await apiJson<void>("/v1/credentials/llm", { method: "DELETE" });
}

export async function getLlmModels(): Promise<LlmModelsOut> {
  return apiJson<LlmModelsOut>("/v1/credentials/llm/models");
}

export async function updateLlmModels(input: {
  model_principal: string;
  model_rapido?: string;
}): Promise<void> {
  await apiJson<void>("/v1/credentials/llm/models", { method: "PATCH", body: input });
}

// --- Escrituras: Voz ------------------------------------------------------------

export async function putVoiceStt(input: PutVoiceCredentialInput): Promise<void> {
  await apiJson<void>("/v1/credentials/voice/stt", { method: "PUT", body: input });
}

export async function deleteVoiceStt(): Promise<void> {
  await apiJson<void>("/v1/credentials/voice/stt", { method: "DELETE" });
}

export async function putVoiceTts(input: PutVoiceCredentialInput): Promise<void> {
  await apiJson<void>("/v1/credentials/voice/tts", { method: "PUT", body: input });
}

export async function deleteVoiceTts(): Promise<void> {
  await apiJson<void>("/v1/credentials/voice/tts", { method: "DELETE" });
}

// --- Escrituras: Imágenes y búsqueda web -----------------------------------

/** `PUT /v1/credentials/images` → 204, o lanza `ApiError` con el `detail` exacto del proveedor en un 400. */
export async function putImagesCredentials(input: PutImagesCredentialInput): Promise<void> {
  await apiJson<void>("/v1/credentials/images", { method: "PUT", body: input });
}

export async function deleteImagesCredentials(): Promise<void> {
  await apiJson<void>("/v1/credentials/images", { method: "DELETE" });
}

/** `PUT /v1/credentials/search` → 204, o lanza `ApiError` con el `detail` exacto del proveedor en un 400. */
export async function putSearchCredentials(input: PutSearchCredentialInput): Promise<void> {
  await apiJson<void>("/v1/credentials/search", { method: "PUT", body: input });
}

export async function deleteSearchCredentials(): Promise<void> {
  await apiJson<void>("/v1/credentials/search", { method: "DELETE" });
}

// --- Escrituras: Casa inteligente / Home Assistant ---------------------------

/** `PUT /v1/smarthome/credentials` → 204, o lanza `ApiError` con el detalle exacto (p. ej. token rechazado, host inalcanzable) cuando `validate` (default `true`) falla. */
export async function putSmarthomeCredentials(input: PutSmarthomeCredentialInput): Promise<void> {
  await apiJson<void>("/v1/smarthome/credentials", { method: "PUT", body: input });
}

export async function deleteSmarthomeCredentials(): Promise<void> {
  await apiJson<void>("/v1/smarthome/credentials", { method: "DELETE" });
}
