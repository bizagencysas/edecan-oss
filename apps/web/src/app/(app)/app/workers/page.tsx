"use client";

import { useEffect, useState } from "react";

import { BellIcon, PlusIcon, SparklesIcon } from "@/components/icons";
import { Alert, Badge, Button, EmptyState, PageHeader, Spinner } from "@/components/ui";
import { ActivityDrawer } from "@/components/activity/ActivityDrawer";
import { AgentCard } from "@/components/workers/AgentCard";
import { AgentMessagesSection } from "@/components/workers/AgentMessagesSection";
import { ApprovalsSection } from "@/components/workers/ApprovalsSection";
import { ChiefOfStaff } from "@/components/workers/ChiefOfStaff";
import { CreateWorkerSheet } from "@/components/workers/CreateWorkerSheet";
import { RoutinesLibrary } from "@/components/workers/RoutinesLibrary";
import { TeachTaskSheet } from "@/components/workers/TeachTaskSheet";
import { WorkerDetailSheet } from "@/components/workers/WorkerDetailSheet";
import { workerActivityEntry, type WorkerActivityEntry } from "@/components/workers/status";
import {
  approveWorkerHandoff,
  listWorkerHandoffs,
  listWorkers,
  type PersistentWorker,
  type WorkerHandoff,
} from "@/lib/api";
import { FLAG_AUTOMATIONS_RULES } from "@/lib/api-automatizaciones";
import { useAuth } from "@/lib/auth-context";
import { formatDateTime } from "@/lib/format";

export default function WorkersPage() {
  const { me } = useAuth();
  const allowed = Boolean(me?.flags?.["agents.missions"]);
  const routinesAllowed = Boolean(me?.flags?.[FLAG_AUTOMATIONS_RULES]);
  const [workers, setWorkers] = useState<PersistentWorker[]>([]);
  const [handoffs, setHandoffs] = useState<WorkerHandoff[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [teachOpen, setTeachOpen] = useState(false);
  const [selected, setSelected] = useState<PersistentWorker | null>(null);
  const [approving, setApproving] = useState<string | null>(null);
  const [activityOpen, setActivityOpen] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [nextWorkers, nextHandoffs] = await Promise.all([listWorkers(), listWorkerHandoffs()]);
      setWorkers(nextWorkers);
      setHandoffs(nextHandoffs);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudieron cargar los compañeros.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (allowed) void load();
    else setLoading(false);
  }, [allowed]);

  function handleWorkerChanged(updated: PersistentWorker) {
    setWorkers((prev) => prev.map((w) => (w.id === updated.id ? updated : w)));
    setSelected(updated);
  }

  const activityEntries: WorkerActivityEntry[] = workers
    .flatMap((worker) => {
      const entry = workerActivityEntry(worker);
      return entry ? [entry] : [];
    })
    .sort((a, b) => {
      const at = a.timestamp ? Date.parse(a.timestamp) : Number.NEGATIVE_INFINITY;
      const bt = b.timestamp ? Date.parse(b.timestamp) : Number.NEGATIVE_INFINITY;
      return (Number.isNaN(bt) ? Number.NEGATIVE_INFINITY : bt) - (Number.isNaN(at) ? Number.NEGATIVE_INFINITY : at);
    })
    .slice(0, 8);

  if (!allowed) {
    return (
      <div className="mx-auto max-w-3xl p-6">
        <PageHeader title="Tu equipo" description="Los compañeros persistentes no están en este plan." />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6">
      <PageHeader
        title="Tu equipo"
        description="Compañeros que siguen trabajando cuando cierras el chat. Aprueba solo lo que necesita tu OK."
        actions={
          <>
            <Button variant="secondary" onClick={() => setActivityOpen(true)}>
              <BellIcon className="h-4 w-4" />
              Actividad
            </Button>
            <Button variant="secondary" onClick={() => setTeachOpen(true)}>
              <SparklesIcon className="h-4 w-4" />
              Enseñar tarea
            </Button>
            <Button onClick={() => setCreateOpen(true)}>
              <PlusIcon className="h-4 w-4" />
              Nuevo compañero
            </Button>
          </>
        }
      />

      {error && <Alert variant="error">{error}</Alert>}

      {!loading && <ChiefOfStaff workers={workers} handoffs={handoffs} canAutomations={routinesAllowed} />}

      <ApprovalsSection />

      {handoffs.length > 0 && (
        <section aria-label="Handoffs pendientes">
          <h2 className="mb-2 text-sm font-semibold text-slate-900 dark:text-slate-100">
            Necesitan tu OK
          </h2>
          <ul className="space-y-2">
            {handoffs.map((handoff) => {
              const instruction =
                typeof handoff.envelope === "object" && handoff.envelope
                  ? handoff.envelope.instruction
                  : null;
              const approvingThis = approving === handoff.id;
              return (
                <li
                  key={handoff.id}
                  className="flex items-start justify-between gap-3 rounded-xl border border-slate-200 bg-white px-3 py-2.5 shadow-panel dark:border-slate-800 dark:bg-slate-900"
                >
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-slate-900 dark:text-slate-100">
                      {handoff.destination_name || handoff.destination_worker_id}
                    </p>
                    <p className="mt-0.5 truncate text-xs text-slate-500 dark:text-slate-400">
                      {instruction || handoff.task_id}
                    </p>
                    <p className="mt-0.5 text-[11px] text-slate-400 dark:text-slate-500">
                      {formatDateTime(handoff.created_at)}
                    </p>
                  </div>
                  <Button
                    size="sm"
                    loading={approvingThis}
                    onClick={() => {
                      setApproving(handoff.id);
                      void approveWorkerHandoff(handoff.id)
                        .then(() => load())
                        .catch((err) =>
                          setError(err instanceof Error ? err.message : "No se pudo aprobar."),
                        )
                        .finally(() => setApproving(null));
                    }}
                  >
                    Aprobar
                  </Button>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      <section aria-label="Compañeros">
        {loading ? (
          <div className="flex justify-center py-12">
            <Spinner className="h-5 w-5 text-slate-400" />
          </div>
        ) : workers.length === 0 ? (
          <EmptyState
            title="Crea tu primer compañero"
            description="Descríbele el trabajo que quieras dejar en otras manos y Edecan lo mantiene avanzando aunque cierres el chat."
            action={
              <Button onClick={() => setCreateOpen(true)}>
                <PlusIcon className="h-4 w-4" />
                Crear mi primer compañero
              </Button>
            }
          />
        ) : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {workers.map((worker) => (
              <AgentCard key={worker.id} worker={worker} onSelect={setSelected} />
            ))}
          </div>
        )}
      </section>

      <section aria-label="Actividad reciente">
        <h2 className="mb-2 text-sm font-semibold text-slate-900 dark:text-slate-100">Actividad reciente</h2>
        {activityEntries.length === 0 ? (
          <p className="text-xs text-slate-400 dark:text-slate-500">
            Todavía no hay actividad. Encola una tarea desde el detalle de un compañero y su avance aparecerá aquí.
          </p>
        ) : (
          <ul className="space-y-2">
            {activityEntries.map((entry) => (
              <li
                key={entry.id}
                className="flex items-start justify-between gap-3 rounded-xl border border-slate-200 bg-white px-3 py-2.5 shadow-panel dark:border-slate-800 dark:bg-slate-900"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-slate-900 dark:text-slate-100">
                    {entry.workerName}
                  </p>
                  <p className="mt-0.5 truncate text-xs text-slate-500 dark:text-slate-400">
                    {entry.detail}
                  </p>
                  {entry.timestamp && (
                    <p className="mt-0.5 text-[11px] text-slate-400 dark:text-slate-500">
                      {formatDateTime(entry.timestamp)}
                    </p>
                  )}
                </div>
                <Badge
                  variant={entry.tone === "error" ? "danger" : entry.tone === "active" ? "brand" : "success"}
                >
                  {entry.tone === "error" ? "Requiere revisión" : entry.tone === "active" ? "En curso" : "Completada"}
                </Badge>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-label="Rutinas">
        {routinesAllowed ? (
          <RoutinesLibrary />
        ) : (
          <Alert variant="info">
            Las rutinas (automatizaciones) no están disponibles en tu plan.
          </Alert>
        )}
      </section>

      <section aria-label="Mensajes entre agentes">
        <AgentMessagesSection workers={workers} />
      </section>

      {createOpen && (
        <CreateWorkerSheet
          onClose={() => setCreateOpen(false)}
          onCreated={(worker) => {
            setWorkers((prev) => [worker, ...prev.filter((w) => w.id !== worker.id)]);
            setCreateOpen(false);
          }}
        />
      )}

      {teachOpen && <TeachTaskSheet onClose={() => setTeachOpen(false)} />}

      {selected && (
        <WorkerDetailSheet
          worker={selected}
          onClose={() => setSelected(null)}
          onChanged={handleWorkerChanged}
          onOpenActivity={() => setActivityOpen(true)}
        />
      )}

      <ActivityDrawer open={activityOpen} onClose={() => setActivityOpen(false)} />
    </div>
  );
}