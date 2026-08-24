"use client";

/**
 * `/app/automatizaciones` (`ROADMAP_V2.md` §7.10, WP-V2-07): lista + form de
 * creación + detalle con feed de corridas. Sin ruta dinámica `[id]`: no hay
 * ningún precedente de rutas dinámicas en `apps/web/src/app` todavía, así que
 * se sigue el mismo patrón maestro-detalle que ya usa `/app/remoto`
 * (`ConsentGate`/`RemoteViewer` por estado local, no por URL) — `selected`
 * decide si se muestra la lista+form o el detalle de una automatización.
 */

import { useCallback, useEffect, useState } from "react";

import { AutomationDetail } from "@/components/automatizaciones/AutomationDetail";
import { AutomationForm } from "@/components/automatizaciones/AutomationForm";
import { AutomationList } from "@/components/automatizaciones/AutomationList";
import { Alert, Button, Card, CardBody, CardHeader, PageHeader, Spinner } from "@/components/ui";
import {
  ApiError,
  FLAG_AUTOMATIONS_RULES,
  createAutomation,
  deleteAutomation,
  listAutomationRuns,
  listAutomationSuggestions,
  listAutomations,
  probarAutomation,
  updateAutomation,
  type Automation,
  type AutomationRun,
  type CreateAutomationInput,
  type AutomationSuggestion,
} from "@/lib/api-automatizaciones";
import { useAuth } from "@/lib/auth-context";

function describeError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "Ocurrió un error inesperado.";
}

export default function AutomatizacionesPage() {
  const { me, loading: authLoading } = useAuth();
  const hasFlag = Boolean(me?.flags?.[FLAG_AUTOMATIONS_RULES]);

  const [items, setItems] = useState<Automation[]>([]);
  const [suggestions, setSuggestions] = useState<AutomationSuggestion[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [selected, setSelected] = useState<Automation | null>(null);
  const [runs, setRuns] = useState<AutomationRun[]>([]);
  const [runsLoading, setRunsLoading] = useState(false);
  const [probando, setProbando] = useState(false);
  const [toggling, setToggling] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [automations, nextSuggestions] = await Promise.all([
        listAutomations(),
        listAutomationSuggestions(),
      ]);
      setItems(automations);
      setSuggestions(nextSuggestions);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (hasFlag) void load();
    else setLoading(false);
  }, [hasFlag, load]);

  async function loadRuns(automationId: string) {
    setRunsLoading(true);
    try {
      setRuns(await listAutomationRuns(automationId));
    } catch (err) {
      setError(describeError(err));
    } finally {
      setRunsLoading(false);
    }
  }

  async function handleCreate(input: CreateAutomationInput) {
    setCreating(true);
    setError(null);
    try {
      const created = await createAutomation(input);
      setItems((prev) => [created, ...prev]);
      setSelected(created); // así se ve el hook_secret (si aplica) de una sola vez.
      void loadRuns(created.id);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setCreating(false);
    }
  }

  async function handleToggleEnabled(automation: Automation, enabled: boolean) {
    setBusyId(automation.id);
    setError(null);
    try {
      const updated = await updateAutomation(automation.id, { enabled });
      setItems((prev) => prev.map((a) => (a.id === updated.id ? updated : a)));
      setSelected((prev) => (prev && prev.id === updated.id ? updated : prev));
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusyId(null);
    }
  }

  async function handleDetailToggle(enabled: boolean) {
    if (!selected) return;
    setToggling(true);
    setError(null);
    try {
      const updated = await updateAutomation(selected.id, { enabled });
      setSelected(updated);
      setItems((prev) => prev.map((a) => (a.id === updated.id ? updated : a)));
    } catch (err) {
      setError(describeError(err));
    } finally {
      setToggling(false);
    }
  }

  async function handleDelete(automation: Automation) {
    setBusyId(automation.id);
    setError(null);
    try {
      await deleteAutomation(automation.id);
      setItems((prev) => prev.filter((a) => a.id !== automation.id));
      setSelected((prev) => (prev && prev.id === automation.id ? null : prev));
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusyId(null);
    }
  }

  async function handleDetailDelete() {
    if (!selected) return;
    setDeleting(true);
    try {
      await deleteAutomation(selected.id);
      setItems((prev) => prev.filter((a) => a.id !== selected.id));
      setSelected(null);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setDeleting(false);
    }
  }

  async function handleProbar() {
    if (!selected) return;
    setProbando(true);
    setError(null);
    try {
      await probarAutomation(selected.id);
      void loadRuns(selected.id);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setProbando(false);
    }
  }

  function handleSelect(automation: Automation) {
    // Al volver a seleccionar desde la lista, el `hook_secret` (si lo hubo)
    // ya no viaja del backend (solo la respuesta del POST/PATCH que lo
    // generó lo trae) — se limpia acá para no arrastrar uno viejo del
    // estado anterior.
    setSelected({ ...automation, hook_secret: undefined });
    void loadRuns(automation.id);
  }

  function handleReviewSuggestion(automationId: string) {
    const automation = items.find((item) => item.id === automationId);
    if (automation) handleSelect(automation);
  }

  return (
    <div>
      <PageHeader
        title="Automatizaciones"
        description="Reglas de agenda o webhook que corren una instrucción del agente sin que tengas que estar mirando."
      />

      {error && (
        <div className="mb-4">
          <Alert variant="error">{error}</Alert>
        </div>
      )}

      {authLoading ? (
        <div className="flex justify-center py-16">
          <Spinner className="h-6 w-6 text-slate-400" />
        </div>
      ) : !hasFlag ? (
        <Card>
          <CardBody>
            <Alert variant="info">
              Las automatizaciones no están disponibles en tu plan actual
              {me ? ` (${me.tenant.plan_key})` : ""}. Mejora tu plan desde Ajustes → Facturación
              para activarlas.
            </Alert>
          </CardBody>
        </Card>
      ) : selected ? (
        <AutomationDetail
          automation={selected}
          runs={runs}
          runsLoading={runsLoading}
          onBack={() => setSelected(null)}
          onToggleEnabled={handleDetailToggle}
          onProbar={handleProbar}
          onDelete={handleDetailDelete}
          probando={probando}
          toggling={toggling}
          deleting={deleting}
        />
      ) : (
        <div className="space-y-6">
          {suggestions.length > 0 && (
              <div className="mb-6">
                <Card>
                  <CardHeader
                    title="Sugerencias de revisión"
                    description="Edecán detectó fallos consecutivos. Revisa antes de cambiar la regla."
                  />
                  <CardBody>
                    <ul className="space-y-3">
                      {suggestions.map((suggestion) => (
                        <li
                          key={suggestion.automation_id}
                          className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 dark:border-amber-900 dark:bg-amber-950/30"
                        >
                          <p className="text-sm font-medium text-amber-950 dark:text-amber-100">
                            {suggestion.nombre}: {suggestion.reason}
                          </p>
                          <p className="mt-1 text-xs text-amber-800 dark:text-amber-300">
                            {suggestion.failure_count} fallos consecutivos · requiere tu confirmación
                          </p>
                          <Button
                            className="mt-3"
                            size="sm"
                            variant="ghost"
                            onClick={() => handleReviewSuggestion(suggestion.automation_id)}
                          >
                            Revisar automatización
                          </Button>
                        </li>
                      ))}
                    </ul>
                  </CardBody>
                </Card>
              </div>
            )}
          <Card>
            <CardHeader
              title="Nueva automatización"
              description="Presets de frecuencia listos para usar, o una rrule personalizada."
            />
            <CardBody>
              <AutomationForm onCreate={handleCreate} creating={creating} />
            </CardBody>
          </Card>

          <AutomationList
            items={items}
            loading={loading}
            busyId={busyId}
            onToggleEnabled={handleToggleEnabled}
            onSelect={handleSelect}
            onDelete={handleDelete}
          />
        </div>
      )}
    </div>
  );
}
