"use client";

import { Badge, EmptyState, Spinner } from "@/components/ui";
import type { Nomina, NominaStatus } from "@/lib/api-rrhh";
import { formatDateTime, formatMoney } from "@/lib/format";

const STATUS_LABEL: Record<NominaStatus, string> = {
  draft: "Borrador",
  approved: "Aprobada",
  paid: "Pagada",
  cancelled: "Cancelada",
};

const STATUS_BADGE: Record<NominaStatus, "neutral" | "brand" | "success" | "danger"> = {
  draft: "neutral",
  approved: "brand",
  paid: "success",
  cancelled: "danger",
};

export function NominasTable({
  nominas,
  loading,
  selectedId,
  onSelect,
}: {
  nominas: Nomina[];
  loading: boolean;
  selectedId: string | null;
  onSelect: (nomina: Nomina) => void;
}) {
  if (loading) {
    return (
      <div className="flex justify-center py-8">
        <Spinner className="h-5 w-5 text-slate-400" />
      </div>
    );
  }

  if (nominas.length === 0) {
    return (
      <EmptyState
        title="Sin nóminas todavía"
        description="Genera la primera con el formulario de arriba."
      />
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-slate-100 text-xs uppercase tracking-wide text-slate-400 dark:border-slate-800">
            <th className="py-2 pr-3 font-medium">Periodo</th>
            <th className="py-2 pr-3 text-right font-medium">Total (neto)</th>
            <th className="py-2 pr-3 font-medium">Estado</th>
            <th className="py-2 pr-3 font-medium">Creada</th>
          </tr>
        </thead>
        <tbody>
          {nominas.map((n) => (
            <tr
              key={n.id}
              onClick={() => onSelect(n)}
              className={`cursor-pointer border-b border-slate-50 hover:bg-slate-50 dark:border-slate-800/60 dark:hover:bg-slate-800/40 ${
                selectedId === n.id ? "bg-brand-50 dark:bg-brand-950/30" : ""
              }`}
            >
              <td className="py-2 pr-3 font-medium text-slate-800 dark:text-slate-100">{n.periodo}</td>
              <td className="py-2 pr-3 whitespace-nowrap text-right text-slate-700 dark:text-slate-200">
                {formatMoney(n.total, n.moneda)}
              </td>
              <td className="py-2 pr-3">
                <Badge variant={STATUS_BADGE[n.status]}>{STATUS_LABEL[n.status]}</Badge>
              </td>
              <td className="py-2 pr-3 whitespace-nowrap text-slate-500">
                {formatDateTime(n.created_at)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
