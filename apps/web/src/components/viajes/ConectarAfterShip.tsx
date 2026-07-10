"use client";

/**
 * Tarjeta "Conectar AfterShip" — pegar y validar (1 campo), mismo patrón que
 * `ConectarAmadeus`/`SelectorLLM`/`SelectorVoz` (`DIRECCION_ACTUAL.md`, "Principio de
 * UX no negociable"; `docs/viajes.md`; `apps/api/edecan_api/routers/viajes.py`).
 */

import { useState } from "react";

import { Alert, Button, Field, Input } from "@/components/ui";
import { ApiError, putRastreoCredentials } from "@/lib/api-viajes";

const ENLACE_AFTERSHIP = "https://www.aftership.com/signup";

function mensajeError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "No se pudo conectar.";
}

export function ConectarAfterShip({ onConnected }: { onConnected?: () => void }) {
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [resultado, setResultado] = useState<{ ok: boolean; mensaje: string } | null>(null);

  async function conectar() {
    setBusy(true);
    setResultado(null);
    try {
      await putRastreoCredentials({ api_key: apiKey.trim(), validate: true });
      setResultado({ ok: true, mensaje: "Conectado y validado." });
      setApiKey("");
      onConnected?.();
    } catch (err) {
      setResultado({ ok: false, mensaje: mensajeError(err) });
    } finally {
      setBusy(false);
    }
  }

  const puedeConectar = apiKey.trim().length > 0;

  return (
    <div className="space-y-3">
      <p className="text-xs text-slate-400">
        Opcional — conecta tu propia cuenta de AfterShip para rastrear paquetes/envíos por
        número de guía, con historial completo de checkpoints.
      </p>
      {resultado && <Alert variant={resultado.ok ? "success" : "error"}>{resultado.mensaje}</Alert>}
      <Field
        label="API Key de AfterShip"
        htmlFor="viajes_aftership_key"
        hint="La sacas en tu cuenta de AfterShip → Configuración → API Keys."
      >
        <Input
          id="viajes_aftership_key"
          type="password"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder="Tu API Key de AfterShip"
          autoComplete="off"
          disabled={busy}
        />
      </Field>
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <a
          href={ENLACE_AFTERSHIP}
          target="_blank"
          rel="noreferrer"
          className="text-xs font-medium text-brand-600 hover:text-brand-700 dark:text-brand-400"
        >
          Sacar mi API Key de AfterShip →
        </a>
      </div>
      <Button size="sm" onClick={() => void conectar()} loading={busy} disabled={!puedeConectar}>
        Conectar
      </Button>
    </div>
  );
}
