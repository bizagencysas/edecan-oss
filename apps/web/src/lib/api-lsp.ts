/**
 * Cliente HTTP para las capacidades LSP REALES que expone opencode --
 * `GET /find/symbol` y `GET /lsp` -- envueltas por
 * `apps/companion/edecan_companion/ide_opencode_lsp.py::ClienteLspOpencode`.
 * Léase el docstring completo de ese módulo antes de tocar este archivo: ahí
 * está la evidencia verificada (contra un `opencode serve` 1.17.18 real) de
 * qué SÍ y qué NO existe.
 *
 * Archivo nuevo, aparte de `api-ide.ts` (ese archivo ya es enorme y lo tocan
 * varios agentes en la misma tanda) -- por eso replica aquí, en miniatura,
 * el mismo patrón de autenticación que usa `api-ide.ts` en vez de importar
 * sus helpers (`rawFetch`/`authedFetch`/`apiJson` no están exportados).
 *
 * # Por qué SOLO dos funciones -- no las cuatro que sugería el encargo
 *
 * El encargo mencionaba cuatro acciones en curso del otro lado
 * (`ide_lsp_symbols`, `ide_lsp_definition`, `ide_lsp_references`,
 * `ide_lsp_status`). Pero `ide_opencode_lsp.py` -- que ya existe, se leyó
 * completo antes de escribir esto -- documenta con evidencia real que
 * "definición de símbolo en posición X,Y" y "referencias de un símbolo" NO
 * EXISTEN en la superficie HTTP de opencode serve 1.17.18: se enumeraron las
 * ~150 rutas de `/doc` una por una y no hay ninguna equivalente a
 * `textDocument/definition` ni a `textDocument/references`.
 * `ClienteLspOpencode.definicion()` y `.referencias()` son, a propósito, solo
 * un `raise LspOpencodeNoDisponibleError(...)` explicativo -- no hay ningún
 * dato real que estas dos puedan traer nunca, sin importar qué ruta REST se
 * les ponga encima. Cablearlas hasta la interfaz sería pintar un botón que
 * SIEMPRE falla -- el "control muerto" que el encargo prohíbe. Por eso este
 * cliente (y la pantalla que lo usa, `SearchPanel.tsx`) solo cubren lo que sí
 * es real: buscar un símbolo por nombre, y ver el estado de los servidores
 * de lenguaje.
 *
 * # Aviso que hay que trasladar a cualquier pantalla que use esto
 *
 * El mismo docstring documenta, con pasos reproducidos contra un servidor
 * real, que `buscar_simbolos`/`estado_lsp` devuelven `[]` casi siempre --no
 * porque no haya nada que encontrar, sino porque la superficie pública de
 * opencode no tiene ninguna ruta para pedirle "indexá este archivo" antes de
 * buscar. Un `[]` de estas dos funciones NO es evidencia de "no hay
 * resultados": es, hoy, el resultado esperado la mayoría de las veces. Toda
 * pantalla que consuma esto debe decirlo, no mostrar una lista vacía muda.
 *
 * # Contrato REST asumido -- AJUSTAR si no coincide con lo que aterrizó
 *
 * Al momento de escribir esto, `apps/api/edecan_api/routers/ide.py` no tenía
 * ninguna ruta `lsp` todavía: otro agente la construye en la misma tanda, en
 * paralelo, sin canal de coordinación con esta sesión (se verificó con
 * `grep -i lsp` sobre ese archivo, cero resultados). Las rutas de abajo
 * siguen el mismo patrón que ya usa el resto de `api-ide.ts` para
 * capacidades por-workspace (p. ej. `GET /v1/ide/workspaces/{id}/references`,
 * `.../search/semantic/status`):
 *
 *   GET /v1/ide/workspaces/{workspace_id}/lsp/symbols?query=...
 *   GET /v1/ide/workspaces/{workspace_id}/lsp/status
 *
 * La clave con la que el companion envuelve cada lista (`_send_or_error`
 * exige un dict, nunca un array suelto -- ver `routers/ide.py::_send_or_error`)
 * tampoco se puede saber de antemano: el propio archivo mezcla envoltorios en
 * español (`permisos`) e inglés (`conversations`, `projects`) según quién los
 * escribió. `pickArray()` de abajo prueba varias claves candidatas en vez de
 * asumir una sola, para no romperse en silencio si la real es distinta. Si
 * ni las rutas ni ninguna clave candidata coinciden, este es el ÚNICO archivo
 * que hay que tocar -- los tipos (`IdeLspSymbol`/`IdeLspServerStatus`) ya
 * copian los campos exactos de `SimboloLsp`/`EstadoServidorLsp`.
 */

import { API_BASE_URL, ApiError } from "./api";
import { recoverSessionAfterUnauthorized, isRefreshResultCurrent } from "./session-refresh";
import { getAccessToken, getDesktopCapability } from "./tokens";

// ---------------------------------------------------------------------------
// Tipos -- espejo exacto de los modelos Pydantic de `ide_opencode_lsp.py`
// ---------------------------------------------------------------------------

/** `PosicionLsp` -- posición LSP 0-indexada (línea/columna). */
export interface IdeLspPosition {
  line: number;
  character: number;
}

/** `RangoLsp`. */
export interface IdeLspRange {
  start: IdeLspPosition;
  end: IdeLspPosition;
}

/** `UbicacionSimboloLsp` -- URI del archivo (`file://...`, normalmente absoluto) + rango. */
export interface IdeLspSymbolLocation {
  uri: string;
  range: IdeLspRange;
}

/**
 * `SimboloLsp` -- un elemento de `GET /find/symbol`. `kind` es el entero
 * crudo de `SymbolKind` del protocolo LSP (1=File, 5=Class, 12=Function, …);
 * opencode no lo traduce a texto y este cliente tampoco -- mapearlo sin
 * haber visto casos reales sería inventar (ver el docstring de
 * `ide_opencode_lsp.py::SimboloLsp`).
 */
export interface IdeLspSymbol {
  name: string;
  kind: number;
  location: IdeLspSymbolLocation;
}

/** `EstadoServidorLsp` -- un elemento de `GET /lsp`. */
export interface IdeLspServerStatus {
  id: string;
  name: string;
  root: string;
  status: "connected" | "error";
}

// ---------------------------------------------------------------------------
// Auth + fetch -- mismo patrón que `api-ide.ts` (ver docstring del archivo)
// ---------------------------------------------------------------------------

async function rawFetch(path: string, init: RequestInit): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = getAccessToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const desktopCapability = getDesktopCapability();
  if (desktopCapability) {
    headers.set("X-Edecan-Desktop-Capability", desktopCapability);
  }
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
  return { message: `Error HTTP ${res.status}`, detail };
}

async function apiJson<T>(path: string): Promise<T> {
  const res = await authedFetch(path);
  if (!res.ok) {
    const { message, detail } = await extractErrorMessage(res);
    throw new ApiError(res.status, message, detail);
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

/**
 * Prueba varias claves de envoltorio candidatas y devuelve la primera cuyo
 * valor sea un array -- ver "Contrato REST asumido" arriba: la clave real no
 * se puede saber de antemano. Lanza un error legible (no devuelve `[]` en
 * silencio, que se confundiría con el `[]` real y ya ambiguo de opencode) si
 * ninguna candidata calza, para que el fallo de contrato se note en vez de
 * disfrazarse de "sin resultados".
 */
function pickArray<T>(body: unknown, candidates: string[]): T[] {
  if (Array.isArray(body)) return body as T[];
  if (body && typeof body === "object") {
    for (const key of candidates) {
      const value = (body as Record<string, unknown>)[key];
      if (Array.isArray(value)) return value as T[];
    }
  }
  throw new Error(
    `Respuesta con forma inesperada (se esperaba un array en una de: ${candidates.join(", ")}).`,
  );
}

// ---------------------------------------------------------------------------
// Fetchers
// ---------------------------------------------------------------------------

/**
 * `GET /v1/ide/workspaces/{id}/lsp/symbols` -- busca símbolos del workspace
 * (funciones, clases, variables) por nombre, vía el LSP que opencode ya
 * trae. Ver el aviso del docstring del archivo: contra un `opencode serve`
 * real esto casi siempre devuelve `[]`, sin que eso signifique "no existe".
 */
export async function getIdeLspSymbols(workspaceId: string, query: string): Promise<IdeLspSymbol[]> {
  const params = new URLSearchParams({ query });
  const body = await apiJson<unknown>(
    `/v1/ide/workspaces/${encodeURIComponent(workspaceId)}/lsp/symbols?${params.toString()}`,
  );
  return pickArray<IdeLspSymbol>(body, ["simbolos", "symbols"]);
}

/**
 * `GET /v1/ide/workspaces/{id}/lsp/status` -- estado de conexión de los
 * servidores de lenguaje para este workspace (`connected` / `error`). Mismo
 * aviso: hoy suele venir vacía porque opencode nunca llega a levantar un
 * servidor por esta vía HTTP, no porque no haya ninguno instalado.
 */
export async function getIdeLspStatus(workspaceId: string): Promise<IdeLspServerStatus[]> {
  const body = await apiJson<unknown>(
    `/v1/ide/workspaces/${encodeURIComponent(workspaceId)}/lsp/status`,
  );
  return pickArray<IdeLspServerStatus>(body, ["servidores", "servers", "estado_lsp", "lsp"]);
}

/**
 * Convierte la `uri` de un `IdeLspSymbol` (típicamente `file:///abs/...`) en
 * una ruta relativa al workspace, la única forma que aceptan
 * `getIdeWorkspaceFile`/`onOpenFile` en el resto del IDE. Devuelve `null`
 * cuando no se puede resolver con certeza (uri fuera del workspace, o
 * `workspacePath` desconocido) -- la pantalla debe entonces mostrar la ruta
 * cruda como texto, NUNCA como un botón "abrir" que fallaría contra el
 * archivo equivocado.
 */
export function lspUriToWorkspaceRelativePath(uri: string, workspacePath: string | null | undefined): string | null {
  if (!workspacePath) return null;
  let raw = uri;
  if (raw.startsWith("file://")) {
    try {
      raw = decodeURIComponent(raw.slice("file://".length));
    } catch {
      raw = raw.slice("file://".length);
    }
  }
  const root = workspacePath.endsWith("/") ? workspacePath.slice(0, -1) : workspacePath;
  if (raw === root) return "";
  if (raw.startsWith(`${root}/`)) return raw.slice(root.length + 1);
  return null;
}
