"use client";

/**
 * Historial de sesiones del tenant (`GET /v1/remote/sessions`) — refuerza el
 * principio de "visibilidad permanente" de `docs/control-remoto.md`: cada
 * sesión (activa, terminada o denegada) queda a la vista, no solo en
 * `audit_log`.
 */

import { Badge, Card, CardBody, CardHeader, EmptyState } from "@/components/ui";
import { formatDateTime } from "@/lib/format";
import type { RemoteSession } from "@/lib/api-remoto";

const STATUS_VARIANT: Record<string, "brand" | "success" | "neutral" | "danger"> = {
  pending: "brand",
  active: "success",
  ended: "neutral",
  denied: "danger",
};

export function SessionHistory({ items }: { items: RemoteSession[] }) {
  return (
    <Card>
      <CardHeader
        title="Historial de sesiones"
        description="Inicio, fin, frames y estado de cada sesión — también quedan en audit_log."
      />
      <CardBody>
        {items.length === 0 ? (
          <EmptyState
            title="Todavía no hay sesiones"
            description="Cuando inicies una arriba, va a aparecer en esta lista."
          />
        ) : (
          <ul className="space-y-2">
            {items.map((s) => (
              <li
                key={s.id}
                className="flex items-center justify-between gap-3 rounded-lg border border-slate-100 px-3 py-2.5 text-sm dark:border-slate-800"
              >
                <div className="min-w-0">
                  <p className="truncate font-medium text-slate-700 dark:text-slate-200">
                    {formatDateTime(s.created_at)}
                    {s.kind === "control" && (
                      <span className="ml-2 text-xs font-normal text-rose-600 dark:text-rose-400">
                        control remoto
                      </span>
                    )}
                  </p>
                  <p className="text-xs text-slate-400">
                    {s.frames_count} frame(s)
                    {s.started_at ? ` · empezó ${formatDateTime(s.started_at)}` : ""}
                    {s.ended_at ? ` · terminó ${formatDateTime(s.ended_at)}` : ""}
                  </p>
                </div>
                <Badge variant={STATUS_VARIANT[s.status] ?? "neutral"}>{s.status}</Badge>
              </li>
            ))}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}
