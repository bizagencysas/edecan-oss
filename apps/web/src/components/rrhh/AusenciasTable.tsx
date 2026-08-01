"use client";

import { useState } from "react";

import { Badge, Button, EmptyState, Spinner } from "@/components/ui";
import type { Ausencia, AusenciaStatus, Empleado } from "@/lib/api-rrhh";
import { formatDate } from "@/lib/format";

const KIND_LABEL: Record<Ausencia["kind"], string> = {
  vacaciones: "Vacaciones",
  enfermedad: "Enfermedad",
  permiso: "Permiso",
  otro: "Otro",
};

const STATUS_LABEL: Record<AusenciaStatus, string> = {
  pending: "Pendiente",
  approved: "Aprobada",
  rejected: "Rechazada",
  cancelled: "Cancelada",
};

const STATUS_BADGE: Record<AusenciaStatus, "neutral" | "success" | "danger" | "warning"> = {
  pending: "warning",
  approved: "success",
  rejected: "danger",
  cancelled: "neutral",
};

export function AusenciasTable({
  ausencias,
  empleados,
  loading,
  onResolver,
}: {
  ausencias: Ausencia[];
  empleados: Empleado[];
  loading: boolean;
  onResolver: (ausencia: Ausencia, accion: "aprobar" | "rechazar") => Promise<void>;
}) {
  const [busyId, setBusyId] = useState<string | null>(null);
  const nombrePorId = new Map(empleados.map((e) => [e.id, e.nombre]));

  async function handleResolver(ausencia: Ausencia, accion: "aprobar" | "rechazar") {
    setBusyId(ausencia.id);
    try {
      await onResolver(ausencia, accion);
    } finally {
      setBusyId(null);
    }
  }

  if (loading) {
    return (
      <div className="flex justify-center py-8">
        <Spinner className="h-5 w-5 text-slate-400" />
      </div>
    );
  }

  if (ausencias.length === 0) {
    return (
      <EmptyState
        title="Sin ausencias todavía"
        description="Regístralas con el formulario de arriba."
      />
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-slate-100 text-xs uppercase tracking-wide text-slate-400 dark:border-slate-800">
            <th className="py-2 pr-3 font-medium">Empleado</th>
            <th className="py-2 pr-3 font-medium">Tipo</th>
            <th className="py-2 pr-3 font-medium">Desde</th>
            <th className="py-2 pr-3 font-medium">Hasta</th>
            <th className="py-2 pr-3 font-medium">Estado</th>
            <th className="py-2 pr-3 font-medium">Acciones</th>
          </tr>
        </thead>
        <tbody>
          {ausencias.map((a) => (
            <tr key={a.id} className="border-b border-slate-50 dark:border-slate-800/60">
              <td className="py-2 pr-3 text-slate-700 dark:text-slate-200">
                {nombrePorId.get(a.employee_id) ?? "—"}
              </td>
              <td className="py-2 pr-3 text-slate-700 dark:text-slate-200">
                {KIND_LABEL[a.kind]}
                {a.notas && <div className="text-xs text-slate-400">{a.notas}</div>}
              </td>
              <td className="py-2 pr-3 whitespace-nowrap text-slate-500">{formatDate(a.desde)}</td>
              <td className="py-2 pr-3 whitespace-nowrap text-slate-500">{formatDate(a.hasta)}</td>
              <td className="py-2 pr-3">
                <Badge variant={STATUS_BADGE[a.status]}>{STATUS_LABEL[a.status]}</Badge>
              </td>
              <td className="py-2 pr-3">
                {a.status === "pending" ? (
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Button
                      size="sm"
                      disabled={busyId === a.id}
                      onClick={() => handleResolver(a, "aprobar")}
                    >
                      Aprobar
                    </Button>
                    <Button
                      size="sm"
                      variant="secondary"
                      disabled={busyId === a.id}
                      onClick={() => handleResolver(a, "rechazar")}
                    >
                      Rechazar
                    </Button>
                    {busyId === a.id && <Spinner className="h-3.5 w-3.5 text-slate-400" />}
                  </div>
                ) : (
                  <span className="text-xs text-slate-400">—</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
