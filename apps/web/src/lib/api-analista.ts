/**
 * Cliente HTTP de `apps/api/edecan_api/routers/analista.py` (`/v1/analista/*` — pantalla
 * Analista: estadística, pronóstico/anomalías y gráficos de `edecan_docanalysis`, WP-V6-06).
 *
 * Mismo motivo de duplicación que `lib/api-rrhh.ts`/`lib/api-negocios.ts`/`lib/api-inventario.ts`
 * (ver su docstring): `lib/api.ts` está fuera de la lista de archivos que este paquete de
 * trabajo puede tocar, y sus helpers de fetch autenticado (`authedFetch`/`apiJson`) son
 * privados de ese módulo — así que este archivo replica localmente el mismo patrón (Bearer +
 * un reintento tras refrescar el token en 401, incluido el gate de TOTP en `/v1/auth/refresh`
 * que documenta `HOTFIXES_PENDIENTES.md` punto 2). Tipos propios en vez de `lib/types.ts` por
 * el mismo motivo.
 *
 * Esta pantalla NUNCA llama al LLM (ver el docstring de `analista.py`): los 4 endpoints son
 * puro Python determinista/offline. La vía CON LLM (preguntas en lenguaje natural sobre una
 * tabla) sigue existiendo exclusivamente por chat.
 */

import { API_BASE_URL, ApiError } from "./api";
import { recoverSessionAfterUnauthorized, isRefreshResultCurrent } from "./session-refresh";
import { getAccessToken } from "./tokens";

// ---------------------------------------------------------------------------
// Tipos (`edecan_docanalysis` / `routers/analista.py`)
// ---------------------------------------------------------------------------

export interface ArchivoAnalista {
  id: string;
  filename: string | null;
  mime: string | null;
  size_bytes: number | null;
  created_at: string | null;
}

export type TipoColumna = "numerica" | "texto";

export interface ColumnaInfo {
  nombre: string;
  tipo: TipoColumna;
}

export interface StatsNumerica {
  tipo: "numerica";
  count: number;
  media: number;
  mediana: number;
  min: number;
  max: number;
  std: number;
  nulos: number;
}

export interface StatsTexto {
  tipo: "texto";
  nulos: number;
  top: { valor: string; conteo: number }[];
}

export type StatsColumna = StatsNumerica | StatsTexto;

export interface OutliersColumna {
  conteo: number;
  valores: number[];
}

export interface ResumenTabla {
  columnas: ColumnaInfo[];
  stats: Record<string, StatsColumna>;
  outliers: Record<string, OutliersColumna>;
  filas_leidas: number;
  total_filas_archivo: number;
}

export interface ForecastResultado {
  metodo: string;
  metodo_legible: string;
  mae: number;
  candidatos_mae: Record<string, number>;
  n_holdout: number;
  predicciones: number[];
  intervalo_inferior: number[];
  intervalo_superior: number[];
  margen: number;
  pendiente: number | null;
  intercepto: number | null;
  r2: number | null;
  tendencia: number | null;
  aviso: string | null;
}

export interface AnomaliaOutlier {
  indice: number;
  valor: number;
  score: number;
}

export interface AnomaliaRacha {
  inicio: number;
  fin: number;
  longitud: number;
  direccion: "por_encima" | "por_debajo";
}

export interface AnomaliasResultado {
  metodo: string;
  umbral: number;
  n: number;
  outliers: AnomaliaOutlier[];
  rachas: AnomaliaRacha[];
  aviso: string | null;
}

export interface ForecastResponse {
  columna_valor: string;
  columna_fecha: string | null;
  etiquetas: string[] | null;
  forecast: ForecastResultado;
  anomalias: AnomaliasResultado | null;
  anomalias_error: string | null;
}

export type TipoGrafico = "barras" | "lineas" | "dona";

export interface GraficoResponse {
  svg: string;
  tipo: TipoGrafico;
  columna_x: string;
  columna_y: string;
  puntos_graficados: number;
  puntos_totales: number;
  truncado: boolean;
}

// ---------------------------------------------------------------------------
// Auth (mismo patrón que `lib/api.ts` / `lib/api-rrhh.ts`)
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

// ---------------------------------------------------------------------------
// Fetchers
// ---------------------------------------------------------------------------

export async function listArchivosAnalista(): Promise<ArchivoAnalista[]> {
  return apiJson<ArchivoAnalista[]>("/v1/analista/archivos");
}

export async function obtenerResumen(fileId: string, hoja?: string): Promise<ResumenTabla> {
  return apiJson<ResumenTabla>(`/v1/analista/${fileId}/resumen`, {
    method: "POST",
    ...jsonBody({ hoja: hoja || undefined }),
  });
}

export async function obtenerForecast(
  fileId: string,
  input: { columna_fecha?: string; columna_valor?: string; horizonte?: number },
): Promise<ForecastResponse> {
  return apiJson<ForecastResponse>(`/v1/analista/${fileId}/forecast`, {
    method: "POST",
    ...jsonBody(input),
  });
}

export async function generarGraficoAnalista(
  fileId: string,
  input: { tipo: TipoGrafico; columna_x?: string; columna_y?: string },
): Promise<GraficoResponse> {
  return apiJson<GraficoResponse>(`/v1/analista/${fileId}/grafico`, {
    method: "POST",
    ...jsonBody(input),
  });
}

export { ApiError };
