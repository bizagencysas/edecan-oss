/**
 * `/app/computer` — plano de control de sesiones (`GET /v1/computer/sessions`
 * y transiciones takeover/return/pause/resume/end). No es la vista remota
 * (`/app/remoto`): aquí solo se decide quién mueve cada superficie.
 * 404 → "Próximamente". Lista vacía ≠ backend ausente.
 */

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
  PageHeader,
  Select,
  Spinner,
} from "@/components/ui";
import { formatDateTime } from "@/lib/format";
import {
  createComputerSession,
  endComputerSession,
  isNotFound,
  listComputerSessions,
  pauseComputerSession,
  resumeComputerSession,
  returnComputerSession,
  takeoverComputerSession,
  type ComputerSession,
} from "@/lib/api-computer";

const MODE_LABELS: Record<string, string> = {
  agent: "Control del agente",
  user: "Control tuyo",
  paused: "En pausa",
};

const KINDS = ["desktop", "browser", "terminal", "files"] as const;

function modeVariant(mode: string): "brand" | "success" | "warning" | "neutral" {
  if (mode === "agent") return "brand";
  if (mode === "user") return "success";
  if (mode === "paused") return "warning";
  return "neutral";
}

export default function ComputerPage() {
  const [items, setItems] = useState<ComputerSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [upcoming, setUpcoming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [kind, setKind] = useState<(typeof KINDS)[number]>("desktop");
  const [saving, setSaving] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

  useEffect(() => {
    void load();
  }, []);

  async function load(background = false) {
    if (!background) setLoading(true);
    setError(null);
    try {
      const next = await listComputerSessions();
      setUpcoming(false);
      setItems(next);
    } catch (err) {
      if (isNotFound(err)) {
        setUpcoming(true);
        setItems([]);
      } else {
        setError(err instanceof Error ? err.message : "No se pudieron cargar las sesiones.");
      }
    } finally {
      if (!background) setLoading(false);
    }
  }

  function replace(updated: ComputerSession) {
    setItems((prev) => prev.map((row) => (row.id === updated.id ? updated : row)));
  }

  async function runAction(
    id: string,
    action: (sessionId: string) => Promise<ComputerSession>,
    fallback: string,
  ) {
    setBusyId(id);
    setError(null);
    try {
      replace(await action(id));
    } catch (err) {
      setError(err instanceof Error ? err.message : fallback);
    } finally {
      setBusyId(null);
    }
  }

  async function handleCreate(event: React.FormEvent) {
    event.preventDefault();
    if (upcoming) return;
    setSaving(true);
    setError(null);
    try {
      await createComputerSession({ kind });
      await load(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo crear la sesión.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="Computadora"
        description="Quién mueve cada superficie ahora. La vista en vivo está en Control remoto."
      />
      {error && (
        <div className="mb-4">
          <Alert variant="error">{error}</Alert>
        </div>
      )}

      {loading ? (
        <div className="flex justify-center py-12">
          <Spinner className="h-5 w-5 text-slate-400" />
        </div>
      ) : upcoming ? (
        <Card>
          <CardBody>
            <p className="text-sm text-slate-400 dark:text-slate-500">Próximamente</p>
          </CardBody>
        </Card>
      ) : (
        <>
          <Card className="mb-6">
            <CardHeader title="Nueva sesión de control" />
            <CardBody>
              <form onSubmit={(event) => void handleCreate(event)} className="flex flex-col gap-3 sm:flex-row">
                <Field label="Superficie" htmlFor="computer-kind" className="min-w-0 flex-1">
                  <Select
                    id="computer-kind"
                    value={kind}
                    onChange={(event) => setKind(event.target.value as (typeof KINDS)[number])}
                  >
                    {KINDS.map((value) => (
                      <option key={value} value={value}>
                        {value}
                      </option>
                    ))}
                  </Select>
                </Field>
                <div className="flex items-end">
                  <Button type="submit" loading={saving} className="w-full sm:w-auto">
                    Crear
                  </Button>
                </div>
              </form>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Sesiones" />
            <CardBody>
              {items.length === 0 ? (
                <EmptyState
                  title="Sin sesiones"
                  description="Crea una arriba. Una lista vacía no significa que el plano de control esté ausente."
                />
              ) : (
                <ul className="space-y-2">
                  {items.map((session) => {
                    const ended = session.status === "ended";
                    const paused = session.mode === "paused" || session.status === "paused";
                    const busy = busyId === session.id;
                    return (
                      <li
                        key={session.id}
                        className="flex flex-col gap-3 rounded-lg border border-slate-100 px-3 py-2.5 sm:flex-row sm:items-center sm:justify-between dark:border-slate-800"
                      >
                        <div className="min-w-0">
                          <p className="truncate text-sm font-medium text-slate-700 dark:text-slate-200">
                            {session.kind || "Sesión"}
                          </p>
                          <p className="text-[11px] text-slate-400 dark:text-slate-500">
                            {session.status}
                            {session.agent_id ? ` · agente ${session.agent_id.slice(0, 8)}` : ""}
                            {` · ${formatDateTime(session.updated_at || session.created_at)}`}
                          </p>
                        </div>
                        <div className="flex flex-wrap items-center gap-2">
                          <Badge variant={modeVariant(session.mode)}>
                            {MODE_LABELS[session.mode] ?? session.mode}
                          </Badge>
                          {!ended && !paused && session.mode !== "user" && (
                            <Button
                              type="button"
                              size="sm"
                              variant="secondary"
                              loading={busy}
                              onClick={() =>
                                void runAction(session.id, takeoverComputerSession, "No se pudo tomar el control.")
                              }
                            >
                              Tomar control
                            </Button>
                          )}
                          {!ended && session.mode === "user" && (
                            <Button
                              type="button"
                              size="sm"
                              variant="secondary"
                              loading={busy}
                              onClick={() =>
                                void runAction(session.id, returnComputerSession, "No se pudo devolver el control.")
                              }
                            >
                              Devolver
                            </Button>
                          )}
                          {!ended && !paused && (
                            <Button
                              type="button"
                              size="sm"
                              variant="secondary"
                              loading={busy}
                              onClick={() =>
                                void runAction(session.id, pauseComputerSession, "No se pudo pausar.")
                              }
                            >
                              Pausar
                            </Button>
                          )}
                          {!ended && paused && (
                            <Button
                              type="button"
                              size="sm"
                              variant="secondary"
                              loading={busy}
                              onClick={() =>
                                void runAction(session.id, resumeComputerSession, "No se pudo reanudar.")
                              }
                            >
                              Reanudar
                            </Button>
                          )}
                          {!ended && (
                            <Button
                              type="button"
                              size="sm"
                              variant="danger"
                              loading={busy}
                              onClick={() =>
                                void runAction(session.id, endComputerSession, "No se pudo cerrar la sesión.")
                              }
                            >
                              Cerrar
                            </Button>
                          )}
                        </div>
                      </li>
                    );
                  })}
                </ul>
              )}
            </CardBody>
          </Card>
        </>
      )}
    </div>
  );
}
