/**
 * Cliente HTTP de la voz gestionada por Speech Engine de ElevenLabs
 * (`docs/speech-engine.md`, `apps/api/edecan_api/routers/speech_engine.py`).
 *
 * El navegador recibe SOLO el `conversation_token` efímero de WebRTC — jamás
 * la API key del tenant. Mismo patrón de fetch autenticado que `lib/api-voz.ts`
 * (Bearer + reintento tras refresh en 401).
 */

import { API_BASE_URL, ApiError } from "./api";
import { getAccessToken } from "./tokens";
import { isRefreshResultCurrent, recoverSessionAfterUnauthorized } from "./session-refresh";

// ---------------------------------------------------------------------------
// Tipos (espejo de `routers/speech_engine.py`)
// ---------------------------------------------------------------------------

export interface ModeloChatCatalogo {
  id: string;
  nombre: string;
  descripcion?: string;
  orden?: number;
  principal?: boolean;
  ve_imagenes?: boolean;
  soporta_esfuerzo?: boolean;
}

export interface VozTTS {
  id: string;
  name: string;
}

export interface ModeloTTS {
  id: string;
  name: string;
}

export interface PreferenciaVozGestionada {
  enabled: boolean;
  provider: string;
  voice_model_id: string | null;
  delegation_model_id: string | null;
  delegation_effort: string | null;
  voice_id: string | null;
  tts_model_id: string | null;
  max_duration_seconds: number;
}

export interface PreferenciasVozGestionada {
  preference: PreferenciaVozGestionada | null;
  credential_connected: boolean;
  paid_provider_notice: string;
  catalogs: {
    voice_models: ModeloChatCatalogo[];
    tts_voices: VozTTS[];
    tts_models: ModeloTTS[];
  };
  defaults: {
    voice_model_id: string;
    tts_model_id: string;
    max_duration_seconds: number;
  };
}

export interface PutPreferenciasVozGestionada {
  enabled: boolean;
  provider: string;
  voice_model_id: string | null;
  delegation_model_id: string | null;
  delegation_effort: string | null;
  voice_id: string | null;
  tts_model_id: string | null;
  max_duration_seconds: number;
}

export interface SesionVozGestionada {
  session_id: string;
  conversation_id: string;
  conversation_token: string;
  expires_at: string;
  token_expires_at: string;
  voice_model_id: string;
  delegation_model_id: string | null;
  tts_model_id: string;
  voice_id: string | null;
  language: string;
}

// ---------------------------------------------------------------------------
// Auth
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

async function apiJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (typeof init.body === "string") headers.set("Content-Type", "application/json");
  const res = await authedFetch(path, { ...init, headers });
  if (!res.ok) {
    let detail: unknown;
    try {
      detail = await res.clone().json();
    } catch {
      detail = undefined;
    }
    const raw = (detail as { detail?: unknown } | null)?.detail;
    const message =
      typeof raw === "string" ? raw : `Error HTTP ${res.status}`;
    throw new ApiError(res.status, message, detail);
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

// ---------------------------------------------------------------------------
// Fetchers
// ---------------------------------------------------------------------------

export async function getPreferenciasVozGestionada(): Promise<PreferenciasVozGestionada> {
  return apiJson<PreferenciasVozGestionada>("/v1/voice/preferences");
}

export async function putPreferenciasVozGestionada(
  input: PutPreferenciasVozGestionada,
): Promise<{ preference: PreferenciaVozGestionada }> {
  return apiJson<{ preference: PreferenciaVozGestionada }>("/v1/voice/preferences", {
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export async function crearSesionVozGestionada(
  conversationId?: string | null,
): Promise<SesionVozGestionada> {
  return apiJson<SesionVozGestionada>("/v1/voice/speech-engine/sessions", {
    method: "POST",
    body: JSON.stringify({
      paid_consent: true,
      ...(conversationId ? { conversation_id: conversationId } : {}),
    }),
  });
}

export async function terminarSesionVozGestionada(
  sessionId: string,
): Promise<{ session_id: string; ended: boolean }> {
  return apiJson<{ session_id: string; ended: boolean }>(
    `/v1/voice/speech-engine/sessions/${sessionId}/end`,
    { method: "POST" },
  );
}

// ---------------------------------------------------------------------------
// Credencial del proveedor pagado (`/v1/credentials/voice/speech-engine`)
// ---------------------------------------------------------------------------

export interface CredencialSpeechEngine {
  provider: string;
  masked: string | null;
}

export async function getCredencialSpeechEngine(): Promise<CredencialSpeechEngine | null> {
  const todas = await apiJson<{ speech_engine: CredencialSpeechEngine | null }>(
    "/v1/credentials",
  );
  return todas.speech_engine ?? null;
}

export async function conectarSpeechEngine(apiKey: string): Promise<void> {
  await apiJson<undefined>("/v1/credentials/voice/speech-engine", {
    method: "PUT",
    body: JSON.stringify({ provider: "elevenlabs", api_key: apiKey }),
  });
}

export async function desconectarSpeechEngine(): Promise<void> {
  await apiJson<undefined>("/v1/credentials/voice/speech-engine", { method: "DELETE" });
}

export { ApiError };