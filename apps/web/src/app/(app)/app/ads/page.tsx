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
import { ResumenCampanas } from "@/components/ads/ResumenCampanas";
import { AdsConnectionCard } from "@/components/configuracion/AdsConnectionCard";
import { Alert, PageHeader } from "@/components/ui";
import {
  ApiError,
  listAdDrafts,
  type AdDraft,
  type AdsStatus,
} from "@/lib/api-ads";

function mensajeError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "No se pudo cargar Ads.";
}

export default function AdsPage() {
  const [status, setStatus] = useState<AdsStatus | null>(null);
  const [drafts, setDrafts] = useState<AdDraft[]>([]);
  const [loadingDrafts, setLoadingDrafts] = useState(true);
  const [error, setError] = useState<string | null>(null);

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
    void cargarDrafts();
  }, [cargarDrafts]);

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
        <AdsConnectionCard onStatusChange={setStatus} />
      </div>

      <div className="space-y-6">
        <ResumenCampanas conectado={conectado} moneda={status?.moneda} />
        <BorradoresAds drafts={drafts} loading={loadingDrafts} onChange={() => void cargarDrafts()} />
      </div>
    </div>
  );
}
