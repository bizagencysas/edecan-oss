"use client";

/**
 * `/app/ads` — Meta Marketing API oficial, bring-your-own, con el guardrail
 * de dinero como centro del diseño (`ARCHITECTURE.md` §13, `DIRECCION_ACTUAL.md`,
 * WP-V4-07; ver `docs/ads.md` para el flujo completo y `apps/api/edecan_api/
 * routers/ads.py` para el contrato exacto). Construida por WP-V7-09 (auditoría
 * UX/navegación) — el router `/v1/ads` existía desde v4 sin ninguna página que
 * lo consumiera.
 *
 * Guardrail de dinero (DIRECCION_ACTUAL.md: "dinero real nunca se mueve
 * solo"): toda campaña que Edecán crea en Meta nace SIEMPRE en pausa
 * (`status=PAUSED`, hardcodeado en el proveedor, sin excepción). Esta página
 * es el SEGUNDO gate de confirmación humana (el primero ya ocurrió en el
 * chat, al aprobar el tool call `ads_preparar_campana`) — nada se publica sin
 * el modal explícito de `BorradoresAds`.
 *
 * Pocos clicks / configuración progresiva: sin conectar una cuenta de Meta,
 * la página sigue siendo 100% usable — `GET /v1/ads/resumen` cae a
 * `StubAdsProvider` (una campaña de ejemplo, nunca bloquea) y los borradores
 * siguen listándose y confirmándose/cancelándose igual. La credencial de
 * Meta vive en esta misma página (`ConectarMetaAds`), no en
 * `/app/configuracion` — mismo criterio que `/app/viajes` con Amadeus/
 * AfterShip: es una credencial propia de esta vertical, no una de las
 * tarjetas centrales de Configuración.
 */

import { useCallback, useEffect, useState } from "react";

import { BorradoresAds } from "@/components/ads/BorradoresAds";
import { ConectarMetaAds } from "@/components/ads/ConectarMetaAds";
import { ResumenCampanas } from "@/components/ads/ResumenCampanas";
import { Alert, Badge, Card, CardBody, CardHeader, PageHeader, Spinner } from "@/components/ui";
import {
  ApiError,
  deleteAdsCredentials,
  getAdsStatus,
  listAdDrafts,
  type AdDraft,
  type AdsStatus,
} from "@/lib/api-ads";

function EstadoBadge({ configurado }: { configurado: boolean }) {
  return <Badge variant={configurado ? "success" : "neutral"}>{configurado ? "Conectado" : "Sin conectar"}</Badge>;
}

function mensajeError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "No se pudo cargar Ads.";
}

export default function AdsPage() {
  const [status, setStatus] = useState<AdsStatus | null>(null);
  const [drafts, setDrafts] = useState<AdDraft[]>([]);
  const [loadingStatus, setLoadingStatus] = useState(true);
  const [loadingDrafts, setLoadingDrafts] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [quitando, setQuitando] = useState(false);

  const cargarStatus = useCallback(async () => {
    setLoadingStatus(true);
    try {
      const s = await getAdsStatus();
      setStatus(s);
      setError(null);
    } catch (err) {
      setError(mensajeError(err));
    } finally {
      setLoadingStatus(false);
    }
  }, []);

  const cargarDrafts = useCallback(async () => {
    setLoadingDrafts(true);
    try {
      const d = await listAdDrafts();
      setDrafts(d);
      setError(null);
    } catch (err) {
      setError(mensajeError(err));
    } finally {
      setLoadingDrafts(false);
    }
  }, []);

  useEffect(() => {
    void cargarStatus();
    void cargarDrafts();
  }, [cargarStatus, cargarDrafts]);

  async function desconectar() {
    setQuitando(true);
    try {
      await deleteAdsCredentials();
    } catch (err) {
      setError(mensajeError(err));
    } finally {
      setQuitando(false);
      await cargarStatus();
    }
  }

  const conectado = status?.configured ?? false;

  return (
    <div>
      <PageHeader
        title="Ads"
        description="Campañas de Meta (Facebook/Instagram) con tu propia cuenta de anuncios. Edecán jamás activa gasto por su cuenta — toda campaña que crea nace siempre en pausa; activarla es una decisión tuya en el Ads Manager de Meta."
      />

      {error && (
        <div className="mb-4">
          <Alert variant="error">{error}</Alert>
        </div>
      )}

      <div className="mb-6">
        <Card>
          <CardHeader
            title="Meta Ads"
            actions={
              loadingStatus ? <Spinner className="h-4 w-4 text-slate-400" /> : <EstadoBadge configurado={conectado} />
            }
          />
          <CardBody className="space-y-3">
            {conectado && status && (
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg bg-slate-50 px-3 py-2 text-sm dark:bg-slate-950/40">
                <span className="text-slate-700 dark:text-slate-200">
                  {[
                    status.nombre_cuenta,
                    status.ad_account_id ? `act_${status.ad_account_id}` : null,
                    status.moneda,
                    status.reachable === false ? "sin respuesta ahora mismo" : undefined,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
                <button
                  type="button"
                  onClick={() => void desconectar()}
                  disabled={quitando}
                  className="text-xs font-medium text-rose-600 hover:text-rose-700 disabled:opacity-50 dark:text-rose-400"
                >
                  {quitando ? "Quitando…" : "Quitar"}
                </button>
              </div>
            )}
            <ConectarMetaAds onConnected={cargarStatus} />
          </CardBody>
        </Card>
      </div>

      <div className="space-y-6">
        <ResumenCampanas conectado={conectado} moneda={status?.moneda} />
        <BorradoresAds drafts={drafts} loading={loadingDrafts} onChange={() => void cargarDrafts()} />
      </div>
    </div>
  );
}
