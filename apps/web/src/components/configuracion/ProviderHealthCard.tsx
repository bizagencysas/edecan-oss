"use client";

import { useCallback, useEffect, useState } from "react";

import { Alert, Button, Card, CardBody, CardHeader, Spinner } from "@/components/ui";
import { getProviderHealth, type ProviderHealthResponse } from "@/lib/api";

function statusLabel(status: string): string {
  if (status === "success") return "Correcto";
  if (status === "failure") return "Fallo";
  if (status === "rate_limited") return "Límite temporal";
  return status;
}

function formatLatency(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "—";
  return `${Math.round(seconds * 1000)} ms`;
}

export function ProviderHealthCard() {
  const [data, setData] = useState<ProviderHealthResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (manual = false) => {
    if (manual) setRefreshing(true);
    setError(null);
    try {
      setData(await getProviderHealth());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No se pudo consultar la salud de proveedores.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const providers = Object.entries(data?.providers ?? {});
  return (
    <Card className="lg:col-span-2">
      <CardHeader
        title="Salud de proveedores"
        description="Diagnóstico agregado del proceso actual; no contiene prompts ni credenciales."
        actions={
          <Button variant="ghost" size="sm" onClick={() => void load(true)} disabled={loading || refreshing}>
            {refreshing ? <Spinner className="h-3.5 w-3.5" /> : "Actualizar"}
          </Button>
        }
      />
      <CardBody className="space-y-3">
        {error && <Alert variant="error">{error}</Alert>}
        {loading ? (
          <div className="flex items-center gap-2 text-sm text-slate-500"><Spinner /> Consultando…</div>
        ) : providers.length === 0 ? (
          <p className="text-sm text-slate-500 dark:text-slate-400">Todavía no hay llamadas registradas en este proceso.</p>
        ) : (
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {providers.map(([name, snapshot]) => (
              <div key={name} className="rounded-xl border border-slate-200 p-3 dark:border-slate-700">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium text-slate-900 dark:text-slate-100">{name}</span>
                  <span className={snapshot.available ? "text-xs text-emerald-600" : "text-xs text-rose-600"}>
                    {snapshot.available ? "Disponible" : "Degradado"}
                  </span>
                </div>
                <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
                  Latencia media: {formatLatency(snapshot.avg_latency)} · fallos: {snapshot.total_failures}
                </p>
              </div>
            ))}
          </div>
        )}
        {data && data.recent_events.length > 0 && (
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Último evento: {statusLabel(data.recent_events[0].status)} en {data.recent_events[0].provider}.
            Este historial se reinicia al reiniciar el proceso.
          </p>
        )}
      </CardBody>
    </Card>
  );
}

