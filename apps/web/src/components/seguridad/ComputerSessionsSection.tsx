/**
 * Computadora local (`GET /v1/computer/sessions`): sesiones de toma de control
 * por agente y por superficie, con su modo actual (`agent` | `user` |
 * `paused`). Solo lectura; pausar/reanudar una sesión concreta vive en
 * `/app/computer`.
 */

"use client";

import { useEffect, useState } from "react";

import { ComputerIcon } from "@/components/icons";
import { Alert, Badge, Card, CardBody, CardHeader, Spinner } from "@/components/ui";
import { listComputerSessions, type ComputerSession } from "@/lib/api-computer";
import { formatDateTime } from "@/lib/format";

const MODE_LABELS: Record<string, string> = {
  agent: "Control del agente",
  user: "Control tuyo",
  paused: "En pausa",
};

function modeVariant(mode: string): "brand" | "success" | "warning" | "neutral" {
  if (mode === "agent") return "brand";
  if (mode === "user") return "success";
  if (mode === "paused") return "warning";
  return "neutral";
}

export function ComputerSessionsSection() {
  const [sessions, setSessions] = useState<ComputerSession[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listComputerSessions()
      .then((next) => {
        if (!cancelled) setSessions(next);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "No se pudieron cargar las sesiones.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <Card>
      <CardHeader
        title="Computadora local"
        description="Sesiones de toma de control entre tú y Edecan en este equipo."
      />
      <CardBody>
        {error ? (
          <Alert variant="error">{error}</Alert>
        ) : sessions === null ? (
          <div className="flex justify-center py-6">
            <Spinner className="h-4 w-4 text-slate-400" />
          </div>
        ) : sessions.length === 0 ? (
          <p className="flex items-center gap-2 text-xs text-slate-400 dark:text-slate-500">
            <ComputerIcon className="h-3.5 w-3.5" />
            No hay sesiones de control remoto activas.
          </p>
        ) : (
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {sessions.map((session) => (
              <li key={session.id} className="flex items-center justify-between gap-3 py-2.5 first:pt-0 last:pb-0">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-slate-800 dark:text-slate-100">
                    {session.kind || "Sesión"}
                  </p>
                  <p className="truncate text-xs text-slate-500 dark:text-slate-400">
                    {session.status} · {formatDateTime(session.updated_at || session.created_at)}
                  </p>
                </div>
                <Badge variant={modeVariant(session.mode)}>{MODE_LABELS[session.mode] ?? session.mode}</Badge>
              </li>
            ))}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}