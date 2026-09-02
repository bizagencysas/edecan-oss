/**
 * Panel de la memoria propia de un compañero (`GET /v1/memory?namespace=
 * agent:<id>`). El backend devuelve los pares `key`/`value` del dict JSONB
 * `persistent_agents.memory` — sin `id`/`source_trust`, y sin un `DELETE`
 * equivalente (`DELETE /v1/memory/{id}` apunta a `memory_items` por uuid, no a
 * esta estructura), así que este panel es de solo lectura.
 */

"use client";

import { useEffect, useState } from "react";

import { Alert, Spinner } from "@/components/ui";
import { listAgentMemory, type AgentMemoryEntry } from "@/lib/api";

function renderValue(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return "—";
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

export function MemoryPanel({ workerId }: { workerId: string }) {
  const [entries, setEntries] = useState<AgentMemoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const next = await listAgentMemory(workerId);
        if (!cancelled) setEntries(next);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "No se pudo cargar la memoria.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [workerId]);

  return (
    <div>
      {error && <Alert variant="error">{error}</Alert>}
      {loading ? (
        <div className="flex justify-center py-4">
          <Spinner className="h-4 w-4 text-slate-400" />
        </div>
      ) : entries.length === 0 ? (
        <p className="text-xs text-slate-400 dark:text-slate-500">
          Este compañero todavía no recuerda nada propio.
        </p>
      ) : (
        <ul className="space-y-1.5">
          {entries.map((entry) => (
            <li
              key={entry.key}
              className="rounded-lg border border-slate-100 px-3 py-2 dark:border-slate-800"
            >
              <p className="text-[11px] font-medium uppercase tracking-wide text-slate-400 dark:text-slate-500">
                {entry.key}
              </p>
              <p className="mt-0.5 whitespace-pre-wrap break-words text-xs text-slate-700 dark:text-slate-300">
                {renderValue(entry.value)}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}