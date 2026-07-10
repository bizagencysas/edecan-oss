"use client";

/**
 * Tarjeta "Conectar Meta Ads" — pegar y validar (access token + id de cuenta
 * de anuncios), mismo patrón que `ConectarAmadeus`/`SelectorCasaInteligente`
 * (`DIRECCION_ACTUAL.md`, "Principio de UX no negociable"; `docs/ads.md`;
 * `apps/api/edecan_api/routers/ads.py`).
 *
 * Vive directo en `/app/ads` (no en `/app/configuracion`) — mismo criterio
 * que Viajes (`ConectarAmadeus`): la credencial es propia de esta vertical,
 * no una de las tarjetas centrales de Configuración (LLM/voz/imágenes/
 * búsqueda/casa inteligente/MCP). Sin esto conectado, la página sigue siendo
 * 100% usable: `GET /v1/ads/resumen` cae a `StubAdsProvider` (datos de
 * ejemplo, nunca bloquea), así que este formulario es siempre opcional.
 */

import { useState } from "react";

import { Alert, Button, Field, Input } from "@/components/ui";
import { ApiError, putAdsCredentials } from "@/lib/api-ads";

const ENLACE_META_DEVELOPERS = "https://developers.facebook.com/apps";

function mensajeError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "No se pudo conectar.";
}

export function ConectarMetaAds({ onConnected }: { onConnected?: () => void }) {
  const [accessToken, setAccessToken] = useState("");
  const [adAccountId, setAdAccountId] = useState("");
  const [busy, setBusy] = useState(false);
  const [resultado, setResultado] = useState<{ ok: boolean; mensaje: string } | null>(null);

  async function conectar() {
    setBusy(true);
    setResultado(null);
    try {
      await putAdsCredentials({
        access_token: accessToken.trim(),
        ad_account_id: adAccountId.trim(),
        validate: true,
      });
      setResultado({ ok: true, mensaje: "Conectado y validado." });
      setAccessToken("");
      onConnected?.();
    } catch (err) {
      setResultado({ ok: false, mensaje: mensajeError(err) });
    } finally {
      setBusy(false);
    }
  }

  const puedeConectar = accessToken.trim().length > 0 && adAccountId.trim().length > 0;

  return (
    <div className="space-y-3">
      <p className="text-xs text-slate-400">
        Opcional — conecta tu propia app de Meta for Developers (access token con permisos
        «ads_management»/«ads_read» + el id de tu cuenta de anuncios) para ver campañas y
        métricas reales, y crear borradores que puedas empujar a Meta. Sin esto, el resumen
        sigue funcionando con una campaña de ejemplo en modo offline — no bloquea nada.
      </p>
      {resultado && <Alert variant={resultado.ok ? "success" : "error"}>{resultado.mensaje}</Alert>}
      <Field
        label="Access token de Meta"
        htmlFor="ads_access_token"
        hint="Con permisos 'ads_management' y 'ads_read' sobre tu cuenta de anuncios."
      >
        <Input
          id="ads_access_token"
          type="password"
          value={accessToken}
          onChange={(e) => setAccessToken(e.target.value)}
          placeholder="Tu access token de la Graph API de Meta"
          autoComplete="off"
          disabled={busy}
        />
      </Field>
      <Field
        label="ID de la cuenta de anuncios"
        htmlFor="ads_account_id"
        hint="Con o sin el prefijo 'act_' — lo encuentras en el Ads Manager, arriba a la izquierda."
      >
        <Input
          id="ads_account_id"
          value={adAccountId}
          onChange={(e) => setAdAccountId(e.target.value)}
          placeholder="act_1234567890"
          autoComplete="off"
          disabled={busy}
        />
      </Field>
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <a
          href={ENLACE_META_DEVELOPERS}
          target="_blank"
          rel="noreferrer"
          className="text-xs font-medium text-brand-600 hover:text-brand-700 dark:text-brand-400"
        >
          Sacar mi access token en Meta for Developers →
        </a>
      </div>
      <Button size="sm" onClick={() => void conectar()} loading={busy} disabled={!puedeConectar}>
        Conectar
      </Button>
    </div>
  );
}
