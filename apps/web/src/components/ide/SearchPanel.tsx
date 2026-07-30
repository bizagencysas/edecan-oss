"use client";

/**
 * Búsqueda en el sandbox del companion, con dos modos (2.2 del plan de
 * paridad de IDE):
 * - "Texto" (`POST /v1/ide/search`): substring literal, ya existía.
 * - "Significado" (`POST /v1/ide/workspaces/{id}/search/semantic`): por
 *   significado, vía `ide_busqueda_semantica.SemanticSearchService`. Solo se
 *   ofrece cuando se conoce el `workspaceId` -- la ruta semántica es
 *   por-workspace explícito, a diferencia de la de texto (que opera sobre
 *   "el workspace activo" del companion).
 *
 * Ambos resultados se normalizan a la misma forma (`Hit`) para no duplicar
 * el renderizado de la lista.
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

type Mode = "texto" | "significado";

interface Hit {
  path: string;
  line: number;
  text: string;
  score: number | null;
}

export function SearchPanel({
  workspaceId,
  onOpenFile,
}: {
  workspaceId?: string | null;
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

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const q = query.trim();
    if (!q) return;
    setLoading(true);
    setError(null);
    setDegradedReason(null);
    try {
      if (mode === "significado" && workspaceId) {
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

  return (
    <div className="flex flex-col gap-3">
      <form onSubmit={handleSubmit} className="flex items-center gap-2">
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={mode === "texto" ? "Buscar texto en los archivos…" : "Buscar por significado…"}
          aria-label="Buscar en los archivos"
        />
        <Button type="submit" size="sm" loading={loading} disabled={!query.trim()}>
          <SearchIcon className="h-4 w-4" />
        </Button>
      </form>

      {workspaceId && (
        <div className="flex gap-1 text-xs">
          {([
            ["texto", "Texto"],
            ["significado", "Significado"],
          ] as const).map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => {
                setMode(value);
                setSearched(false);
                setHits([]);
              }}
              className={`rounded-md px-2.5 py-1 font-semibold ${
                mode === value
                  ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900"
                  : "text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
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

      {loading ? (
        <div className="flex justify-center py-6">
          <Spinner className="h-4 w-4 text-slate-400" />
        </div>
      ) : searched && hits.length === 0 ? (
        <p className="text-xs text-slate-400">Sin coincidencias.</p>
      ) : (
        <ul className="max-h-80 space-y-1 overflow-y-auto">
          {hits.map((hit, i) => (
            <li key={`${hit.path}:${hit.line}:${i}`}>
              <button
                type="button"
                onClick={() => onOpenFile(hit.path)}
                className="block w-full rounded-lg px-2 py-1.5 text-left text-xs hover:bg-slate-100 dark:hover:bg-slate-800"
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
