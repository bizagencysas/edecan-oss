/**
 * Biblioteca de rutinas (`GET /v1/automations` + `GET /v1/automations/
 * suggestions`): tarjetas de cada automatización (nombre, disparo, próxima/
 * última corrida, estado) y sugerencias descartables. Las sugerencias son
 * SOLO informativas — nunca crean ni activan una automatización.
 */

"use client";

import { useEffect, useState } from "react";

import { XIcon, ZapIcon } from "@/components/icons";
import { Alert, Badge, Button, Card, CardBody, CardHeader, EmptyState, Spinner } from "@/components/ui";
import {
  listAutomationSuggestions,
  listAutomations,
  triggerResumen,
  type Automation,
  type AutomationSuggestion,
  type AutomationSuggestionStage,
} from "@/lib/api-automatizaciones";
import { formatDateTime } from "@/lib/format";

const STAGE_LABEL: Record<AutomationSuggestionStage, string> = {
  observation: "Observación",
  suggestion: "Sugerencia",
  draft: "Borrador",
  action: "Acción",
};

const STAGE_VARIANT: Record<
  AutomationSuggestionStage,
  "neutral" | "brand" | "warning" | "danger"
> = {
  observation: "neutral",
  suggestion: "brand",
  draft: "neutral",
  action: "danger",
};

function suggestionTitle(suggestion: AutomationSuggestion): string {
  if (suggestion.kind === "routine_suggestion") return suggestion.task;
  return suggestion.nombre;
}

function suggestionMeta(suggestion: AutomationSuggestion): string {
  if (suggestion.kind === "routine_suggestion") {
    return `${suggestion.repetitions} repeticiones recientes · ${suggestion.reason}`;
  }
  return `${suggestion.failure_count} fallos consecutivos · ${suggestion.reason}`;
}

function SuggestionCard({
  suggestion,
  onDismiss,
}: {
  suggestion: AutomationSuggestion;
  onDismiss: () => void;
}) {
  // El backend nuevo etiqueta cada sugerencia con una `stage` del flujo
  // proactivo; el rendering por etapa es más fiel que el `kind` anterior.
  const stage = suggestion.stage;
  if (stage) {
    const title = suggestionTitle(suggestion);
    const meta = suggestionMeta(suggestion);
    return (
      <li className="rounded-lg border border-slate-100 bg-white px-3 py-2.5 dark:border-slate-800 dark:bg-slate-900">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <Badge variant={STAGE_VARIANT[stage]}>{STAGE_LABEL[stage]}</Badge>
            <p
              className={`mt-1.5 text-sm ${
                stage === "observation"
                  ? "font-normal text-slate-500 dark:text-slate-400"
                  : "font-medium text-slate-900 dark:text-slate-100"
              }`}
            >
              {title}
            </p>
            {stage === "observation" && (
              <p className="mt-0.5 text-xs text-slate-400 dark:text-slate-500">{meta}</p>
            )}
            {stage === "suggestion" && (
              <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                ¿Quieres que lo haga? Pídeselo en el chat; aquí solo lo ves, tú decides.
              </p>
            )}
            {stage === "draft" && (
              <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                Borrador listo para revisar. {meta}
              </p>
            )}
            {stage === "action" && (
              <p className="mt-0.5 text-xs font-medium text-rose-600 dark:text-rose-400">
                Requiere tu atención. {meta}
              </p>
            )}
          </div>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={onDismiss}
            aria-label="Descartar sugerencia"
          >
            <XIcon className="h-3.5 w-3.5" />
          </Button>
        </div>
      </li>
    );
  }

  return (
    <li className="rounded-lg border border-slate-100 bg-white px-3 py-2.5 dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          {suggestion.kind === "routine_suggestion" ? (
            <>
              <Badge variant="brand">Convertir en rutina</Badge>
              <p className="mt-1.5 text-sm font-medium text-slate-900 dark:text-slate-100">
                {suggestion.task}
              </p>
              <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                {suggestion.repetitions} repeticiones recientes · {suggestion.reason}
              </p>
            </>
          ) : (
            <>
              <Badge variant="warning">Revisar automatización</Badge>
              <p className="mt-1.5 text-sm font-medium text-slate-900 dark:text-slate-100">
                {suggestion.nombre}
              </p>
              <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                {suggestion.failure_count} fallos consecutivos · {suggestion.reason}
              </p>
            </>
          )}
        </div>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onDismiss}
          aria-label="Descartar sugerencia"
        >
          <XIcon className="h-3.5 w-3.5" />
        </Button>
      </div>
    </li>
  );
}

export function RoutinesLibrary() {
  const [automations, setAutomations] = useState<Automation[]>([]);
  const [suggestions, setSuggestions] = useState<AutomationSuggestion[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const [items, sug] = await Promise.all([listAutomations(), listAutomationSuggestions()]);
        if (!cancelled) {
          setAutomations(items);
          setSuggestions(sug);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "No se pudieron cargar las rutinas.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  function dismiss(suggestion: AutomationSuggestion) {
    setSuggestions((prev) =>
      prev.filter((s) => {
        if (s.kind === "routine_suggestion" && suggestion.kind === "routine_suggestion") {
          return s.task !== suggestion.task;
        }
        if (
          s.kind === "automation_suggestion" &&
          suggestion.kind === "automation_suggestion"
        ) {
          return s.automation_id !== suggestion.automation_id;
        }
        return true;
      }),
    );
  }

  return (
    <div className="space-y-4">
      {error && <Alert variant="error">{error}</Alert>}

      {suggestions.length > 0 && (
        <Card>
          <CardHeader
            title="Sugerencias"
            description="Rutinas que Edecán cree que podrías automatizar. Aquí solo las ves; tú decides."
          />
          <CardBody>
            <ul className="space-y-2">
              {suggestions.map((suggestion) => (
                <SuggestionCard
                  key={
                    suggestion.kind === "routine_suggestion"
                      ? `routine:${suggestion.task}`
                      : suggestion.automation_id
                  }
                  suggestion={suggestion}
                  onDismiss={() => dismiss(suggestion)}
                />
              ))}
            </ul>
          </CardBody>
        </Card>
      )}

      <Card>
        <CardHeader
          title="Rutinas"
          description="Automatizaciones de agenda o webhook que corren una instrucción del agente."
        />
        <CardBody>
          {loading ? (
            <div className="flex justify-center py-8">
              <Spinner className="h-5 w-5 text-slate-400" />
            </div>
          ) : automations.length === 0 ? (
            <EmptyState
              title="Sin rutinas todavía"
              description="Créalas desde Automatizaciones, o pídeselo a tu asistente en el chat."
            />
          ) : (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {automations.map((automation) => {
                const fallos = automation.consecutive_failures ?? 0;
                return (
                  <div
                    key={automation.id}
                    className="rounded-xl border border-slate-200 bg-white p-3.5 shadow-panel dark:border-slate-800 dark:bg-slate-900"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand-50 text-brand-600 dark:bg-brand-900/50 dark:text-brand-300">
                        <ZapIcon className="h-4 w-4" />
                      </span>
                      <Badge variant={automation.enabled ? "success" : "neutral"}>
                        {automation.enabled ? "Activa" : "Pausada"}
                      </Badge>
                    </div>
                    <p className="mt-2 truncate text-sm font-semibold text-slate-900 dark:text-slate-100">
                      {automation.nombre}
                    </p>
                    <p className="mt-0.5 truncate text-xs text-slate-500 dark:text-slate-400">
                      {triggerResumen(automation.trigger)}
                    </p>
                    <div className="mt-2 space-y-0.5 text-[11px] text-slate-400 dark:text-slate-500">
                      {automation.next_run_at && (
                        <p>Próxima: {formatDateTime(automation.next_run_at)}</p>
                      )}
                      {automation.last_run_at && (
                        <p>Última: {formatDateTime(automation.last_run_at)}</p>
                      )}
                      {fallos > 0 && (
                        <p className="font-medium text-amber-600 dark:text-amber-400">
                          {fallos} fallos consecutivos
                        </p>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </CardBody>
      </Card>
    </div>
  );
}