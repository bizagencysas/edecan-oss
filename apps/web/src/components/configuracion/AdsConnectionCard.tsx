"use client";

import { useCallback, useEffect, useState } from "react";

import { ConectarMetaAds } from "@/components/ads/ConectarMetaAds";
import { Alert, Badge, Card, CardBody, CardHeader, Spinner } from "@/components/ui";
import { ApiError, deleteAdsCredentials, getAdsStatus, type AdsStatus } from "@/lib/api-ads";

function mensajeError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "No se pudo cargar Meta Ads.";
}

export function AdsConnectionCard({ onStatusChange }: { onStatusChange?: (status: AdsStatus) => void }) {
  const [status, setStatus] = useState<AdsStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [removing, setRemoving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const next = await getAdsStatus();
      setStatus(next);
      onStatusChange?.(next);
      setError(null);
    } catch (err) {
      setError(mensajeError(err));
    } finally {
      setLoading(false);
    }
  }, [onStatusChange]);

  useEffect(() => {
    void load();
  }, [load]);

  async function remove() {
    setRemoving(true);
    try {
      await deleteAdsCredentials();
    } catch (err) {
      setError(mensajeError(err));
    } finally {
      setRemoving(false);
      await load();
    }
  }

  const connected = status?.configured ?? false;
  const statusLabel = !connected
    ? "Sin configurar"
    : status?.reachable === true
      ? "Disponible ahora"
      : status?.reachable === false
        ? "Requiere atención"
        : "No se pudo comprobar";
  const statusVariant = !connected
    ? "neutral"
    : status?.reachable === true
      ? "success"
      : "warning";
  return (
    <Card>
      <CardHeader
        title="Meta Ads"
        description="La conexión nativa recomendada: consulta datos reales y conserva el flujo seguro de borrador → confirmación → campaña pausada."
        actions={loading ? <Spinner className="h-4 w-4 text-slate-400" /> : <Badge variant={statusVariant}>{statusLabel}</Badge>}
      />
      <CardBody className="space-y-3">
        {error && <Alert variant="error">{error}</Alert>}
        {connected && status && (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg bg-slate-50 px-3 py-2 text-sm dark:bg-slate-950/40">
            <span className="text-slate-700 dark:text-slate-200">
              {[status.nombre_cuenta, status.ad_account_id ? `act_${status.ad_account_id}` : null, status.moneda].filter(Boolean).join(" · ") || "Credencial guardada"}
            </span>
            <button type="button" onClick={() => void remove()} disabled={removing} className="text-xs font-medium text-rose-600 hover:text-rose-700 disabled:opacity-50 dark:text-rose-400">
              {removing ? "Quitando…" : "Quitar"}
            </button>
          </div>
        )}
        <ConectarMetaAds onConnected={load} />
      </CardBody>
    </Card>
  );
}
