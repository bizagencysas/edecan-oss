"use client";

import { useState } from "react";

import { Alert, Button, Card, CardBody, CardHeader, Field, Input, Select, Textarea } from "@/components/ui";
import type { AusenciaCreateInput, AusenciaKind, Empleado } from "@/lib/api-rrhh";

const KIND_OPCIONES: { value: AusenciaKind; label: string }[] = [
  { value: "vacaciones", label: "Vacaciones" },
  { value: "enfermedad", label: "Enfermedad" },
  { value: "permiso", label: "Permiso" },
  { value: "otro", label: "Otro" },
];

export function AusenciaForm({
  empleados,
  onCreate,
  submitting,
}: {
  empleados: Empleado[];
  onCreate: (input: AusenciaCreateInput) => Promise<void>;
  submitting: boolean;
}) {
  const [employeeId, setEmployeeId] = useState("");
  const [kind, setKind] = useState<AusenciaKind>("vacaciones");
  const [desde, setDesde] = useState("");
  const [hasta, setHasta] = useState("");
  const [notas, setNotas] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    if (!employeeId) {
      setError("Elige un empleado.");
      return;
    }
    if (!desde || !hasta) {
      setError("Indica desde y hasta.");
      return;
    }
    if (desde > hasta) {
      setError("Desde no puede ser posterior a hasta.");
      return;
    }

    try {
      await onCreate({ employee_id: employeeId, kind, desde, hasta, notas: notas.trim() });
      setEmployeeId("");
      setKind("vacaciones");
      setDesde("");
      setHasta("");
      setNotas("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo registrar la ausencia.");
    }
  }

  return (
    <Card>
      <CardHeader
        title="Registrar ausencia"
        description="Queda pendiente de aprobación — apruébala o recházala en la tabla de abajo."
      />
      <CardBody>
        <form onSubmit={handleSubmit} className="space-y-4">
          {error && <Alert variant="error">{error}</Alert>}

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="Empleado" htmlFor="ausencia-empleado">
              <Select
                id="ausencia-empleado"
                value={employeeId}
                onChange={(e) => setEmployeeId(e.target.value)}
              >
                <option value="">Selecciona un empleado…</option>
                {empleados.map((emp) => (
                  <option key={emp.id} value={emp.id}>
                    {emp.nombre}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Tipo" htmlFor="ausencia-kind">
              <Select
                id="ausencia-kind"
                value={kind}
                onChange={(e) => setKind(e.target.value as AusenciaKind)}
              >
                {KIND_OPCIONES.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </Select>
            </Field>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="Desde" htmlFor="ausencia-desde">
              <Input
                id="ausencia-desde"
                type="date"
                value={desde}
                onChange={(e) => setDesde(e.target.value)}
              />
            </Field>
            <Field label="Hasta" htmlFor="ausencia-hasta">
              <Input
                id="ausencia-hasta"
                type="date"
                value={hasta}
                onChange={(e) => setHasta(e.target.value)}
              />
            </Field>
          </div>

          <Field label="Nota (opcional)" htmlFor="ausencia-notas">
            <Textarea id="ausencia-notas" rows={2} value={notas} onChange={(e) => setNotas(e.target.value)} />
          </Field>

          <div className="flex justify-end">
            <Button type="submit" loading={submitting}>
              Registrar ausencia
            </Button>
          </div>
        </form>
      </CardBody>
    </Card>
  );
}
