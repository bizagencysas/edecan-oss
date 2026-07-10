"use client";

import { useEffect, useState } from "react";

import { Alert, Card, CardBody, CardHeader } from "@/components/ui";
import { createNomina, getNomina, listNominas, type Nomina, type NominaCreateInput } from "@/lib/api-rrhh";

import { NominaDetalle } from "./NominaDetalle";
import { NominaGenerarForm } from "./NominaGenerarForm";
import { NominasTable } from "./NominasTable";

export function NominaTab() {
  const [nominas, setNominas] = useState<Nomina[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [generando, setGenerando] = useState(false);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detalle, setDetalle] = useState<Nomina | null>(null);
  const [detalleLoading, setDetalleLoading] = useState(false);

  useEffect(() => {
    void load();
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setDetalle(null);
      return;
    }
    void loadDetalle(selectedId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const res = await listNominas();
      setNominas(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo cargar las nóminas.");
    } finally {
      setLoading(false);
    }
  }

  async function loadDetalle(id: string) {
    setDetalleLoading(true);
    try {
      const res = await getNomina(id);
      setDetalle(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo cargar el detalle de la nómina.");
    } finally {
      setDetalleLoading(false);
    }
  }

  async function handleGenerar(input: NominaCreateInput) {
    setGenerando(true);
    setError(null);
    try {
      const nueva = await createNomina(input);
      await load();
      setSelectedId(nueva.id);
      setDetalle(nueva); // ya viene completa (con items) de la respuesta de creación
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo generar la nómina.");
      throw err;
    } finally {
      setGenerando(false);
    }
  }

  function handleDetalleChanged() {
    void load();
    if (selectedId) void loadDetalle(selectedId);
  }

  return (
    <div>
      {error && (
        <div className="mb-4">
          <Alert variant="error">{error}</Alert>
        </div>
      )}

      <div className="mb-6">
        <NominaGenerarForm onGenerar={handleGenerar} submitting={generando} />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="Nóminas" description="Selecciona una corrida para ver el detalle." />
          <CardBody>
            <NominasTable
              nominas={nominas}
              loading={loading}
              selectedId={selectedId}
              onSelect={(n) => setSelectedId(n.id)}
            />
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="Detalle"
            description={detalle ? `Periodo ${detalle.periodo}` : "Sin selección"}
          />
          <CardBody>
            <NominaDetalle nomina={detalle} loading={detalleLoading} onChanged={handleDetalleChanged} />
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
