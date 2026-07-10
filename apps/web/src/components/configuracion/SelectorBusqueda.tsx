"use client";

/**
 * Selector de búsqueda web — bring-your-own, mismo patrón "pegar y validar"
 * que `SelectorLLM`/`SelectorVoz`/`SelectorImagenes` (DIRECCION_ACTUAL.md
 * "Principio de UX no negociable"; ARCHITECTURE.md §12.b;
 * docs/credenciales.md; apps/api/edecan_api/routers/credentials.py).
 *
 * Dos proveedores soportados (`provider` en `PUT /v1/credentials/search`):
 * Brave Search API y Tavily. Sin proveedor propio conectado, `buscar_web`/
 * `comparar_precios` siguen funcionando con resultados de ejemplo offline
 * (stub) — nunca bloquea el chat.
 */

import { useState } from "react";

import { Alert, Button, Field, Select } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { putSearchCredentials, type SearchProviderKind } from "@/lib/api-configuracion";

import { CampoLlave } from "./CampoLlave";

const ENLACES: Record<SearchProviderKind, string> = {
  brave: "https://brave.com/search/api/",
  tavily: "https://app.tavily.com/home",
};

const ETIQUETAS: Record<SearchProviderKind, string> = {
  brave: "Brave Search",
  tavily: "Tavily",
};

function mensajeError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "No se pudo conectar.";
}

export function SelectorBusqueda({ onConnected }: { onConnected?: () => void }) {
  const [provider, setProvider] = useState<SearchProviderKind>("brave");
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [resultado, setResultado] = useState<{ ok: boolean; mensaje: string } | null>(null);

  async function conectar() {
    setBusy(true);
    setResultado(null);
    try {
      await putSearchCredentials({ provider, api_key: apiKey.trim(), validate: true });
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
        Opcional — conecta tu propia key de búsqueda para que «buscar en la web» y «comparar
        precios» usen resultados reales. Sin esto, siguen funcionando con resultados de ejemplo
        offline (no bloquea el chat).
      </p>
      {resultado && <Alert variant={resultado.ok ? "success" : "error"}>{resultado.ok ? `✅ ${resultado.mensaje}` : `❌ ${resultado.mensaje}`}</Alert>}
      <Field label="Proveedor" htmlFor="busqueda_provider">
        <Select
          id="busqueda_provider"
          value={provider}
          onChange={(e) => setProvider(e.target.value as SearchProviderKind)}
          disabled={busy}
        >
          <option value="brave">Brave Search</option>
          <option value="tavily">Tavily</option>
        </Select>
      </Field>
      <CampoLlave
        id="busqueda_api_key"
        label={`API key de ${ETIQUETAS[provider]}`}
        value={apiKey}
        onChange={setApiKey}
        placeholder={`Tu API key de ${ETIQUETAS[provider]}`}
        linkHref={ENLACES[provider]}
        disabled={busy}
      />
      <Button size="sm" onClick={() => void conectar()} loading={busy} disabled={!puedeConectar}>
        Conectar
      </Button>
    </div>
  );
}
