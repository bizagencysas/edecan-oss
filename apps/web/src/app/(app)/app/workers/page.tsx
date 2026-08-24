"use client";

import { useEffect, useState } from "react";

import {
  Alert,
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  EmptyState,
  Field,
  Input,
  PageHeader,
  Spinner,
  Textarea,
} from "@/components/ui";
import {
  approveWorkerHandoff,
  createWorker,
  enqueueWorkerTask,
  listWorkerHandoffs,
  listWorkers,
  type PersistentWorker,
  type WorkerHandoff,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { formatDateTime } from "@/lib/format";

export default function WorkersPage() {
  const { me } = useAuth();
  const allowed = Boolean(me?.flags?.["agents.missions"]);
  const [workers, setWorkers] = useState<PersistentWorker[]>([]);
  const [handoffs, setHandoffs] = useState<WorkerHandoff[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [purpose, setPurpose] = useState("");
  const [task, setTask] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [nextWorkers, nextHandoffs] = await Promise.all([listWorkers(), listWorkerHandoffs()]);
      setWorkers(nextWorkers);
      setHandoffs(nextHandoffs);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudieron cargar los workers.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (allowed) void load();
    else setLoading(false);
  }, [allowed]);

  if (!allowed) {
    return (
      <div className="mx-auto max-w-3xl p-6">
        <PageHeader title="Equipo" description="Los compañeros persistentes no están en este plan." />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6">
      <PageHeader title="Equipo" description="Escríbeles como a un colega. Aprueba solo lo que necesita tu OK." />
      {error && <Alert variant="error">{error}</Alert>}
      <Card>
        <CardHeader title="Nuevo compañero" />
        <CardBody>
          <form
            className="grid gap-3"
            onSubmit={(event) => {
              event.preventDefault();
              if (!name.trim() || !purpose.trim()) return;
              setBusy(true);
              void createWorker({ name: name.trim(), purpose: purpose.trim() })
                .then(() => {
                  setName("");
                  setPurpose("");
                  return load();
                })
                .catch((err) => setError(err instanceof Error ? err.message : "No se pudo crear."))
                .finally(() => setBusy(false));
            }}
          >
            <Field label="Nombre">
              <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="Investigador nocturno" />
            </Field>
            <Field label="Para qué existe">
              <Textarea value={purpose} onChange={(event) => setPurpose(event.target.value)} rows={3} />
            </Field>
            <Button type="submit" loading={busy} disabled={!name.trim() || !purpose.trim()}>
              Crear
            </Button>
          </form>
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Handoffs pendientes" />
        <CardBody>
          {handoffs.length === 0 ? (
            <p className="text-sm text-slate-500">No hay handoffs esperando aprobación.</p>
          ) : (
            <ul className="space-y-2">
              {handoffs.map((handoff) => {
                const instruction =
                  typeof handoff.envelope === "object" && handoff.envelope
                    ? handoff.envelope.instruction
                    : null;
                return (
                  <li key={handoff.id} className="flex items-start justify-between gap-3 rounded-lg border border-slate-100 px-3 py-2 dark:border-slate-800">
                    <div>
                      <p className="text-sm font-medium">{handoff.destination_name || handoff.destination_worker_id}</p>
                      <p className="text-xs text-slate-500">{instruction || handoff.task_id}</p>
                    </div>
                    <Button
                      size="sm"
                      onClick={() => {
                        setBusy(true);
                        void approveWorkerHandoff(handoff.id)
                          .then(() => load())
                          .catch((err) => setError(err instanceof Error ? err.message : "No se pudo aprobar."))
                          .finally(() => setBusy(false));
                      }}
                      loading={busy}
                    >
                      Aprobar
                    </Button>
                  </li>
                );
              })}
            </ul>
          )}
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Compañeros" />
        <CardBody>
          {loading ? (
            <div className="flex justify-center py-8">
              <Spinner className="h-5 w-5 text-slate-400" />
            </div>
          ) : workers.length === 0 ? (
            <EmptyState title="Sin workers" description="Crea uno para trabajo que debe seguir cuando cierres el chat." />
          ) : (
            <ul className="space-y-2">
              {workers.map((worker) => (
                <li key={worker.id} className="rounded-lg border border-slate-100 px-3 py-2.5 dark:border-slate-800">
                  <button type="button" className="flex w-full items-center justify-between text-left" onClick={() => setSelectedId(worker.id)}>
                    <span className="font-medium">{worker.name}</span>
                    <Badge>{worker.status}</Badge>
                  </button>
                  <p className="mt-1 text-sm text-slate-600 dark:text-slate-300">{worker.purpose}</p>
                  <p className="text-[11px] text-slate-400">{formatDateTime(worker.updated_at)}</p>
                  {selectedId === worker.id && (
                    <form
                      className="mt-3 flex flex-wrap gap-2"
                      onSubmit={(event) => {
                        event.preventDefault();
                        if (!task.trim()) return;
                        setBusy(true);
                        void enqueueWorkerTask(worker.id, task.trim())
                          .then(() => setTask(""))
                          .catch((err) => setError(err instanceof Error ? err.message : "No se pudo encolar."))
                          .finally(() => setBusy(false));
                      }}
                    >
                      <Input
                        value={task}
                        onChange={(event) => setTask(event.target.value)}
                        placeholder="Instrucción para este worker…"
                        className="min-w-[16rem] flex-1"
                      />
                      <Button type="submit" size="sm" loading={busy} disabled={!task.trim()}>
                        Encolar
                      </Button>
                    </form>
                  )}
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
