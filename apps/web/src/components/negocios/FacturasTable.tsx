"use client";

import { useState } from "react";

import { Badge, Button, EmptyState, Spinner } from "@/components/ui";
import type { Invoice, InvoiceStatus } from "@/lib/api-negocios";
import { formatDate, formatMoney } from "@/lib/format";

const STATUS_LABEL: Record<InvoiceStatus, string> = {
  draft: "Borrador",
  sent: "Enviada",
  paid: "Pagada",
  void: "Anulada",
};

const STATUS_VARIANT: Record<InvoiceStatus, "neutral" | "brand" | "success" | "danger"> = {
  draft: "neutral",
  sent: "brand",
  paid: "success",
  void: "danger",
};

export function FacturasTable({
  facturas,
  loading,
  onChangeStatus,
}: {
  facturas: Invoice[];
  loading: boolean;
  onChangeStatus: (id: string, status: Exclude<InvoiceStatus, "draft">) => Promise<void>;
}) {
  const [busyId, setBusyId] = useState<string | null>(null);

  async function handleChange(id: string, status: Exclude<InvoiceStatus, "draft">) {
    setBusyId(id);
    try {
      await onChangeStatus(id, status);
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

  if (facturas.length === 0) {
    return (
      <EmptyState
        title="Sin facturas todavía"
        description="Crea la primera con el formulario de arriba."
      />
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-slate-100 text-xs uppercase tracking-wide text-slate-400 dark:border-slate-800">
            <th className="py-2 pr-3 font-medium">Número</th>
            <th className="py-2 pr-3 font-medium">Cliente</th>
            <th className="py-2 pr-3 font-medium">Emitida</th>
            <th className="py-2 pr-3 font-medium">Vence</th>
            <th className="py-2 pr-3 text-right font-medium">Total</th>
            <th className="py-2 pr-3 font-medium">Estado</th>
            <th className="py-2 pr-3 font-medium">Acciones</th>
          </tr>
        </thead>
        <tbody>
          {facturas.map((f) => (
            <tr key={f.id} className="border-b border-slate-50 dark:border-slate-800/60">
              <td className="py-2 pr-3 whitespace-nowrap font-medium text-slate-700 dark:text-slate-200">
                {f.numero}
              </td>
              <td className="py-2 pr-3 text-slate-700 dark:text-slate-200">{f.cliente_nombre}</td>
              <td className="py-2 pr-3 whitespace-nowrap text-slate-500">
                {formatDate(f.created_at.slice(0, 10))}
              </td>
              <td className="py-2 pr-3 whitespace-nowrap text-slate-500">
                {f.due_date ? formatDate(f.due_date) : "—"}
              </td>
              <td className="py-2 pr-3 whitespace-nowrap text-right font-medium text-slate-800 dark:text-slate-100">
                {formatMoney(f.total, f.moneda)}
              </td>
              <td className="py-2 pr-3">
                <Badge variant={STATUS_VARIANT[f.status]}>{STATUS_LABEL[f.status]}</Badge>
              </td>
              <td className="py-2 pr-3">
                <div className="flex flex-wrap items-center gap-1.5">
                  {f.status === "draft" && (
                    <Button
                      size="sm"
                      variant="secondary"
                      disabled={busyId === f.id}
                      onClick={() => handleChange(f.id, "sent")}
                    >
                      Marcar enviada
                    </Button>
                  )}
                  {f.status === "sent" && (
                    <Button
                      size="sm"
                      variant="secondary"
                      disabled={busyId === f.id}
                      onClick={() => handleChange(f.id, "paid")}
                    >
                      Marcar pagada
                    </Button>
                  )}
                  {/* "void" está permitido desde cualquier estado no-void (incluida una
                      factura ya pagada, p. ej. para corregir un error) — mismo criterio que
                      `edecan_business.invoices.cambiar_estado`, ver su docstring. */}
                  {f.status !== "void" && (
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={busyId === f.id}
                      onClick={() => handleChange(f.id, "void")}
                    >
                      Anular
                    </Button>
                  )}
                  {busyId === f.id && <Spinner className="h-3.5 w-3.5 text-slate-400" />}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
