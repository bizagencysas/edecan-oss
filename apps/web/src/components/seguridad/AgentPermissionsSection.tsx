/**
 * Permisos de los agentes (`GET /v1/agents/workers`): quiénes existen, en qué
 * estado están y con qué nivel de autonomía actúan. Solo lectura — editar la
 * autonomía se hace desde el detalle del compañero en `/app/workers`.
 */

"use client";

import { useEffect, useState } from "react";

import { Alert, Badge, Card, CardBody, CardHeader, Spinner } from "@/components/ui";
import { AgentAvatar } from "@/components/workers/AgentAvatar";
import { PresenceDot, presenceState } from "@/components/workers/PresenceDot";
import { autonomyLabel } from "@/components/workers/options";
import { listWorkers, type PersistentWorker } from "@/lib/api";

function autonomyVariant(level: PersistentWorker["autonomy_level"]): "danger" | "warning" | "brand" | "success" | "neutral" {
  if (level === "full") return "danger";
  if (level === "draft") return "brand";
  if (level === "read_only") return "warning";
  return "neutral";
}

export function AgentPermissionsSection() {
  const [workers, setWorkers] = useState<PersistentWorker[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listWorkers()
      .then((next) => {
        if (!cancelled) setWorkers(next);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "No se pudieron cargar los compañeros.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <Card>
      <CardHeader
        title="Permisos de los agentes"
        description="Cuánta libertad tiene cada compañero para actuar sin tu OK."
      />
      <CardBody>
        {error ? (
          <Alert variant="error">{error}</Alert>
        ) : workers === null ? (
          <div className="flex justify-center py-6">
            <Spinner className="h-4 w-4 text-slate-400" />
          </div>
        ) : workers.length === 0 ? (
          <p className="text-xs text-slate-400 dark:text-slate-500">
            Todavía no tienes compañeros persistentes.
          </p>
        ) : (
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {workers.map((worker) => {
              const state = presenceState(worker.status, worker.enabled);
              return (
                <li key={worker.id} className="flex items-center gap-3 py-2.5 first:pt-0 last:pb-0">
                  <AgentAvatar
                    name={worker.name}
                    displayName={worker.display_name}
                    avatar={worker.avatar}
                    size="sm"
                  />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-slate-800 dark:text-slate-100">
                      {worker.display_name?.trim() || worker.name}
                    </p>
                    <p className="flex items-center gap-1.5 truncate text-xs text-slate-500 dark:text-slate-400">
                      <PresenceDot state={state} />
                      {worker.role_title?.trim() || worker.purpose}
                    </p>
                  </div>
                  <Badge variant={autonomyVariant(worker.autonomy_level)}>
                    {autonomyLabel(worker.autonomy_level)}
                  </Badge>
                </li>
              );
            })}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}