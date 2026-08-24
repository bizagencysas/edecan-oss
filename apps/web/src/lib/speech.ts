/** Trocea texto en oraciones para reproducción progresiva de voz: la primera
 * síntesis pedida al backend es corta, así que llega rápido y el audio
 * empieza a sonar casi de inmediato en vez de esperar a que se sintetice el
 * mensaje completo. Fragmentos resultantes demasiado cortos (p. ej. una
 * abreviatura como "Sr.") se fusionan con el siguiente para no disparar una
 * síntesis por casi nada. Compartida entre el botón "Escuchar" por mensaje
 * (`app/page.tsx`) y el modo "Escuchar siempre" (`AlwaysListenMode.tsx`).
 */
export function splitIntoSentences(text: string): string[] {
  const raw = text
    .split(/(?<=[.!?])\s+/)
    .map((s) => s.trim())
    .filter(Boolean);
  if (raw.length === 0) return [];

  const merged: string[] = [];
  for (const sentence of raw) {
    const last = merged[merged.length - 1];
    if (last !== undefined && last.length < 20) {
      merged[merged.length - 1] = `${last} ${sentence}`;
    } else {
      merged.push(sentence);
    }
  }
  return merged;
}

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

/** Transcripción provisional del navegador mientras se graba (no sustituye al STT del WS). */
export function startLiveTranscript(
  lang: string,
  onUpdate: (text: string) => void,
): (() => void) | null {
  const Ctor = getSpeechRecognitionCtor();
  if (!Ctor) return null;
  const recognition = new Ctor();
  recognition.continuous = true;
  recognition.interimResults = true;
  recognition.lang = lang;
  recognition.onresult = (event) => {
    let text = "";
    for (let i = 0; i < event.results.length; i++) {
      text += event.results[i]?.[0]?.transcript ?? "";
    }
    onUpdate(text.trim());
  };
  try {
    recognition.start();
  } catch {
    return null;
  }
  return () => {
    recognition.onresult = null;
    try {
      recognition.abort();
    } catch {
      // Algunos navegadores lanzan si ya estaba detenido.
    }
  };
}
