"use client";

/**
 * Conector heredado de Amadeus para instalaciones que todavía tengan acceso
 * Enterprise. La UI principal ya usa Edecán Viajes sin credenciales y no
 * muestra este formulario a conexiones nuevas.
 * mismo patrón que `SelectorLLM`/`SelectorVoz`/`SelectorCasaInteligente`
 * (`DIRECCION_ACTUAL.md`, "Principio de UX no negociable"; `docs/viajes.md`;
 * `apps/api/edecan_api/routers/viajes.py`).
 *
 * `environment="test"` por defecto a propósito (mismo criterio que
 * `edecan_travel.amadeus.AmadeusClient`): nadie gasta cuota de producción por accidente
 * solo por conectar sus credenciales — el tenant elige "production" explícitamente
 * cuando de verdad lo necesita.
 */

import { useState } from "react";

import { Alert, Button, Field, Input, Select } from "@/components/ui";
import { ApiError, putViajesCredentials, type ViajesEnvironment } from "@/lib/api-viajes";

const ENLACE_AMADEUS = "https://developers.amadeus.com/enterprise-apis";

function mensajeError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "No se pudo conectar.";
}

export function ConectarAmadeus({ onConnected }: { onConnected?: () => void }) {
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [environment, setEnvironment] = useState<ViajesEnvironment>("test");
  const [busy, setBusy] = useState(false);
  const [resultado, setResultado] = useState<{ ok: boolean; mensaje: string } | null>(null);

  async function conectar() {
    setBusy(true);
    setResultado(null);
    try {
      await putViajesCredentials({
        api_key: apiKey.trim(),
        api_secret: apiSecret.trim(),
        environment,
        validate: true,
      });
      setResultado({ ok: true, mensaje: "Conectado y validado." });
      setApiSecret("");
      onConnected?.();
    } catch (err) {
      setResultado({ ok: false, mensaje: mensajeError(err) });
    } finally {
      setBusy(false);
    }
  }

  const puedeConectar = apiKey.trim().length > 0 && apiSecret.trim().length > 0;

  return (
    <div className="space-y-3">
      <p className="text-xs text-slate-400">
        Compatibilidad heredada para cuentas Amadeus Enterprise existentes. Edecán ya busca
        vuelos y hoteles reales sin esta conexión.
      </p>
      {resultado && <Alert variant={resultado.ok ? "success" : "error"}>{resultado.mensaje}</Alert>}
      <Field
        label="API Key de Amadeus"
        htmlFor="viajes_amadeus_key"
        hint="Solo para una cuenta Amadeus Enterprise que ya tengas activa."
      >
        <Input
          id="viajes_amadeus_key"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder="Tu API Key de Amadeus"
          autoComplete="off"
          disabled={busy}
        />
      </Field>
      <Field label="API Secret de Amadeus" htmlFor="viajes_amadeus_secret">
        <Input
          id="viajes_amadeus_secret"
          type="password"
          value={apiSecret}
          onChange={(e) => setApiSecret(e.target.value)}
          placeholder="Tu API Secret de Amadeus"
          autoComplete="off"
          disabled={busy}
        />
      </Field>
      <Field
        label="Entorno"
        htmlFor="viajes_amadeus_environment"
        hint="'Test' es gratis e ilimitado, con datos de prueba de Amadeus — perfecto para empezar. 'Production' usa tu cuota real y devuelve resultados reales."
      >
        <Select
          id="viajes_amadeus_environment"
          value={environment}
          onChange={(e) => setEnvironment(e.target.value as ViajesEnvironment)}
          disabled={busy}
        >
          <option value="test">Test (datos de prueba, recomendado para empezar)</option>
          <option value="production">Production (datos reales)</option>
        </Select>
      </Field>
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <a
          href={ENLACE_AMADEUS}
          target="_blank"
          rel="noreferrer"
          className="text-xs font-medium text-brand-600 hover:text-brand-700 dark:text-brand-400"
        >
          Portal de Amadeus Enterprise →
        </a>
      </div>
      <Button size="sm" onClick={() => void conectar()} loading={busy} disabled={!puedeConectar}>
        Conectar
      </Button>
    </div>
  );
}
