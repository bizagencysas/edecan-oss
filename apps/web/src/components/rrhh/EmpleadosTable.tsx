"use client";

import { useState } from "react";

import { Badge, Button, EmptyState, Spinner } from "@/components/ui";
import type { Empleado } from "@/lib/api-rrhh";
import { formatDate, formatMoney } from "@/lib/format";

export function EmpleadosTable({
  empleados,
  loading,
  onEdit,
  onToggleStatus,
}: {
  empleados: Empleado[];
  loading: boolean;
  onEdit: (empleado: Empleado) => void;
  onToggleStatus: (empleado: Empleado) => Promise<void>;
}) {
  const [busyId, setBusyId] = useState<string | null>(null);

  async function handleToggle(empleado: Empleado) {
    setBusyId(empleado.id);
    try {
      await onToggleStatus(empleado);
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

  if (empleados.length === 0) {
    return (
      <EmptyState
        title="Sin empleados todavía"
        description="Crea el primero con el formulario de arriba."
      />
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-slate-100 text-xs uppercase tracking-wide text-slate-400 dark:border-slate-800">
            <th className="py-2 pr-3 font-medium">Nombre</th>
            <th className="py-2 pr-3 font-medium">Puesto</th>
            <th className="py-2 pr-3 text-right font-medium">Salario mensual</th>
            <th className="py-2 pr-3 font-medium">Ingreso</th>
            <th className="py-2 pr-3 font-medium">Estado</th>
            <th className="py-2 pr-3 font-medium">Acciones</th>
          </tr>
        </thead>
        <tbody>
          {empleados.map((e) => (
            <tr key={e.id} className="border-b border-slate-50 dark:border-slate-800/60">
              <td className="py-2 pr-3 text-slate-700 dark:text-slate-200">
                <div className="font-medium">{e.nombre}</div>
                {e.email && <div className="text-xs text-slate-400">{e.email}</div>}
              </td>
              <td className="py-2 pr-3 text-slate-700 dark:text-slate-200">{e.puesto || "—"}</td>
              <td className="py-2 pr-3 whitespace-nowrap text-right text-slate-700 dark:text-slate-200">
                {e.salario_mensual === null ? (
                  <span className="text-slate-400">sin asignar</span>
                ) : (
                  formatMoney(e.salario_mensual, e.moneda)
                )}
              </td>
              <td className="py-2 pr-3 whitespace-nowrap text-slate-500">
                {e.fecha_ingreso ? formatDate(e.fecha_ingreso) : "—"}
              </td>
              <td className="py-2 pr-3">
                <Badge variant={e.status === "active" ? "success" : "neutral"}>
                  {e.status === "active" ? "Activo" : "Inactivo"}
                </Badge>
              </td>
              <td className="py-2 pr-3">
                <div className="flex flex-wrap items-center gap-1.5">
                  <Button size="sm" variant="secondary" onClick={() => onEdit(e)}>
                    Editar
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busyId === e.id}
                    onClick={() => handleToggle(e)}
                  >
                    {e.status === "active" ? "Desactivar" : "Reactivar"}
                  </Button>
                  {busyId === e.id && <Spinner className="h-3.5 w-3.5 text-slate-400" />}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
