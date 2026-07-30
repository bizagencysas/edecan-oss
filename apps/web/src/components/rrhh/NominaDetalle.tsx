"use client";

import { useState } from "react";

import { Alert, Badge, Button, Spinner } from "@/components/ui";
import { cancelarNomina, type Nomina, type NominaAccionResultado } from "@/lib/api-rrhh";
import { formatDateTime, formatMoney } from "@/lib/format";

import { AprobarNominaModal } from "./AprobarNominaModal";

export function NominaDetalle({
  nomina,
  loading,
  onChanged,
}: {
  nomina: Nomina | null;
  loading: boolean;
  onChanged: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [cancelando, setCancelando] = useState(false);
  const [mostrarAprobar, setMostrarAprobar] = useState(false);

  async function handleCancelar() {
    if (!nomina) return;
    setCancelando(true);
    setError(null);
    try {
      await cancelarNomina(nomina.id);
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo cancelar la nómina.");
    } finally {
      setCancelando(false);
    }
  }

  function handleAprobada(_resultado: NominaAccionResultado) {
    setMostrarAprobar(false);
    onChanged();
  }

  if (loading) {
    return (
      <div className="flex justify-center py-8">
        <Spinner className="h-5 w-5 text-slate-400" />
      </div>
    );
  }

  if (!nomina) {
    return (
      <p className="py-6 text-center text-sm text-slate-500 dark:text-slate-400">
        Selecciona una nómina de la tabla para ver el detalle.
      </p>
    );
  }

  return (
    <div>
      {error && (
        <div className="mb-3">
          <Alert variant="error">{error}</Alert>
        </div>
      )}

      <div className="mb-4 grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-3">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-slate-400">Bruto</p>
          <p className="text-slate-700 dark:text-slate-200">
            {nomina.total_bruto === undefined ? "—" : formatMoney(nomina.total_bruto, nomina.moneda)}
          </p>
        </div>
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-slate-400">Deducciones</p>
          <p className="text-slate-700 dark:text-slate-200">
            {nomina.total_deducciones === undefined
              ? "—"
              : formatMoney(nomina.total_deducciones, nomina.moneda)}
          </p>
        </div>
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-slate-400">Neto (total)</p>
          <p className="font-medium text-slate-800 dark:text-slate-100">
            {formatMoney(nomina.total, nomina.moneda)}
          </p>
        </div>
      </div>

      {nomina.approved_at && (
        <p className="mb-3 text-xs text-slate-400">Aprobada: {formatDateTime(nomina.approved_at)}</p>
      )}

      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-slate-100 text-xs uppercase tracking-wide text-slate-400 dark:border-slate-800">
              <th className="py-2 pr-3 font-medium">Empleado</th>
              <th className="py-2 pr-3 text-right font-medium">Bruto</th>
              <th className="py-2 pr-3 text-right font-medium">Deducciones</th>
              <th className="py-2 pr-3 text-right font-medium">Neto</th>
            </tr>
          </thead>
          <tbody>
            {(nomina.items ?? []).map((item) => (
              <tr key={item.id} className="border-b border-slate-50 dark:border-slate-800/60">
                <td className="py-2 pr-3 text-slate-700 dark:text-slate-200">{item.empleado_nombre}</td>
                <td className="py-2 pr-3 text-right text-slate-700 dark:text-slate-200">
                  {formatMoney(item.bruto, nomina.moneda)}
                </td>
                <td className="py-2 pr-3 text-right text-slate-700 dark:text-slate-200">
                  {formatMoney(item.deducciones, nomina.moneda)}
                </td>
                <td className="py-2 pr-3 text-right font-medium text-slate-800 dark:text-slate-100">
                  {formatMoney(item.neto, nomina.moneda)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {(nomina.items ?? []).length === 0 && (
          <p className="py-4 text-center text-sm text-slate-500 dark:text-slate-400">
            Ningún empleado activo tenía salario asignado para este periodo.
          </p>
        )}
      </div>

      {nomina.status === "draft" && (
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="secondary" loading={cancelando} onClick={handleCancelar}>
            Cancelar nómina
          </Button>
          <Button onClick={() => setMostrarAprobar(true)}>Aprobar</Button>
        </div>
      )}
      {nomina.status !== "draft" && (
        <div className="mt-5 flex justify-end">
          <Badge variant="neutral">Ya no está en borrador — sin más acciones disponibles.</Badge>
        </div>
      )}

      {mostrarAprobar && (
        <AprobarNominaModal
          nomina={nomina}
          onClose={() => setMostrarAprobar(false)}
          onAprobada={handleAprobada}
        />
      )}
    </div>
  );
}
