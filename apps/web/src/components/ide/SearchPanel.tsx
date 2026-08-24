"use client";

/**
 * Búsqueda en el sandbox del companion, con tres modos:
 * - "Texto" (`POST /v1/ide/search`): substring literal, ya existía.
 * - "Significado" (`POST /v1/ide/workspaces/{id}/search/semantic`): por
 *   significado, vía `ide_busqueda_semantica.SemanticSearchService`. Solo se
 *   ofrece cuando se conoce el `workspaceId` -- la ruta semántica es
 *   por-workspace explícito, a diferencia de la de texto (que opera sobre
 *   "el workspace activo" del companion).
 * - "Símbolos" (`GET /v1/ide/workspaces/{id}/lsp/symbols`): CONSTRUIDO PERO NO
 *   OFRECIDO. Medido contra un `opencode serve` 1.17.18 real: `/find/symbol`
 *   devuelve 0 para símbolos que existen con certeza y `/lsp` devuelve `[]`.
 *   opencode no levanta servidores de lenguaje para su API HTTP pública, y no
 *   expone "ir a la definición" bajo ninguna ruta. La pestaña está retirada a
 *   propósito -- ver el comentario junto a la lista de modos.
 *
 * "Texto" y "Significado" se normalizan a la misma forma (`Hit`) para no
 * duplicar el renderizado de la lista; "Símbolos" usa su propio renderizado
 * porque su resultado (nombre + `uri` + rango LSP) no es un `Hit` de texto y
 * forzarlo a esa forma perdería información real (el `kind` del símbolo) sin
 * necesidad.
 *
 * # Por qué "Símbolos" existe pero "Ir a la definición" y "Referencias" NO
 *
 * El encargo original sugería "ir a la definición" desde `CodeEditor.tsx`
 * como la integración más útil, y "referencias" en un panel lateral. Ninguna
 * de las dos se construyó: `apps/companion/edecan_companion/
 * ide_opencode_lsp.py` (léase su docstring completo) documenta, con
 * evidencia real contra un `opencode serve` 1.17.18 vivo, que NINGUNA de las
 * dos existe en la superficie HTTP de opencode -- se enumeraron las ~150
 * rutas de su OpenAPI una por una. `ClienteLspOpencode.definicion()` y
 * `.referencias()` son, a propósito, solo un error explicativo. Pintar un
 * botón "ir a la definición" que SIEMPRE falla sería exactamente el "control
 * muerto" que el encargo de este paquete de trabajo prohíbe -- por eso lo
 * único que se cablea aquí es lo que sí es real: buscar un símbolo por
 * nombre (LSP `workspace/symbol`), y ver qué servidores de lenguaje están
 * conectados. Ver `lib/api-lsp.ts` para el mismo razonamiento del lado del
 * cliente HTTP.
 */

import { useState } from "react";

import { SearchIcon } from "@/components/icons";
import { Button, Input, Spinner } from "@/components/ui";
import {
  getIdeSemanticSearchStatus,
  postIdeSearch,
  postIdeSemanticSearch,
  type IdeSemanticSearchStatus,
} from "@/lib/api-ide";
import {
  getIdeLspStatus,
  getIdeLspSymbols,
  lspUriToWorkspaceRelativePath,
  type IdeLspServerStatus,
  type IdeLspSymbol,
} from "@/lib/api-lsp";

type Mode = "texto" | "significado" | "simbolos";

interface Hit {
  path: string;
  line: number;
  text: string;
  score: number | null;
}

export function SearchPanel({
  workspaceId,
  workspacePath,
  onOpenFile,
}: {
  workspaceId?: string | null;
  /** Raíz absoluta del workspace -- ver el docstring de `FilesPanel.tsx`.
   * Sin esto, el tab "Símbolos" sigue funcionando pero no puede ofrecer
   * "abrir archivo" con certeza (ver `lspUriToWorkspaceRelativePath`). */
  workspacePath?: string | null;
  onOpenFile: (path: string) => void;
}) {
  const [mode, setMode] = useState<Mode>("texto");
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<Hit[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [degradedReason, setDegradedReason] = useState<string | null>(null);
  const [indexStatus, setIndexStatus] = useState<IdeSemanticSearchStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searched, setSearched] = useState(false);

  // Estado propio del tab "Símbolos": vive aparte de `hits` porque un
  // símbolo no es un `Hit` de texto (ver docstring del archivo).
  const [symbols, setSymbols] = useState<IdeLspSymbol[]>([]);
  const [lspServers, setLspServers] = useState<IdeLspServerStatus[] | null>(null);
  const [lspStatusError, setLspStatusError] = useState<string | null>(null);

  function loadLspStatus(id: string) {
    // De solo lectura y liviano (GET sin body): se pide sola al entrar al
    // tab, igual que `getIdeSemanticSearchStatus` arriba para "Significado".
    // Errores acá no bloquean la búsqueda de símbolos -- solo apagan el
    // contexto informativo de qué servidores están conectados.
    setLspStatusError(null);
    getIdeLspStatus(id)
      .then(setLspServers)
      .catch((err) => {
        setLspServers(null);
        setLspStatusError(err instanceof Error ? err.message : "No se pudo leer el estado del LSP.");
      });
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const q = query.trim();
    if (!q) return;
    setLoading(true);
    setError(null);
    setDegradedReason(null);
    try {
      if (mode === "simbolos" && workspaceId) {
        const result = await getIdeLspSymbols(workspaceId, q);
        setSymbols(result);
      } else if (mode === "significado" && workspaceId) {
        const result = await postIdeSemanticSearch(workspaceId, q);
        setHits(
          result.matches.map((m) => ({
            path: m.path,
            line: m.start_line,
            text: m.excerpt,
            score: m.score,
          })),
        );
        setTruncated(result.index_truncated);
        setDegradedReason(result.mode === "texto" ? result.degraded_reason : null);
        void getIdeSemanticSearchStatus(workspaceId)
          .then(setIndexStatus)
          .catch(() => setIndexStatus(null));
      } else {
        const result = await postIdeSearch(q);
        setHits(result.matches.map((m) => ({ path: m.path, line: m.line, text: m.texto, score: null })));
        setTruncated(result.truncated);
      }
      setSearched(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo buscar.");
    } finally {
      setLoading(false);
    }
  }

  function selectMode(value: Mode) {
    setMode(value);
    setSearched(false);
    setHits([]);
    setSymbols([]);
    setError(null);
    if (value === "simbolos" && workspaceId) {
      loadLspStatus(workspaceId);
    }
  }

  const lspConnected = lspServers?.filter((s) => s.status === "connected") ?? [];

  return (
    <div className="flex flex-col gap-3">
      <form onSubmit={handleSubmit} className="flex items-center gap-2">
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={
            mode === "texto"
              ? "Buscar texto en los archivos…"
              : mode === "significado"
                ? "Buscar por significado…"
                : "Buscar símbolo por nombre…"
          }
          aria-label="Buscar en los archivos"
        />
        <Button type="submit" size="sm" loading={loading} disabled={!query.trim()}>
          <SearchIcon className="h-4 w-4" />
        </Button>
      </form>

      {workspaceId && (
        <div className="flex gap-1 text-xs">
          {/* "Símbolos" NO se ofrece: medido contra un `opencode serve` 1.17.18
              real, `GET /find/symbol` devuelve 0 resultados para símbolos que
              existen con certeza en el repo (`SessionManager`,
              `ServidorOpencode`), y `GET /lsp` devuelve `[]` -- opencode no
              levanta servidores de lenguaje para su API HTTP pública, solo
              dentro de su propia interfaz de terminal. Un modo de búsqueda que
              siempre encuentra cero es peor que no ofrecerlo: parece que tu
              código no tiene ese símbolo.
              El cliente (`lib/api-lsp.ts`) y el cable del backend se quedan
              listos para el día que opencode lo exponga; lo único que se
              retira es la pestaña. */}
          {([
            ["texto", "Texto"],
            ["significado", "Significado"],
          ] as const).map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => selectMode(value)}
              className={`rounded-md px-2.5 py-1 font-semibold ${
                mode === value
                  ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900"
                  : "text-slate-500 hover:bg-forja-superficie-elevada dark:hover:bg-slate-800"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      )}

      {error && <p className="text-xs text-rose-600 dark:text-rose-400">{error}</p>}
      {degradedReason && (
        <p className="text-xs text-amber-600 dark:text-amber-400">{degradedReason}</p>
      )}
      {indexStatus?.state === "indexing" && (
        <p className="text-xs text-slate-400">Indexando este workspace en segundo plano…</p>
      )}
      {truncated && (
        <p className="text-xs text-amber-600 dark:text-amber-400">
          Hay más resultados de los que se muestran: afina la búsqueda.
        </p>
      )}

      {mode === "simbolos" && (
        <>
          {/* Contexto SIEMPRE visible en este modo, no solo cuando el
              resultado sale vacío: la advertencia aplica de antemano, no es
              una excusa post-hoc (ver docstring del archivo). */}
          <p className="text-[11px] leading-4 text-slate-400 dark:text-slate-500">
            Busca símbolos ya indexados por el servidor de lenguaje de opencode.
            opencode solo indexa archivos que su LSP ya abrió dentro de esta
            sesión -- un resultado vacío no significa que el símbolo no exista.
          </p>
          {lspStatusError ? (
            <p className="text-[11px] text-amber-600 dark:text-amber-400">
              No se pudo leer el estado del LSP: {lspStatusError}
            </p>
          ) : lspServers === null ? null : lspServers.length === 0 ? (
            <p className="text-[11px] leading-4 text-amber-600 dark:text-amber-400">
              Ningún servidor de lenguaje está activo en este workspace todavía.
              Suele arrancar solo cuando el agente abre un archivo dentro de una
              sesión activa -- volvé a intentar después de eso.
            </p>
          ) : (
            <p className="text-[11px] text-slate-400 dark:text-slate-500">
              LSP conectado: {lspConnected.length > 0 ? lspConnected.map((s) => s.name).join(", ") : "ninguno (ver detalle abajo)"}
              {lspServers.some((s) => s.status === "error") && (
                <span className="ml-1 text-amber-600 dark:text-amber-400">
                  · con errores: {lspServers.filter((s) => s.status === "error").map((s) => s.name).join(", ")}
                </span>
              )}
            </p>
          )}
        </>
      )}

      {loading ? (
        <div className="flex justify-center py-6">
          <Spinner className="h-4 w-4 text-slate-400" />
        </div>
      ) : mode === "simbolos" ? (
        searched && symbols.length === 0 ? (
          <p className="text-xs text-slate-400">
            Sin símbolos encontrados con ese nombre (o el LSP no llegó a indexar el archivo -- ver la nota de arriba).
          </p>
        ) : (
          <ul className="max-h-80 space-y-1 overflow-y-auto">
            {symbols.map((symbol, i) => {
              const relative = lspUriToWorkspaceRelativePath(symbol.location.uri, workspacePath);
              const line = symbol.location.range.start.line + 1;
              return (
                <li key={`${symbol.name}:${symbol.location.uri}:${i}`}>
                  {relative !== null ? (
                    <button
                      type="button"
                      onClick={() => onOpenFile(relative)}
                      className="block w-full rounded-lg px-2 py-1.5 text-left text-xs hover:bg-forja-superficie-elevada dark:hover:bg-slate-800"
                    >
                      <span className="font-mono font-medium text-brand-600 dark:text-brand-400">
                        {symbol.name}
                      </span>
                      <span className="ml-2 text-slate-500 dark:text-slate-400">
                        {relative || "."}:{line}
                      </span>
                    </button>
                  ) : (
                    // No se pudo mapear la uri a una ruta del workspace (raíz
                    // desconocida, o el símbolo vive fuera del workspace):
                    // se muestra el dato real, pero SIN botón -- un botón acá
                    // abriría, en silencio, el archivo equivocado.
                    <div className="rounded-lg px-2 py-1.5 text-xs">
                      <span className="font-mono font-medium text-slate-600 dark:text-slate-300">
                        {symbol.name}
                      </span>
                      <span className="ml-2 break-all text-slate-400 dark:text-slate-500">
                        {symbol.location.uri}:{line}
                      </span>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )
      ) : searched && hits.length === 0 ? (
        <p className="text-xs text-slate-400">Sin coincidencias.</p>
      ) : (
        <ul className="max-h-80 space-y-1 overflow-y-auto">
          {hits.map((hit, i) => (
            <li key={`${hit.path}:${hit.line}:${i}`}>
              <button
                type="button"
                onClick={() => onOpenFile(hit.path)}
                className="block w-full rounded-lg px-2 py-1.5 text-left text-xs hover:bg-forja-superficie-elevada dark:hover:bg-slate-800"
              >
                <span className="font-mono font-medium text-brand-600 dark:text-brand-400">
                  {hit.path}:{hit.line}
                </span>
                {hit.score !== null && (
                  <span className="ml-2 text-[10px] text-slate-400">
                    {(hit.score * 100).toFixed(0)}%
                  </span>
                )}
                <span className="ml-2 text-slate-500 dark:text-slate-400">{hit.text}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
