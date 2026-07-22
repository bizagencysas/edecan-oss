"use client";

/**
 * Selector de generación de imágenes — bring-your-own, mismo patrón "pegar y
 * validar" que `SelectorLLM`/`SelectorVoz`/`SelectorCasaInteligente`
 * (DIRECCION_ACTUAL.md "Principio de UX no negociable"; ARCHITECTURE.md §12.b;
 * docs/credenciales.md; apps/api/edecan_api/routers/credentials.py).
 *
 * Único proveedor real hoy: cualquier endpoint compatible con
 * `POST {base_url}/images/generations` (contrato de OpenAI Images) — mismo
 * criterio que el tab "Compatible con OpenAI" de `SelectorLLM`, por eso el
 * formulario es idéntico (base_url + api_key + model), solo cambia el
 * endpoint al que apunta. Sin proveedor propio conectado, `generar_imagen`
 * sigue funcionando con un generador offline determinista (stub) — nunca
 * bloquea el chat.
 */

import { useState } from "react";

import { Alert, Button, Field, Input } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { putImagesCredentials } from "@/lib/api-configuracion";

import { CampoLlave } from "./CampoLlave";

const ENLACE_OPENAI = "https://platform.openai.com/api-keys";

function mensajeError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "No se pudo conectar.";
}

export function SelectorImagenes({ onConnected }: { onConnected?: () => void }) {
  const [baseUrl, setBaseUrl] = useState("https://api.openai.com/v1");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [busy, setBusy] = useState(false);
  const [resultado, setResultado] = useState<{ ok: boolean; mensaje: string } | null>(null);

  async function conectar() {
    setBusy(true);
    setResultado(null);
    try {
      await putImagesCredentials({
        base_url: baseUrl.trim(),
        api_key: apiKey.trim(),
        model: model.trim(),
        validate: true,
      });
      setResultado({ ok: true, mensaje: "Conectado y validado." });
      setApiKey("");
      onConnected?.();
    } catch (err) {
      setResultado({ ok: false, mensaje: mensajeError(err) });
    } finally {
      setBusy(false);
    }
  }

  const puedeConectar = baseUrl.trim().length > 0 && apiKey.trim().length > 0 && model.trim().length > 0;

  return (
    <div className="space-y-3">
      <p className="text-xs text-slate-400">
        Opcional — conecta cualquier endpoint compatible con generación de imágenes tipo OpenAI
        (OpenAI, o un proxy que hable el mismo contrato). Sin esto, «generar imagen» sigue
        funcionando con un generador offline simple (no bloquea el chat).
      </p>
      {resultado && <Alert variant={resultado.ok ? "success" : "error"}>{resultado.ok ? `✅ ${resultado.mensaje}` : `❌ ${resultado.mensaje}`}</Alert>}
      <Field
        label="Base URL"
        htmlFor="imagenes_base_url"
        hint="Cualquier endpoint compatible con POST {base_url}/images/generations."
      >
        <Input
          id="imagenes_base_url"
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          placeholder="https://api.openai.com/v1"
          autoComplete="off"
          disabled={busy}
        />
      </Field>
      <CampoLlave
        id="imagenes_api_key"
        label="API key"
        value={apiKey}
        onChange={setApiKey}
        placeholder="sk-…"
        linkHref={ENLACE_OPENAI}
        disabled={busy}
      />
      <Field label="Modelo" htmlFor="imagenes_model" hint="El nombre del modelo de generación de imágenes de tu proveedor.">
        <Input
          id="imagenes_model"
          value={model}
          onChange={(e) => setModel(e.target.value)}
          placeholder="gpt-image-2"
          autoComplete="off"
          disabled={busy}
        />
      </Field>
      <Button size="sm" onClick={() => void conectar()} loading={busy} disabled={!puedeConectar}>
        Conectar
      </Button>
    </div>
  );
}
