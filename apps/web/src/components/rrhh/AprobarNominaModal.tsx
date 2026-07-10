"use client";

import { useState } from "react";

import { Alert, Button } from "@/components/ui";
import { aprobarNomina, type Nomina, type NominaAccionResultado } from "@/lib/api-rrhh";
import { formatMoney } from "@/lib/format";

const AVISO_NO_MUEVE_DINERO =
  "Esto NO mueve dinero. Solo registra la nómina como aprobada en el sistema — el pago lo " +
  "haces tú por tu propio medio (transferencia, efectivo, tu plataforma de nómina).";

/** Segundo gate de confirmación antes de aprobar una nómina — mismo patrón visual que
 * `ConfirmModal` de `/app/ordenes` (sin un `Modal` compartido en `components/ui.tsx`, cada
 * página arma el suyo con `fixed inset-0` + `role="dialog"`). El primer gate ya lo cruzó la
 * corrida al nacer `draft` vía la tool `preparar_nomina` (`dangerous=True`) o el formulario
 * de generar; este modal es el gate humano final antes de `POST .../aprobar
 * {"confirmar": true}`. */
export function AprobarNominaModal({
  nomina,
  onClose,
  onAprobada,
}: {
  nomina: Nomina;
  onClose: () => void;
  onAprobada: (resultado: NominaAccionResultado) => void;
}) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleConfirmar() {
    setLoading(true);
    setError(null);
    try {
      const resultado = await aprobarNomina(nomina.id);
      onAprobada(resultado);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo aprobar la nómina.");
      setLoading(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="aprobar-nomina-titulo"
    >
      <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-5 shadow-xl dark:border-slate-800 dark:bg-slate-900">
        <h3 id="aprobar-nomina-titulo" className="text-sm font-semibold text-slate-900 dark:text-slate-100">
          Aprobar nómina de {nomina.periodo}
        </h3>
        <p className="mt-2 text-sm text-slate-600 dark:text-slate-300">
          Total neto: <span className="font-medium">{formatMoney(nomina.total, nomina.moneda)}</span>
          {" · "}
          {nomina.items?.length ?? 0} empleado(s)
        </p>

        <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300">
          {AVISO_NO_MUEVE_DINERO}
        </div>

        {error && (
          <div className="mt-3">
            <Alert variant="error">{error}</Alert>
          </div>
        )}

        <div className="mt-5 flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose} disabled={loading}>
            Cancelar
          </Button>
          <Button onClick={handleConfirmar} loading={loading}>
            Sí, aprobar (no mueve dinero)
          </Button>
        </div>
      </div>
    </div>
  );
}
