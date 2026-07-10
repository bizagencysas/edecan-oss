"use client";

/**
 * Búsqueda de texto en el sandbox del companion (`POST /v1/ide/search`):
 * resultados clicables que abren el archivo (en la línea encontrada) en el
 * editor.
 */

import { useState } from "react";

import { SearchIcon } from "@/components/icons";
import { Button, Input, Spinner } from "@/components/ui";
import { postIdeSearch, type IdeSearchMatch } from "@/lib/api-ide";

export function SearchPanel({ onOpenFile }: { onOpenFile: (path: string) => void }) {
  const [query, setQuery] = useState("");
  const [matches, setMatches] = useState<IdeSearchMatch[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searched, setSearched] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const q = query.trim();
    if (!q) return;
    setLoading(true);
    setError(null);
    try {
      const result = await postIdeSearch(q);
      setMatches(result.matches);
      setTruncated(result.truncated);
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
          placeholder="Buscar texto en los archivos…"
          aria-label="Buscar texto en los archivos"
        />
        <Button type="submit" size="sm" loading={loading} disabled={!query.trim()}>
          <SearchIcon className="h-4 w-4" />
        </Button>
      </form>

      {error && <p className="text-xs text-rose-600 dark:text-rose-400">{error}</p>}
      {truncated && (
        <p className="text-xs text-amber-600 dark:text-amber-400">
          Hay más resultados de los que se muestran: afina la búsqueda.
        </p>
      )}

      {loading ? (
        <div className="flex justify-center py-6">
          <Spinner className="h-4 w-4 text-slate-400" />
        </div>
      ) : searched && matches.length === 0 ? (
        <p className="text-xs text-slate-400">Sin coincidencias.</p>
      ) : (
        <ul className="max-h-80 space-y-1 overflow-y-auto">
          {matches.map((m, i) => (
            <li key={`${m.path}:${m.line}:${i}`}>
              <button
                type="button"
                onClick={() => onOpenFile(m.path)}
                className="block w-full rounded-lg px-2 py-1.5 text-left text-xs hover:bg-slate-100 dark:hover:bg-slate-800"
              >
                <span className="font-mono font-medium text-brand-600 dark:text-brand-400">
                  {m.path}:{m.line}
                </span>
                <span className="ml-2 text-slate-500 dark:text-slate-400">{m.texto}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
