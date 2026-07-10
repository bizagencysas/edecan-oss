"use client";

/**
 * Selector de voz (STT Deepgram + TTS ElevenLabs/Polly) — mismo componente
 * dentro de la tarjeta "Voz" de `/app/configuracion` y en el Paso 2
 * (opcional) del wizard de `/app/bienvenida`.
 *
 * Polly no pide `api_key`: se autentica con la identidad AWS *ambiente* del
 * proceso que corre el backend (`edecan_voice.polly`) — eso solo es "la
 * credencial del tenant" en self-host de un único tenant
 * (`EDECAN_LOCAL_MODE=true`); en un servidor hospedado/compartido esa
 * identidad se compartiría entre tenants (hallazgo "riesgo-legal-tos", ver
 * `HOTFIXES_PENDIENTES.md`), así que el backend rechaza guardarla fuera de
 * ese modo (`PUT /v1/credentials/voice/tts`, `docs/credenciales.md`). Por
 * eso `localMode` (mismo prop que ya usa `SelectorLLM` para
 * `claude_cli`/`codex_cli`/`ollama`, ver `detect.local_mode` de
 * `GET /v1/setup/detect`) oculta la opción "Amazon Polly" del todo cuando
 * es `false`, en vez de ofrecerla y que el backend la rechace después —
 * mismo criterio de "pocos clicks, sin caminos muertos" que el resto de
 * `/app/configuracion`.
 */

import { useState } from "react";

import { Alert, Button, Field, Input, Select } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { putVoiceStt, putVoiceTts } from "@/lib/api-configuracion";

import { CampoLlave } from "./CampoLlave";

const ENLACE_DEEPGRAM = "https://console.deepgram.com";
const ENLACE_ELEVENLABS = "https://elevenlabs.io";

function mensajeError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "No se pudo conectar.";
}

function ResultadoConexion({ resultado }: { resultado: { ok: boolean; mensaje: string } | null }) {
  if (!resultado) return null;
  return (
    <Alert variant={resultado.ok ? "success" : "error"}>
      {resultado.ok ? `✅ ${resultado.mensaje}` : `❌ ${resultado.mensaje}`}
    </Alert>
  );
}

export function SelectorVoz({
  localMode = false,
  onSttConnected,
  onTtsConnected,
}: {
  /** `detect.local_mode` de `GET /v1/setup/detect` — ver docstring del módulo. */
  localMode?: boolean;
  onSttConnected?: () => void;
  onTtsConnected?: () => void;
}) {
  // --- STT (Deepgram) ---------------------------------------------------
  const [sttKey, setSttKey] = useState("");
  const [sttBusy, setSttBusy] = useState(false);
  const [sttResultado, setSttResultado] = useState<{ ok: boolean; mensaje: string } | null>(null);

  async function conectarStt() {
    setSttBusy(true);
    setSttResultado(null);
    try {
      await putVoiceStt({ provider: "deepgram", api_key: sttKey, validate: true });
      setSttResultado({ ok: true, mensaje: "Conectado y validado." });
      onSttConnected?.();
    } catch (err) {
      setSttResultado({ ok: false, mensaje: mensajeError(err) });
    } finally {
      setSttBusy(false);
    }
  }

  // --- TTS (ElevenLabs / Polly) -------------------------------------------
  const [ttsProvider, setTtsProvider] = useState<"elevenlabs" | "polly">("elevenlabs");
  const [ttsKey, setTtsKey] = useState("");
  const [ttsVoiceId, setTtsVoiceId] = useState("");
  const [ttsBusy, setTtsBusy] = useState(false);
  const [ttsResultado, setTtsResultado] = useState<{ ok: boolean; mensaje: string } | null>(null);

  async function conectarTts() {
    setTtsBusy(true);
    setTtsResultado(null);
    try {
      await putVoiceTts({
        provider: ttsProvider,
        api_key: ttsProvider === "elevenlabs" ? ttsKey : undefined,
        voice_id: ttsVoiceId.trim() || undefined,
        validate: true,
      });
      setTtsResultado({ ok: true, mensaje: "Conectado y validado." });
      onTtsConnected?.();
    } catch (err) {
      setTtsResultado({ ok: false, mensaje: mensajeError(err) });
    } finally {
      setTtsBusy(false);
    }
  }

  const ttsPuedeConectar = ttsProvider === "elevenlabs" ? ttsKey.trim().length > 0 : ttsVoiceId.trim().length > 0;

  return (
    <div className="space-y-6">
      <p className="text-xs text-slate-400">
        Opcional — si no conectas nada aquí, la voz sigue funcionando con un stub (sin llamadas reales); no bloquea el chat de texto.
      </p>

      <div>
        <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
          Voz a texto (STT) — Deepgram
        </p>
        <div className="space-y-3">
          <CampoLlave
            id="voz_stt_key"
            label="API key de Deepgram"
            value={sttKey}
            onChange={setSttKey}
            placeholder="Tu API key de Deepgram"
            linkHref={ENLACE_DEEPGRAM}
            disabled={sttBusy}
          />
          <ResultadoConexion resultado={sttResultado} />
          <Button size="sm" onClick={() => void conectarStt()} loading={sttBusy} disabled={!sttKey.trim()}>
            Conectar
          </Button>
        </div>
      </div>

      <div className="border-t border-slate-100 pt-4 dark:border-slate-800">
        <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">Texto a voz (TTS)</p>
        <div className="space-y-3">
          <Field label="Proveedor" htmlFor="voz_tts_provider">
            <Select
              id="voz_tts_provider"
              value={ttsProvider}
              onChange={(e) => setTtsProvider(e.target.value as "elevenlabs" | "polly")}
              disabled={ttsBusy}
            >
              <option value="elevenlabs">ElevenLabs</option>
              {/* Solo en self-host de un único tenant: fuera de `localMode` el
                  backend rechaza guardar "polly" con 400 (ver docstring del
                  módulo) — se oculta la opción en vez de ofrecer un camino
                  muerto. */}
              {localMode && <option value="polly">Amazon Polly</option>}
            </Select>
          </Field>

          {ttsProvider === "elevenlabs" ? (
            <>
              <CampoLlave
                id="voz_tts_key"
                label="API key de ElevenLabs"
                value={ttsKey}
                onChange={setTtsKey}
                placeholder="Tu API key de ElevenLabs"
                linkHref={ENLACE_ELEVENLABS}
                disabled={ttsBusy}
              />
              <Field
                label="Voz (opcional)"
                htmlFor="voz_tts_voice"
                hint="ID de la voz de ElevenLabs; si lo dejas vacío se usa la voz por defecto."
              >
                <Input
                  id="voz_tts_voice"
                  value={ttsVoiceId}
                  onChange={(e) => setTtsVoiceId(e.target.value)}
                  placeholder="21m00Tcm4TlvDq8ikWAM"
                  autoComplete="off"
                  disabled={ttsBusy}
                />
              </Field>
            </>
          ) : (
            <Field
              label="Voz de Polly"
              htmlFor="voz_tts_voice_polly"
              hint="Amazon Polly usa la cuenta de AWS de esta instalación local (self-host, single-user); aquí solo eliges qué voz usar."
            >
              <Input
                id="voz_tts_voice_polly"
                value={ttsVoiceId}
                onChange={(e) => setTtsVoiceId(e.target.value)}
                placeholder="Lupe"
                autoComplete="off"
                disabled={ttsBusy}
              />
            </Field>
          )}

          <ResultadoConexion resultado={ttsResultado} />
          <Button size="sm" onClick={() => void conectarTts()} loading={ttsBusy} disabled={!ttsPuedeConectar}>
            Conectar
          </Button>
        </div>
      </div>
    </div>
  );
}
