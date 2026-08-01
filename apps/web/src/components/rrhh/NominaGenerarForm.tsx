"use client";

import { useState } from "react";

import { Alert, Button, Card, CardBody, CardHeader, Field, Input } from "@/components/ui";
import type { NominaCreateInput } from "@/lib/api-rrhh";
import { currentMonth } from "@/lib/format";

/** Genera el borrador (`calcular_nomina`) — nunca mueve dinero, ver `docs/rrhh.md`. Aprobar
 * la corrida resultante es un paso APARTE (`NominaDetalle`/`AprobarNominaModal`), con su
 * propia confirmación explícita. */
export function NominaGenerarForm({
  onGenerar,
  submitting,
}: {
  onGenerar: (input: NominaCreateInput) => Promise<void>;
  submitting: boolean;
}) {
  const [periodo, setPeriodo] = useState(currentMonth());
  const [deduccionesPct, setDeduccionesPct] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    if (!periodo) {
      setError("Elige un periodo.");
      return;
    }
    const pct = deduccionesPct.trim();
    if (pct !== "" && (Number.isNaN(Number(pct)) || Number(pct) < 0 || Number(pct) > 50)) {
      setError("El porcentaje de deducciones debe estar entre 0 y 50.");
      return;
    }

    try {
      await onGenerar({ periodo, deducciones_pct: pct === "" ? undefined : pct });
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo generar la nómina.");
    }
  }

  return (
    <Card>
      <CardHeader
        title="Generar nómina"
        description="Calcula bruto/deducciones/neto de cada empleado activo con salario asignado. No mueve dinero: genera un borrador que apruebas tú abajo."
      />
      <CardBody>
        <form onSubmit={handleSubmit} className="grid grid-cols-1 gap-3 sm:grid-cols-4">
          {error && (
            <div className="sm:col-span-4">
              <Alert variant="error">{error}</Alert>
            </div>
          )}
          <Field label="Periodo" htmlFor="nomina-periodo">
            <Input
              id="nomina-periodo"
              type="month"
              value={periodo}
              onChange={(e) => setPeriodo(e.target.value)}
            />
          </Field>
          <Field
            label="Deducciones % (opcional)"
            htmlFor="nomina-deducciones"
            hint="0 a 50, aplica a todos por igual."
          >
            <Input
              id="nomina-deducciones"
              type="number"
              min="0"
              max="50"
              step="0.01"
              value={deduccionesPct}
              onChange={(e) => setDeduccionesPct(e.target.value)}
              placeholder="0"
            />
          </Field>
          <div className="sm:col-span-2 flex items-end">
            <Button type="submit" loading={submitting}>
              Generar nómina
            </Button>
          </div>
        </form>
      </CardBody>
    </Card>
  );
}
