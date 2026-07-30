"use client";

import { useEffect, useState } from "react";

import { ActividadList } from "@/components/negocios/ActividadList";
import { DonutChart } from "@/components/negocios/DonutChart";
import { FacturaForm } from "@/components/negocios/FacturaForm";
import { FacturasTable } from "@/components/negocios/FacturasTable";
import { KpiCard } from "@/components/negocios/KpiCard";
import { Alert, Card, CardBody, CardHeader, Input, PageHeader, Spinner } from "@/components/ui";
import {
  createFactura,
  getNegociosKpis,
  listFacturas,
  setFacturaEstado,
  type Invoice,
  type InvoiceCreateInput,
  type InvoiceStatus,
  type NegociosKpis,
} from "@/lib/api-negocios";
import { currentMonth, formatNumber } from "@/lib/format";

export default function NegociosPage() {
  const [mes, setMes] = useState(currentMonth());
  const [kpis, setKpis] = useState<NegociosKpis | null>(null);
  const [facturas, setFacturas] = useState<Invoice[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    void load(mes);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mes]);

  async function load(month: string) {
    setLoading(true);
    setError(null);
    try {
      const [k, f] = await Promise.all([getNegociosKpis(month), listFacturas()]);
      setKpis(k);
      setFacturas(f);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo cargar el panel de negocios.");
    } finally {
      setLoading(false);
    }
  }

  async function handleCreateFactura(input: InvoiceCreateInput) {
    setCreating(true);
    setError(null);
    try {
      await createFactura(input);
      await load(mes);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo crear la factura.");
      throw err; // deja que FacturaForm mantenga los datos capturados para reintentar
    } finally {
      setCreating(false);
    }
  }

  async function handleChangeStatus(id: string, status: Exclude<InvoiceStatus, "draft">) {
    setError(null);
    try {
      await setFacturaEstado(id, status);
      await load(mes);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo cambiar el estado de la factura.");
    }
  }

  return (
    <div>
      <PageHeader
        title="Negocios"
        description="Facturación ligera + KPIs del negocio. No es contabilidad fiscal."
        actions={
          <Input
            type="month"
            value={mes}
            onChange={(e) => setMes(e.target.value)}
            className="w-auto"
            aria-label="Mes"
          />
        }
      />

      {error && (
        <div className="mb-4">
          <Alert variant="error">{error}</Alert>
        </div>
      )}

      {loading && !kpis ? (
        <div className="flex justify-center py-16">
          <Spinner className="h-6 w-6 text-slate-400" />
        </div>
      ) : (
        kpis && (
          <>
            <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <KpiCard label="Ingresos" value={formatNumber(kpis.ingresos)} tone="positive" />
              <KpiCard label="Gastos" value={formatNumber(kpis.gastos)} tone="negative" />
              <KpiCard
                label="Beneficio"
                value={formatNumber(kpis.beneficio)}
                tone={kpis.beneficio >= 0 ? "positive" : "negative"}
              />
              <KpiCard label="Nuevos clientes" value={String(kpis.nuevos_clientes)} />
            </div>

            <div className="mb-6 grid grid-cols-1 gap-4 lg:grid-cols-2">
              <Card>
                <CardHeader
                  title="Ventas por canal"
                  description={`Facturado: ${formatNumber(kpis.facturado)} · Cobrado: ${formatNumber(kpis.cobrado)}`}
                />
                <CardBody>
                  <DonutChart data={kpis.por_canal} />
                </CardBody>
              </Card>
              <Card>
                <CardHeader title="Actividad reciente" description={`Del mes de ${kpis.mes}`} />
                <CardBody>
                  <ActividadList items={kpis.actividad} />
                </CardBody>
              </Card>
            </div>

            <div className="mb-6">
              <FacturaForm onSubmit={handleCreateFactura} submitting={creating} />
            </div>

            <Card>
              <CardHeader title="Facturas" description="Marca el estado a medida que avanza cada una." />
              <CardBody>
                <FacturasTable
                  facturas={facturas}
                  loading={loading}
                  onChangeStatus={handleChangeStatus}
                />
              </CardBody>
            </Card>
          </>
        )
      )}
    </div>
  );
}
