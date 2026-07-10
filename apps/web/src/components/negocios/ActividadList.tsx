import { Badge, EmptyState } from "@/components/ui";
import type { ActividadItem } from "@/lib/api-negocios";
import { formatDateTime, formatMoney } from "@/lib/format";

const STATUS_LABEL: Record<string, string> = {
  draft: "Borrador",
  sent: "Enviada",
  paid: "Pagada",
  void: "Anulada",
};

/** Últimos eventos del mes entre facturas y transacciones (`kpis.actividad`, ya mezclados
 * y ordenados por `edecan_business.kpis`). Cada fila SÍ tiene una moneda propia (a
 * diferencia de los totales agregados de arriba), así que aquí `formatMoney` es correcto. */
export function ActividadList({ items }: { items: ActividadItem[] }) {
  if (items.length === 0) {
    return <EmptyState title="Sin actividad este mes" />;
  }

  return (
    <ul className="divide-y divide-slate-100 dark:divide-slate-800">
      {items.map((item) => (
        <li
          key={`${item.tipo}-${item.id}`}
          className="flex items-center justify-between gap-3 py-2.5 text-sm"
        >
          <div className="min-w-0">
            <p className="truncate text-slate-700 dark:text-slate-200">{item.descripcion}</p>
            <p className="text-xs text-slate-400">{formatDateTime(item.fecha)}</p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {item.status && <Badge variant="neutral">{STATUS_LABEL[item.status] ?? item.status}</Badge>}
            <span
              className={`font-medium ${
                item.monto < 0
                  ? "text-rose-600 dark:text-rose-400"
                  : "text-emerald-600 dark:text-emerald-400"
              }`}
            >
              {formatMoney(item.monto, item.moneda)}
            </span>
          </div>
        </li>
      ))}
    </ul>
  );
}
