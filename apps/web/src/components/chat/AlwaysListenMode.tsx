"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { speakText, transcribeAudio } from "@/lib/api";
import { splitIntoSentences } from "@/lib/speech";
import type { MessageOut } from "@/lib/types";
import {
  SPEECH_RECOGNITION_LOCALE,
  transcriptContainsWakePhrase,
  transcriptRequestsSleep,
} from "@/lib/wake-word-detection";
import { WAKE_WORD_PRESETS } from "@/lib/wakeWords";

import { ConfirmationCard } from "./ConfirmationCard";
import { ListeningOrb, type OrbState } from "./ListeningOrb";
import { messageText } from "./utils";

// La Web Speech API (`SpeechRecognition`/`webkitSpeechRecognition`) no tiene
// tipos oficiales en el DOM lib de TypeScript -- se declara acá el subconjunto
// mínimo que este componente usa, en vez de recurrir a `any`.
interface SpeechRecognitionResultLike {
  readonly isFinal: boolean;
  readonly length: number;
  [index: number]: { transcript: string };
}
interface SpeechRecognitionEventLike extends Event {
  resultIndex: number;
  results: ArrayLike<SpeechRecognitionResultLike>;
}
interface SpeechRecognitionLike extends EventTarget {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start: () => void;
  stop: () => void;
  abort: () => void;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: Event) => void) | null;
  onend: (() => void) | null;
}
type SpeechRecognitionCtor = new () => SpeechRecognitionLike;

function getSpeechRecognitionCtor(): SpeechRecognitionCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as {
    SpeechRecognition?: SpeechRecognitionCtor;
    webkitSpeechRecognition?: SpeechRecognitionCtor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

const STORAGE_KEY = "edecan.always_listen.wake_word";
const SILENCIO_MS_PARA_CORTAR = 1400;
const DURACION_MAXIMA_MS = 30_000;
const NIVEL_UMBRAL_VOZ = 0.08;

function cargarWakeWordGuardada(): string {
  if (typeof window === "undefined") return WAKE_WORD_PRESETS[0];
  try {
    return window.localStorage.getItem(STORAGE_KEY) || WAKE_WORD_PRESETS[0];
  } catch {
    return WAKE_WORD_PRESETS[0];
  }
}

function guardarWakeWord(valor: string): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, valor);
  } catch {
    // localStorage puede fallar en modo privado -- no es crítico, se pierde
    // la preferencia entre sesiones nada más.
  }
}

type Fase =
  | "sin_soporte"
  | "esperando_palabra_clave"
  | "escuchando"
  | "procesando"
  | "hablando"
  | "pausado"
  | "error";

const ORB_ESTADO_POR_FASE: Record<Fase, OrbState> = {
  sin_soporte: "pausado",
  esperando_palabra_clave: "esperando_palabra_clave",
  escuchando: "escuchando",
  procesando: "procesando",
  hablando: "hablando",
  pausado: "pausado",
  error: "pausado",
};

const ETIQUETA_POR_FASE: Record<Fase, string> = {
  sin_soporte: "Tu navegador no soporta reconocimiento de voz continuo.",
  esperando_palabra_clave: "Esperando la palabra clave…",
  escuchando: "Te escucho…",
  procesando: "Pensando…",
  hablando: "Hablando…",
  pausado: "Esperando tu confirmación",
  error: "Algo falló -- reintentando…",
};

/** Medidor de nivel de audio (0-1) sobre un `MediaStream` en vivo, vía
 * `AnalyserNode` -- alimenta la reactividad del orbe mientras el usuario
 * habla. */
function crearMedidorDeNivel(stream: MediaStream): { nivel: () => number; cerrar: () => void } {
  const AudioCtx =
    window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
  const contexto = new AudioCtx();
  const fuente = contexto.createMediaStreamSource(stream);
  const analizador = contexto.createAnalyser();
  analizador.fftSize = 512;
  fuente.connect(analizador);
  const buffer = new Uint8Array(analizador.frequencyBinCount);
  return {
    nivel(): number {
      analizador.getByteFrequencyData(buffer);
      let suma = 0;
      for (let i = 0; i < buffer.length; i++) suma += buffer[i];
      return Math.min(1, suma / buffer.length / 128);
    },
    cerrar() {
      fuente.disconnect();
      void contexto.close();
    },
  };
}

/** Mismo medidor, pero sobre el audio que produce un `<audio>` (TTS) en vez
 * de un micrófono -- hay que reconectar la salida a los parlantes
 * (`analizador.connect(contexto.destination)`), porque `createMediaElementSource`
 * intercepta el audio del elemento y, sin esa reconexión, quedaría mudo. */
function crearMedidorDeNivelDeAudio(
  audio: HTMLAudioElement,
): { nivel: () => number; cerrar: () => void } {
  const AudioCtx =
    window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
  const contexto = new AudioCtx();
  const fuente = contexto.createMediaElementSource(audio);
  const analizador = contexto.createAnalyser();
  analizador.fftSize = 512;
  fuente.connect(analizador);
  analizador.connect(contexto.destination);
  const buffer = new Uint8Array(analizador.frequencyBinCount);
  return {
    nivel(): number {
      analizador.getByteFrequencyData(buffer);
      let suma = 0;
      for (let i = 0; i < buffer.length; i++) suma += buffer[i];
      return Math.min(1, suma / buffer.length / 128);
    },
    cerrar() {
      fuente.disconnect();
      void contexto.close();
    },
  };
}

export function AlwaysListenMode({
  onClose,
  onSendText,
  messages,
  sending,
  pendingConfirmation,
  onConfirm,
  voiceId,
  pendingNativeAudio = null,
  onNativeAudioConsumed,
}: {
  onClose: () => void;
  onSendText: (text: string) => void;
  messages: MessageOut[];
  sending: boolean;
  pendingConfirmation: { tool_call_id: string; name: string; args: Record<string, unknown> } | null;
  onConfirm: (approved: boolean) => void;
  voiceId: string | null;
  /** Audio del comando ya capturado por el detector nativo de wake word
   * (Tauri, ver `apps/web/src/lib/tauriListen.ts` y el evento
   * `edecan://wake-detected` suscrito en `app/page.tsx`) -- cuando llega,
   * este componente se salta la fase de "esperar la palabra clave" (Rust ya
   * la detectó) y entra directo a transcribir+enviar ese audio, reusando el
   * mismo pipeline que ya usa la captura del navegador. */
  pendingNativeAudio?: Blob | null;
  /** Se llama apenas se toma `pendingNativeAudio` para procesarlo, así el
   * padre lo limpia y no lo vuelve a entregar en un futuro re-render. */
  onNativeAudioConsumed?: () => void;
}) {
  const [fase, setFase] = useState<Fase>(() => (pendingNativeAudio ? "procesando" : "esperando_palabra_clave"));
  const [nivelAudio, setNivelAudio] = useState(0);
  const [wakeWord, setWakeWord] = useState(cargarWakeWordGuardada);
  const [personalizado, setPersonalizado] = useState(
    () => !WAKE_WORD_PRESETS.includes(cargarWakeWordGuardada() as (typeof WAKE_WORD_PRESETS)[number]),
  );
  const [mostrarAjustes, setMostrarAjustes] = useState(false);
  const [sesionActiva, setSesionActiva] = useState(Boolean(pendingNativeAudio));
  const sesionActivaRef = useRef(Boolean(pendingNativeAudio));
  const wakeWordRef = useRef(wakeWord);
  wakeWordRef.current = wakeWord;

  const faseRef = useRef<Fase>(fase);
  faseRef.current = fase;
  const reconocedorRef = useRef<SpeechRecognitionLike | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const grabadorRef = useRef<MediaRecorder | null>(null);
  const nivelFrameRef = useRef<number | null>(null);
  const medidorRef = useRef<{ nivel: () => number; cerrar: () => void } | null>(null);
  const cerradoRef = useRef(false);
  const esperandoRespuestaRef = useRef(false);
  const mensajesVistosRef = useRef(0);

  const SpeechRecognitionCtor = useMemo(() => getSpeechRecognitionCtor(), []);

  function pararMedidorNivel() {
    if (nivelFrameRef.current !== null) cancelAnimationFrame(nivelFrameRef.current);
    nivelFrameRef.current = null;
    medidorRef.current?.cerrar();
    medidorRef.current = null;
    setNivelAudio(0);
  }

  function pararTodo() {
    reconocedorRef.current?.abort();
    reconocedorRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    grabadorRef.current = null;
    pararMedidorNivel();
  }

  function activarSesionContinua() {
    sesionActivaRef.current = true;
    setSesionActiva(true);
  }

  function dormirSesion() {
    sesionActivaRef.current = false;
    setSesionActiva(false);
    iniciarEsperaDePalabraClave();
  }

  /**
   * Después de la primera activación, cada respuesta abre automáticamente el
   * micrófono para el siguiente turno. La palabra clave vuelve a ser necesaria
   * únicamente si la persona dice una orden de descanso o cierra el modo.
   */
  function continuarConversacion() {
    if (cerradoRef.current) return;
    if (sesionActivaRef.current) {
      void iniciarCapturaDeComando();
    } else {
      iniciarEsperaDePalabraClave();
    }
  }

  // --- Fase 1: esperar la palabra clave (SpeechRecognition continuo) -------

  function iniciarEsperaDePalabraClave() {
    if (cerradoRef.current) return;
    if (!SpeechRecognitionCtor) {
      setFase("sin_soporte");
      return;
    }
    setFase("esperando_palabra_clave");
    const reconocedor = new SpeechRecognitionCtor();
    reconocedor.continuous = true;
    reconocedor.interimResults = true;
    reconocedor.lang = SPEECH_RECOGNITION_LOCALE;

    reconocedor.onresult = (evento) => {
      for (let i = evento.resultIndex; i < evento.results.length; i++) {
        const transcripcion = evento.results[i][0]?.transcript ?? "";
        if (transcriptContainsWakePhrase(transcripcion, wakeWordRef.current)) {
          reconocedor.abort();
          activarSesionContinua();
          void iniciarCapturaDeComando();
          return;
        }
      }
    };
    reconocedor.onerror = () => {
      if (cerradoRef.current || faseRef.current !== "esperando_palabra_clave") return;
      // `no-speech`/`network` son transitorios en modo continuo -- se
      // reintenta en vez de dejar el modo muerto en silencio.
      setTimeout(() => {
        if (!cerradoRef.current && faseRef.current === "esperando_palabra_clave") {
          iniciarEsperaDePalabraClave();
        }
      }, 500);
    };
    reconocedor.onend = () => {
      if (!cerradoRef.current && faseRef.current === "esperando_palabra_clave") {
        // Algunos navegadores cortan `continuous` solos tras un rato: se
        // relanza para que la escucha sea de verdad "siempre".
        iniciarEsperaDePalabraClave();
      }
    };
    reconocedorRef.current = reconocedor;
    try {
      reconocedor.start();
    } catch {
      setFase("error");
    }
  }

  // --- Fase 2: capturar el comando real (mismo pipeline que push-to-talk) --

  async function iniciarCapturaDeComando() {
    if (cerradoRef.current) return;
    setFase("escuchando");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const medidor = crearMedidorDeNivel(stream);
      medidorRef.current = medidor;

      const recorder = new MediaRecorder(stream);
      grabadorRef.current = recorder;
      const chunks: Blob[] = [];
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunks.push(e.data);
      };

      let habloAlgunaVez = false;
      let silencioDesde = Date.now();
      const inicio = Date.now();

      const medir = () => {
        const nivel = medidor.nivel();
        setNivelAudio(nivel);
        if (nivel > NIVEL_UMBRAL_VOZ) {
          habloAlgunaVez = true;
          silencioDesde = Date.now();
        }
        const silencioLargo = habloAlgunaVez && Date.now() - silencioDesde > SILENCIO_MS_PARA_CORTAR;
        const tiempoAgotado = Date.now() - inicio > DURACION_MAXIMA_MS;
        if (silencioLargo || tiempoAgotado) {
          if (recorder.state !== "inactive") recorder.stop();
          return;
        }
        nivelFrameRef.current = requestAnimationFrame(medir);
      };
      nivelFrameRef.current = requestAnimationFrame(medir);

      recorder.onstop = () => {
        pararMedidorNivel();
        stream.getTracks().forEach((t) => t.stop());
        streamRef.current = null;
        void procesarComando(new Blob(chunks, { type: recorder.mimeType || "audio/webm" }));
      };
      recorder.start();
    } catch {
      // Sin permiso de micrófono, o algún otro fallo transitorio, conserva la
      // sesión activa y reintenta sin exigir otra vez la palabra clave.
      setFase("error");
      setTimeout(() => continuarConversacion(), 1000);
    }
  }

  async function procesarComando(blob: Blob) {
    if (cerradoRef.current) return;
    if (blob.size === 0) {
      continuarConversacion();
      return;
    }
    setFase("procesando");
    try {
      const { text } = await transcribeAudio(blob);
      if (!text.trim()) {
        continuarConversacion();
        return;
      }
      if (transcriptRequestsSleep(text)) {
        dormirSesion();
        return;
      }
      esperandoRespuestaRef.current = true;
      mensajesVistosRef.current = messages.length;
      onSendText(text);
    } catch {
      setFase("error");
      setTimeout(() => continuarConversacion(), 1000);
    }
  }

  // --- Fase 3: hablar la respuesta, oración por oración ---------------------

  async function hablarRespuesta(texto: string) {
    if (cerradoRef.current) return;
    const oraciones = splitIntoSentences(texto);
    if (oraciones.length === 0) {
      continuarConversacion();
      return;
    }
    setFase("hablando");
    try {
      let siguiente: Promise<Blob> | null = speakText(oraciones[0], voiceId);
      for (let i = 0; i < oraciones.length; i++) {
        if (cerradoRef.current || siguiente === null) return;
        const blob = await siguiente;
        siguiente = i + 1 < oraciones.length ? speakText(oraciones[i + 1], voiceId) : null;

        const url = URL.createObjectURL(blob);
        const audio = new Audio(url);
        try {
          const medidor = crearMedidorDeNivelDeAudio(audio);
          medidorRef.current = medidor;
          const medir = () => {
            setNivelAudio(medidor.nivel());
            nivelFrameRef.current = requestAnimationFrame(medir);
          };
          nivelFrameRef.current = requestAnimationFrame(medir);
        } catch {
          // Si el navegador no admite `createMediaElementSource` dos veces
          // en el mismo contexto, el orbe simplemente no reacciona al audio
          // -- no es motivo para cortar la reproducción.
        }
        await new Promise<void>((resolve) => {
          audio.onended = () => resolve();
          audio.onerror = () => resolve();
          audio.play().catch(() => resolve());
        });
        pararMedidorNivel();
        URL.revokeObjectURL(url);
        if (cerradoRef.current) return;
      }
    } catch {
      // Un fallo de síntesis a mitad de la respuesta no debe trabar el
      // modo -- se sigue igual al siguiente ciclo de escucha.
    }
    if (!cerradoRef.current) continuarConversacion();
  }

  // --- Efectos: arranque/cierre, reacción a la respuesta del turno, pausa --

  useEffect(() => {
    cerradoRef.current = false;
    // Si ya llega con un audio nativo pendiente, no arranca la escucha de
    // palabra clave del navegador (innecesaria: Rust ya la detectó) -- el
    // efecto de `pendingNativeAudio` de más abajo se encarga de procesarlo.
    if (!pendingNativeAudio) iniciarEsperaDePalabraClave();
    return () => {
      cerradoRef.current = true;
      pararTodo();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Procesa el audio del comando que ya capturó el listener nativo (wake
  // word + corte por silencio ya resueltos en Rust) -- mismo criterio que si
  // lo hubiera grabado `iniciarCapturaDeComando()` acá adentro, pero sin
  // pedir permiso de micrófono de nuevo: ya viene con el WAV listo. Corre
  // tanto en el montaje (si `pendingNativeAudio` llegó de entrada) como si
  // el usuario ya tenía el overlay abierto y llega un evento nativo después.
  useEffect(() => {
    if (!pendingNativeAudio || cerradoRef.current) return;
    const audio = pendingNativeAudio;
    activarSesionContinua();
    onNativeAudioConsumed?.();
    reconocedorRef.current?.abort();
    reconocedorRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    pararMedidorNivel();
    void procesarComando(audio);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingNativeAudio]);

  useEffect(() => {
    if (!esperandoRespuestaRef.current) return;
    if (sending) return; // el turno sigue en curso
    if (pendingConfirmation) return; // se resuelve en el efecto de abajo
    if (messages.length <= mensajesVistosRef.current) return;
    const ultimo = messages[messages.length - 1];
    esperandoRespuestaRef.current = false;
    if (ultimo?.role === "assistant") {
      void hablarRespuesta(messageText(ultimo.content));
    } else {
      continuarConversacion();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages, sending, pendingConfirmation]);

  useEffect(() => {
    if (pendingConfirmation) {
      reconocedorRef.current?.abort();
      pararMedidorNivel();
      setFase("pausado");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingConfirmation]);

  function handleConfirmar(aprobado: boolean) {
    esperandoRespuestaRef.current = true;
    mensajesVistosRef.current = messages.length;
    onConfirm(aprobado);
  }

  function handleCerrar() {
    cerradoRef.current = true;
    pararTodo();
    onClose();
  }

  function handleCambiarWakeWord(valor: string) {
    setWakeWord(valor);
    wakeWordRef.current = valor;
    if (valor.trim()) guardarWakeWord(valor.trim());
  }

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col items-center justify-center gap-6 bg-slate-950/95 p-6 text-center"
      role="dialog"
      aria-modal="true"
      aria-label="Modo escuchar siempre"
    >
      <button
        onClick={handleCerrar}
        className="absolute right-4 top-4 rounded-full border border-slate-700 px-4 py-2 text-sm text-slate-200 hover:bg-slate-800"
      >
        Colgar
      </button>
      <button
        onClick={() => setMostrarAjustes((v) => !v)}
        className="absolute left-4 top-4 rounded-full border border-slate-700 px-4 py-2 text-sm text-slate-200 hover:bg-slate-800"
      >
        Palabra clave
      </button>

      {mostrarAjustes && (
        <div className="absolute left-4 top-16 w-72 rounded-xl border border-slate-700 bg-slate-900 p-4 text-left text-sm text-slate-100 shadow-xl">
          <p className="mb-2 font-medium">Activar con:</p>
          <select
            className="mb-2 w-full rounded-lg border border-slate-700 bg-slate-800 px-2 py-1.5 text-sm"
            value={personalizado ? "__custom__" : wakeWord}
            onChange={(e) => {
              if (e.target.value === "__custom__") {
                setPersonalizado(true);
                return;
              }
              setPersonalizado(false);
              handleCambiarWakeWord(e.target.value);
            }}
          >
            {WAKE_WORD_PRESETS.map((preset) => (
              <option key={preset} value={preset}>
                {preset}
              </option>
            ))}
            <option value="__custom__">Personalizado…</option>
          </select>
          {personalizado && (
            <input
              className="w-full rounded-lg border border-slate-700 bg-slate-800 px-2 py-1.5 text-sm"
              placeholder="Escribe tu frase de activación"
              value={WAKE_WORD_PRESETS.includes(wakeWord as (typeof WAKE_WORD_PRESETS)[number]) ? "" : wakeWord}
              onChange={(e) => handleCambiarWakeWord(e.target.value)}
              onBlur={(e) => {
                const value = e.target.value.trim();
                if (value) handleCambiarWakeWord(value);
              }}
            />
          )}
        </div>
      )}

      <ListeningOrb estado={ORB_ESTADO_POR_FASE[fase]} nivelAudio={nivelAudio} size={240} />

      <div>
        <p className="text-lg font-medium text-white">{ETIQUETA_POR_FASE[fase]}</p>
        {fase === "esperando_palabra_clave" && (
          <p className="mt-1 text-sm text-slate-400">
            Di «{wakeWord}» una sola vez para iniciar la conversación.
          </p>
        )}
        {fase === "escuchando" && sesionActiva && (
          <p className="mt-1 text-sm text-slate-400">
            Conversación activa. Habla con normalidad; di «descansa» cuando quieras terminar.
          </p>
        )}
        {fase === "sin_soporte" && (
          <p className="mt-1 max-w-sm text-sm text-slate-400">
            Intenta desde Chrome o Edge; este navegador no ofrece reconocimiento de voz continuo.
          </p>
        )}
      </div>

      {pendingConfirmation && (
        <ConfirmationCard
          name={pendingConfirmation.name}
          args={pendingConfirmation.args}
          onApprove={() => handleConfirmar(true)}
          onDeny={() => handleConfirmar(false)}
          loading={sending}
        />
      )}
    </div>
  );
}
