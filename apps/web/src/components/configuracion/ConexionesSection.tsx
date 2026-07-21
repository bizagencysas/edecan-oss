"use client";

/**
 * Centro de credenciales (DIRECCION_ACTUAL.md "Pantalla de Configuración" +
 * "Principio de UX no negociable: configuración de pocos clicks"):
 * configuración PROGRESIVA en tarjetas "Conectar" independientes, nunca un
 * formulario gigante. Solo el LLM es necesario para chatear — el resto
 * (voz, telefonía, conectores OAuth, mensajería) es siempre opcional.
 */

import { useCallback, useEffect, useState, type ReactNode } from "react";

import { CardCredencial, FilaCredencialConectada, type EstadoCredencial } from "@/components/configuracion/CardCredencial";
import { CardServidoresMcp } from "@/components/configuracion/CardServidoresMcp";
import { AdsConnectionCard } from "@/components/configuracion/AdsConnectionCard";
import { ConnectorsSettings } from "@/components/configuracion/ConnectorsSettings";
import { ICloudConnectionCard } from "@/components/configuracion/ICloudConnectionCard";
import { PhonePairingCard } from "@/components/configuracion/PhonePairingCard";
import { StripeConnectionCard } from "@/components/configuracion/StripeConnectionCard";
import { TravelConnectionsPanel } from "@/components/configuracion/TravelConnectionsPanel";
import { SelectorBusqueda } from "@/components/configuracion/SelectorBusqueda";
import { SelectorCasaInteligente } from "@/components/configuracion/SelectorCasaInteligente";
import { SelectorImagenes } from "@/components/configuracion/SelectorImagenes";
import { SelectorLLM } from "@/components/configuracion/SelectorLLM";
import { SelectorModeloLLM } from "@/components/configuracion/SelectorModeloLLM";
import { SelectorVoz } from "@/components/configuracion/SelectorVoz";
import { BrainIcon, MicIcon, PlugIcon, SearchIcon, SparklesIcon } from "@/components/icons";
import { Alert, Spinner } from "@/components/ui";
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

export function ConexionesSection({
  onLocalModeDetected,
}: {
  onLocalModeDetected?: (localMode: boolean) => void;
} = {}) {
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
      onLocalModeDetected?.(det.local_mode === true);
      setSmarthome(casa);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo cargar la configuración.");
    } finally {
      setLoading(false);
    }
  }, [onLocalModeDetected]);

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
    <section id="conexiones" className="scroll-mt-6 lg:col-span-2" aria-labelledby="conexiones-title">
      <div className="mb-5">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-brand-600 dark:text-brand-400">
          Empieza por aquí
        </p>
        <h2 id="conexiones-title" className="mt-1 text-xl font-semibold text-slate-900 dark:text-slate-100">
          Conexiones
        </h2>
        <p className="mt-1 max-w-3xl text-sm leading-6 text-slate-500 dark:text-slate-400">
          Elige cómo quieres que Edecan piense. Luego conecta solo lo que quieras usar; todo lo demás es opcional.
        </p>
        <p className="mt-1 text-xs text-slate-400 dark:text-slate-500">
          Las API keys se validan al conectarlas y se guardan cifradas; nunca vuelven a mostrarse completas.
        </p>
      </div>

      {!loading && status && !status.llm_configured && (
        <div className="mb-4">
          <Alert variant="info">Elige cómo pensará Edecan para empezar a conversar. Todo lo demás es opcional.</Alert>
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
            titulo="Cómo piensa Edecan"
            descripcion="Usa Claude CLI, Codex CLI u Ollama sin API key, o conecta de forma segura Anthropic, OpenAI o Gemini."
            estado={llmEstado}
            resumen={
              credentials?.llm
                ? <>
                    <FilaCredencialConectada onQuitar={handleQuitarLlm} quitando={quitando === "llm"}>
                      {[LLM_KIND_LABELS[credentials.llm.kind], credentials.llm.model_principal, credentials.llm.masked]
                        .filter(Boolean)
                        .join(" · ")}
                    </FilaCredencialConectada>
                    <SelectorModeloLLM onUpdated={load} />
                  </>
                : undefined
            }
            defaultExpanded={!credentials?.llm}
            destacado
          >
            <SelectorLLM detect={detect} onConnected={load} />
          </CardCredencial>

          <PhonePairingCard />

          <div className="rounded-2xl border border-slate-200 bg-slate-50/60 p-4 dark:border-slate-800 dark:bg-slate-900/30">
            <div className="text-sm font-semibold text-slate-800 dark:text-slate-100">
              Agregar conexiones opcionales
              <span className="ml-2 font-normal text-slate-500 dark:text-slate-400">
                Voz, imágenes, búsqueda, casa y cuentas externas
              </span>
            </div>
            <div className="mt-4 space-y-4 border-t border-slate-200 pt-4 dark:border-slate-800">
          <ConnectionCategory title="Voz, contenido y casa" description="Servicios que amplían lo que Edecan puede escuchar, crear, buscar y controlar.">
          <CardCredencial
            icon={<MicIcon className="h-4 w-4 text-brand-600" />}
            titulo="Voz"
            descripcion="Para dictarle a Edecan y escuchar sus respuestas con servicios de voz externos. El chat de texto siempre seguirá disponible."
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
            descripcion="Para pedir imágenes desde el chat usando tu proveedor preferido. Es completamente opcional."
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
            descripcion="Ya funciona sin configuración. Puedes conectar Brave Search o Tavily como proveedor opcional."
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
          </ConnectionCategory>

          <ConnectionCategory title="Correo, calendario, llamadas y mensajería" description="Google, Microsoft, redes, Twilio, bots e iCloud.">
            <ConnectorsSettings embedded />
            <div className="mt-4"><ICloudConnectionCard /></div>
          </ConnectionCategory>

          <ConnectionCategory title="Viajes y entregas" description="Amadeus para vuelos y hoteles; AfterShip para paquetes.">
            <TravelConnectionsPanel />
          </ConnectionCategory>

          <ConnectionCategory title="Negocio y finanzas" description="Meta Ads y Stripe, siempre opcionales.">
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <AdsConnectionCard />
              <StripeConnectionCard />
            </div>
          </ConnectionCategory>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

function ConnectionCategory({ title, description, children }: { title: string; description: string; children: ReactNode }) {
  return (
    <details className="rounded-xl border border-slate-200 bg-white p-3 dark:border-slate-800 dark:bg-slate-900">
      <summary className="cursor-pointer select-none">
        <span className="block text-sm font-semibold text-slate-800 dark:text-slate-100">{title}</span>
        <span className="mt-0.5 block text-xs text-slate-500 dark:text-slate-400">{description}</span>
      </summary>
      <div className="mt-4 space-y-4 border-t border-slate-100 pt-4 dark:border-slate-800">{children}</div>
    </details>
  );
}
