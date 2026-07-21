"use client";

/**
 * Centro de credenciales (DIRECCION_ACTUAL.md "Pantalla de Configuración" +
 * "Principio de UX no negociable: configuración de pocos clicks"):
 * configuración PROGRESIVA en tarjetas "Conectar" independientes, nunca un
 * formulario gigante. Solo el LLM es necesario para chatear — el resto
 * (voz, telefonía, conectores OAuth, mensajería) es siempre opcional.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { CardCredencial, FilaCredencialConectada, type EstadoCredencial } from "@/components/configuracion/CardCredencial";
import { CardServidoresMcp } from "@/components/configuracion/CardServidoresMcp";
import { SelectorBusqueda } from "@/components/configuracion/SelectorBusqueda";
import { SelectorCasaInteligente } from "@/components/configuracion/SelectorCasaInteligente";
import { SelectorImagenes } from "@/components/configuracion/SelectorImagenes";
import { SelectorLLM } from "@/components/configuracion/SelectorLLM";
import { SelectorVoz } from "@/components/configuracion/SelectorVoz";
import { BrainIcon, MicIcon, PlugIcon, SearchIcon, SendIcon, SparklesIcon } from "@/components/icons";
import { Alert, Card, CardBody, CardHeader, PageHeader, Spinner } from "@/components/ui";
import {
  LLM_KIND_LABELS,
  deleteImagesCredentials,
  deleteLlmCredential,
  deleteSearchCredentials,
  deleteSmarthomeCredentials,
  deleteVoiceStt,
  deleteVoiceTts,
  getCredentials,
  getSetupDetect,
  getSetupStatus,
  getSmarthomeStatus,
  type CredentialsOut,
  type SetupDetect,
  type SetupStatus,
  type SmarthomeStatus,
} from "@/lib/api-configuracion";

const ENLACE_BOTON_CONECTORES =
  "inline-flex items-center rounded-lg bg-white px-3.5 py-2 text-sm font-medium text-slate-700 border border-slate-300 hover:bg-slate-50 dark:bg-slate-800 dark:text-slate-100 dark:border-slate-700 dark:hover:bg-slate-700";

export default function ConfiguracionPage() {
  const [credentials, setCredentials] = useState<CredentialsOut | null>(null);
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [detect, setDetect] = useState<SetupDetect | null>(null);
  const [smarthome, setSmarthome] = useState<SmarthomeStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [quitando, setQuitando] = useState<"llm" | "stt" | "tts" | "casa" | "imagenes" | "busqueda" | null>(
    null,
  );

  const load = useCallback(async () => {
    setError(null);
    try {
      const [creds, st, det, casa] = await Promise.all([
        getCredentials(),
        getSetupStatus(),
        getSetupDetect(),
        getSmarthomeStatus(),
      ]);
      setCredentials(creds);
      setStatus(st);
      setDetect(det);
      setSmarthome(casa);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo cargar la configuración.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleQuitarLlm() {
    setQuitando("llm");
    try {
      await deleteLlmCredential();
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo quitar la credencial.");
    } finally {
      setQuitando(null);
    }
  }

  async function handleQuitarStt() {
    setQuitando("stt");
    try {
      await deleteVoiceStt();
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo quitar la credencial.");
    } finally {
      setQuitando(null);
    }
  }

  async function handleQuitarTts() {
    setQuitando("tts");
    try {
      await deleteVoiceTts();
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo quitar la credencial.");
    } finally {
      setQuitando(null);
    }
  }

  async function handleQuitarCasa() {
    setQuitando("casa");
    try {
      await deleteSmarthomeCredentials();
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo quitar la credencial.");
    } finally {
      setQuitando(null);
    }
  }

  async function handleQuitarImagenes() {
    setQuitando("imagenes");
    try {
      await deleteImagesCredentials();
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo quitar la credencial.");
    } finally {
      setQuitando(null);
    }
  }

  async function handleQuitarBusqueda() {
    setQuitando("busqueda");
    try {
      await deleteSearchCredentials();
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo quitar la credencial.");
    } finally {
      setQuitando(null);
    }
  }

  const llmEstado: EstadoCredencial = credentials?.llm ? "conectado" : "sin_conectar";
  const vozEstado: EstadoCredencial = credentials?.voice_stt || credentials?.voice_tts ? "conectado" : "sin_conectar";
  const casaEstado: EstadoCredencial = smarthome?.configured ? "conectado" : "sin_conectar";
  const imagenesEstado: EstadoCredencial = credentials?.images ? "conectado" : "sin_conectar";
  const busquedaEstado: EstadoCredencial = credentials?.search ? "conectado" : "sin_conectar";

  return (
    <div>
      <PageHeader
        title="Configuración"
        description="Conecta tus propias credenciales — cada tarjeta es independiente y opcional salvo la de Inteligencia."
      />

      {!loading && status && !status.llm_configured && (
        <div className="mb-4">
          <Alert variant="info">Conecta tu LLM para empezar a chatear — todo lo demás es opcional.</Alert>
        </div>
      )}
      {error && (
        <div className="mb-4">
          <Alert variant="error">{error}</Alert>
        </div>
      )}

      {loading ? (
        <div className="flex justify-center py-16">
          <Spinner className="h-6 w-6 text-slate-400" />
        </div>
      ) : (
        <div className="space-y-4">
          <CardCredencial
            icon={<BrainIcon className="h-4 w-4 text-brand-600" />}
            titulo="Inteligencia (LLM)"
            descripcion="Anthropic, compatible con OpenAI, Vertex AI/Gemini, o usa tu Claude CLI / Codex CLI / Ollama ya instalados en esta máquina."
            estado={llmEstado}
            resumen={
              credentials?.llm
                ? [
                    LLM_KIND_LABELS[credentials.llm.kind],
                    credentials.llm.model_principal,
                    credentials.llm.masked,
                  ]
                    .filter(Boolean)
                    .join(" · ")
                : undefined
            }
            onQuitar={credentials?.llm ? handleQuitarLlm : undefined}
            quitando={quitando === "llm"}
            defaultExpanded={!credentials?.llm}
            destacado
          >
            <SelectorLLM detect={detect} onConnected={load} />
          </CardCredencial>

          <CardCredencial
            icon={<MicIcon className="h-4 w-4 text-brand-600" />}
            titulo="Voz"
            descripcion="Transcripción (Deepgram) y síntesis de voz (ElevenLabs/Polly). Sin esto, la voz sigue funcionando con un stub — no bloquea el chat."
            estado={vozEstado}
            resumen={
              <>
                {credentials?.voice_stt && (
                  <FilaCredencialConectada onQuitar={handleQuitarStt} quitando={quitando === "stt"}>
                    STT: {credentials.voice_stt.provider}
                    {credentials.voice_stt.masked ? ` · ${credentials.voice_stt.masked}` : ""}
                  </FilaCredencialConectada>
                )}
                {credentials?.voice_tts && (
                  <FilaCredencialConectada onQuitar={handleQuitarTts} quitando={quitando === "tts"}>
                    TTS: {credentials.voice_tts.provider}
                    {credentials.voice_tts.voice_id ? ` · ${credentials.voice_tts.voice_id}` : ""}
                  </FilaCredencialConectada>
                )}
              </>
            }
          >
            <SelectorVoz localMode={detect?.local_mode === true} onSttConnected={load} onTtsConnected={load} />
          </CardCredencial>

          <CardCredencial
            icon={<SparklesIcon className="h-4 w-4 text-brand-600" />}
            titulo="Generación de imágenes"
            descripcion="Cualquier endpoint compatible con OpenAI Images. Sin esto, «generar imagen» sigue funcionando con un generador offline simple — no bloquea el chat."
            estado={imagenesEstado}
            resumen={
              credentials?.images
                ? [credentials.images.model, credentials.images.masked].filter(Boolean).join(" · ")
                : undefined
            }
            onQuitar={credentials?.images ? handleQuitarImagenes : undefined}
            quitando={quitando === "imagenes"}
          >
            <SelectorImagenes onConnected={load} />
          </CardCredencial>

          <CardCredencial
            icon={<SearchIcon className="h-4 w-4 text-brand-600" />}
            titulo="Búsqueda web"
            descripcion="Brave Search o Tavily, para que «buscar en la web» y «comparar precios» usen resultados reales. Sin esto, siguen funcionando con resultados de ejemplo — no bloquea el chat."
            estado={busquedaEstado}
            resumen={
              credentials?.search
                ? [credentials.search.provider, credentials.search.masked].filter(Boolean).join(" · ")
                : undefined
            }
            onQuitar={credentials?.search ? handleQuitarBusqueda : undefined}
            quitando={quitando === "busqueda"}
          >
            <SelectorBusqueda onConnected={load} />
          </CardCredencial>

          <CardCredencial
            icon={<PlugIcon className="h-4 w-4 text-brand-600" />}
            titulo="Casa inteligente (Home Assistant)"
            descripcion="Tu propia instancia de Home Assistant (URL + Long-Lived Access Token) para que el agente vea y controle tus dispositivos desde el chat. Nunca cerraduras."
            estado={casaEstado}
            resumen={
              smarthome?.configured
                ? [smarthome.base_url, smarthome.reachable === false ? "sin respuesta ahora mismo" : undefined]
                    .filter(Boolean)
                    .join(" · ")
                : undefined
            }
            onQuitar={smarthome?.configured ? handleQuitarCasa : undefined}
            quitando={quitando === "casa"}
          >
            <SelectorCasaInteligente onConnected={load} />
          </CardCredencial>

          <CardServidoresMcp localMode={detect?.local_mode === true} />

          <Card>
            <CardHeader
              title={
                <span className="flex items-center gap-2">
                  <PlugIcon className="h-4 w-4 text-brand-600" />
                  Telefonía (Twilio)
                </span>
              }
              description="Tu propia cuenta de Twilio (Account SID + Auth Token) para llamadas del asistente; las credenciales se cifran por cuenta."
            />
            <CardBody>
              <p className="mb-3 text-sm text-slate-500 dark:text-slate-400">
                Se configura desde Conectores, junto con el resto de tus cuentas.
              </p>
              <Link href="/app/conectores" className={ENLACE_BOTON_CONECTORES}>
                Ir a Conectores
              </Link>
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              title={
                <span className="flex items-center gap-2">
                  <PlugIcon className="h-4 w-4 text-brand-600" />
                  Conectores (Google, Microsoft, Meta, X, YouTube, Slack)
                </span>
              }
              description="Correo, calendario y redes sociales vía OAuth 2.0 — cada uno con tu propia cuenta."
            />
            <CardBody>
              <p className="mb-3 text-sm text-slate-500 dark:text-slate-400">
                Autoriza cada cuenta desde Conectores; todos son opcionales y configurables cuando quieras.
              </p>
              <Link href="/app/conectores" className={ENLACE_BOTON_CONECTORES}>
                Ir a Conectores
              </Link>
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              title={
                <span className="flex items-center gap-2">
                  <SendIcon className="h-4 w-4 text-brand-600" />
                  Mensajería (Telegram, Discord)
                </span>
              }
              description="Conecta el bot token de tu propio bot de Telegram o Discord."
            />
            <CardBody>
              <p className="mb-3 text-sm text-slate-500 dark:text-slate-400">
                Se configura desde Conectores junto con el resto de integraciones.
              </p>
              <Link href="/app/conectores" className={ENLACE_BOTON_CONECTORES}>
                Ir a Conectores
              </Link>
            </CardBody>
          </Card>
        </div>
      )}
    </div>
  );
}
