/**
 * Sugerencias de memoria (`GET /v1/memory/suggestions`): el agente observa
 * hábitos y propone guardarlos. Cada una trae `{text, source, scope,
 * confidence}`. "Guardar" persiste con `POST /v1/memory`; "Ignorar" solo la
 * descarta de la vista. Si el endpoint todavía no existe (404), se muestra un
 * estado "Próximamente" honesto — nunca una lista vacía que parezca éxito.
 */

"use client";

import { useEffect, useState } from "react";

import { CheckIcon, XIcon } from "@/components/icons";
import { Alert, Button, Card, CardBody, CardHeader, Spinner } from "@/components/ui";
import { ApiError, addMemory, listMemorySuggestions, type MemorySuggestion } from "@/lib/api";

function confidenceLabel(confidence: number | null | undefined): string | null {
  if (typeof confidence !== "number" || Number.isNaN(confidence)) return null;
  return `${Math.round(Math.min(1, Math.max(0, confidence)) * 100)}%`;
}

export function MemorySuggestionsPanel() {
  const [suggestions, setSuggestions] = useState<MemorySuggestion[]>([]);
  const [loading, setLoading] = useState(true);
  const [upcoming, setUpcoming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savingIndex, setSavingIndex] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    listMemorySuggestions()
      .then((next) => {
        if (!cancelled) setSuggestions(next);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setUpcoming(true);
        } else {
          setError(err instanceof Error ? err.message : "No se pudieron cargar las sugerencias.");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function dismiss(index: number) {
    setSuggestions((prev) => prev.filter((_, i) => i !== index));
  }

  async function save(index: number, suggestion: MemorySuggestion) {
    setSavingIndex(index);
    setError(null);
    try {
      await addMemory({
        content: suggestion.text,
        source: suggestion.source ?? "suggestion",
        namespace: suggestion.scope || undefined,
      });
      dismiss(index);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo guardar la sugerencia.");
    } finally {
      setSavingIndex(null);
    }
  }

  return (
    <Card className="mb-6">
      <CardHeader
        title="Sugerencias de memoria"
        description="Lo que Edecán cree que vale la pena recordar de ti. Tú decides qué se guarda."
      />
      <CardBody>
        {error && <Alert variant="error">{error}</Alert>}
        {loading ? (
          <div className="flex justify-center py-4">
            <Spinner className="h-4 w-4 text-slate-400" />
          </div>
        ) : upcoming ? (
          <p className="text-sm text-slate-400 dark:text-slate-500">Próximamente</p>
        ) : suggestions.length === 0 ? (
          <p className="text-sm text-slate-400 dark:text-slate-500">
            Sin sugerencias por ahora. Aparecerán a medida que Edecán aprenda de tus conversaciones.
          </p>
        ) : (
          <ul className="space-y-2">
            {suggestions.map((suggestion, index) => {
              const confidence = confidenceLabel(suggestion.confidence);
              return (
                <li
                  key={`${suggestion.text}:${index}`}
                  className="rounded-lg border border-slate-100 px-3 py-2.5 dark:border-slate-800"
                >
                  <div className="flex items-start gap-3">
                    <p className="min-w-0 flex-1 text-sm text-slate-700 dark:text-slate-200">
                      {suggestion.text}
                    </p>
                    <div className="flex shrink-0 items-center gap-1">
                      <Button
                        type="button"
                        size="sm"
                        loading={savingIndex === index}
                        disabled={savingIndex !== null && savingIndex !== index}
                        onClick={() => void save(index, suggestion)}
                      >
                        <CheckIcon className="h-3.5 w-3.5" />
                        Guardar
                      </Button>
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        onClick={() => dismiss(index)}
                        aria-label="Ignorar sugerencia"
                      >
                        <XIcon className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-slate-400 dark:text-slate-500">
                    {suggestion.source && <span>Origen: {suggestion.source}</span>}
                    {suggestion.scope && <span>Ámbito: {suggestion.scope}</span>}
                    {confidence && <span>confianza {confidence}</span>}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}
