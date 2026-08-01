"use client";

import { useEffect, useState } from "react";

import { Alert, Card, CardBody, CardHeader, Select } from "@/components/ui";
import {
  createAusencia,
  listAusencias,
  listEmpleados,
  resolverAusencia,
  type Ausencia,
  type AusenciaCreateInput,
  type AusenciaStatus,
  type Empleado,
} from "@/lib/api-rrhh";

import { AusenciaForm } from "./AusenciaForm";
import { AusenciasTable } from "./AusenciasTable";

type FiltroEstado = "todos" | AusenciaStatus;

export function AusenciasTab() {
  const [ausencias, setAusencias] = useState<Ausencia[]>([]);
  const [empleados, setEmpleados] = useState<Empleado[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [filtroEstado, setFiltroEstado] = useState<FiltroEstado>("pending");

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filtroEstado]);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const status = filtroEstado === "todos" ? undefined : filtroEstado;
      const [ausenciasRes, empleadosRes] = await Promise.all([
        listAusencias({ status }),
        listEmpleados({ status: "active" }),
      ]);
      setAusencias(ausenciasRes);
      setEmpleados(empleadosRes);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo cargar las ausencias.");
    } finally {
      setLoading(false);
    }
  }

  async function handleCreate(input: AusenciaCreateInput) {
    setSaving(true);
    setError(null);
    try {
      await createAusencia(input);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo registrar la ausencia.");
      throw err; // deja que AusenciaForm mantenga los datos capturados para reintentar
    } finally {
      setSaving(false);
    }
  }

  async function handleResolver(ausencia: Ausencia, accion: "aprobar" | "rechazar") {
    setError(null);
    try {
      await resolverAusencia(ausencia.id, accion);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo resolver la ausencia.");
    }
  }

  return (
    <div>
      {error && (
        <div className="mb-4">
          <Alert variant="error">{error}</Alert>
        </div>
      )}

      <div className="mb-6">
        <AusenciaForm empleados={empleados} onCreate={handleCreate} submitting={saving} />
      </div>

      <Card>
        <CardHeader
          title="Ausencias"
          description="Aprueba o rechaza las solicitudes pendientes."
          actions={
            <Select
              value={filtroEstado}
              onChange={(e) => setFiltroEstado(e.target.value as FiltroEstado)}
              className="w-auto"
              aria-label="Filtrar por estado"
            >
              <option value="pending">Pendientes</option>
              <option value="approved">Aprobadas</option>
              <option value="rejected">Rechazadas</option>
              <option value="todos">Todas</option>
            </Select>
          }
        />
        <CardBody>
          <AusenciasTable
            ausencias={ausencias}
            empleados={empleados}
            loading={loading}
            onResolver={handleResolver}
          />
        </CardBody>
      </Card>
    </div>
  );
}
