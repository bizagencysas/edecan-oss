"use client";

/**
 * Selector de Casa inteligente (Home Assistant) — bring-your-own, mismo
 * patrón "pegar y validar" que `SelectorLLM`/`SelectorVoz`
 * (DIRECCION_ACTUAL.md "Principio de UX no negociable"; ARCHITECTURE.md
 * §12.a/§12.b; docs/casa-inteligente.md;
 * apps/api/edecan_api/routers/smarthome.py).
 *
 * `base_url` (la URL de la instancia de Home Assistant del propio tenant,
 * típicamente en su LAN) + un Long-Lived Access Token generado en su perfil
 * de Home Assistant. Sin `CampoLlave` (que asume un link fijo externo tipo
 * "sacar mi key →"): aquí no hay una URL única donde "sacar" nada, el token
 * se genera dentro de la propia instancia del tenant — mismo criterio que el
 * formulario de Twilio en `/app/conectores` (Account SID + Auth Token con
 * `Field`/`Input` simples, sin link externo).
 */

import { useState } from "react";

import { Alert, Button, Field, Input } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { putSmarthomeCredentials } from "@/lib/api-configuracion";

function mensajeError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "No se pudo conectar.";
}

export function SelectorCasaInteligente({ onConnected }: { onConnected?: () => void }) {
  const [baseUrl, setBaseUrl] = useState("");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [resultado, setResultado] = useState<{ ok: boolean; mensaje: string } | null>(null);

  async function conectar() {
    setBusy(true);
    setResultado(null);
    try {
      await putSmarthomeCredentials({ base_url: baseUrl.trim(), token: token.trim(), validate: true });
      setResultado({ ok: true, mensaje: "Conectado y validado." });
      setToken("");
      onConnected?.();
    } catch (err) {
      setResultado({ ok: false, mensaje: mensajeError(err) });
    } finally {
      setBusy(false);
    }
  }

  const puedeConectar = baseUrl.trim().length > 0 && token.trim().length > 0;

  return (
    <div className="space-y-3">
      <p className="text-xs text-slate-400">
        Opcional — conecta tu propia instancia de Home Assistant para que el agente pueda listar,
        consultar y controlar tus dispositivos (luces, enchufes, clima, sensores…) desde el chat.
        Nunca cerraduras: eso queda deshabilitado por seguridad, siempre.
      </p>
      {resultado && <Alert variant={resultado.ok ? "success" : "error"}>{resultado.mensaje}</Alert>}
      <Field
        label="URL de tu Home Assistant"
        htmlFor="casa_base_url"
        hint="P. ej. http://homeassistant.local:8123 o la IP local de tu instancia."
      >
        <Input
          id="casa_base_url"
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          placeholder="http://homeassistant.local:8123"
          autoComplete="off"
          disabled={busy}
        />
      </Field>
      <Field
        label="Long-Lived Access Token"
        htmlFor="casa_token"
        hint="Se genera en tu perfil de Home Assistant → Seguridad → Long-Lived Access Tokens."
      >
        <Input
          id="casa_token"
          type="password"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder="Token de tu Home Assistant"
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
