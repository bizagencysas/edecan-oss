/**
 * Cliente web del transporte `WS /v1/voice/realtime`
 * (`edecan.voice.realtime.v1`). El contrato público copia
 * `RealtimeVoiceClient.swift`: el token viaja en el primer frame, nunca
 * en la URL. La UI decide cuándo hablar, interrumpir y reproducir.
 */

import { API_BASE_URL } from "./api";

export const REALTIME_VOICE_PROTOCOL = "edecan.voice.realtime.v1";
export const REALTIME_VOICE_PATH = "/v1/voice/realtime";

export type RealtimeAudioMime = "audio/wav" | "audio/webm" | "audio/mpeg";
export type RealtimeImageMime = "image/jpeg" | "image/png" | "image/webp";

export interface RealtimeVoiceEvent {
  type: string;
  turnId: number | null;
  sequence: number | null;
  mime: string | null;
  audio: Uint8Array | null;
  text: string | null;
  state: string | null;
  message: string | null;
  language: string | null;
  protocol: string | null;
  conversationId: string | null;
  bytes: number | null;
  event: unknown;
}

export type RealtimeVoiceOutgoing =
  | { type: "authenticate"; token: string; conversation_id?: string }
  | { type: "audio"; mime: RealtimeAudioMime; data: string }
  | { type: "image"; mime: RealtimeImageMime; data: string }
  | { type: "commit" }
  | { type: "speak"; text: string }
  | { type: "interrupt" }
  | { type: "ping" }
  | { type: "close" };

export class RealtimeVoiceError extends Error {
  readonly code?: number;

  constructor(message: string, code?: number) {
    super(message);
    this.name = "RealtimeVoiceError";
    this.code = code;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function optionalInt(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function optionalString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

export function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
    const chunk = 0x2000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

export function base64ToBytes(encoded: string): Uint8Array | null {
  try {
    const binary = atob(encoded);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return bytes;
  } catch {
    return null;
  }
}

/** Igual que `RealtimeVoiceEvent(json:)` en iOS: `data` base64 → bytes, o null. */
export function parseRealtimeVoiceEvent(raw: unknown): RealtimeVoiceEvent {
  const json = isRecord(raw) ? raw : {};
  const encoded = optionalString(json.data);
  return {
    type: optionalString(json.type) ?? "unknown",
    turnId: optionalInt(json.turn_id),
    sequence: optionalInt(json.sequence),
    mime: optionalString(json.mime),
    audio: encoded ? base64ToBytes(encoded) : null,
    text: optionalString(json.text),
    state: optionalString(json.state),
    message: optionalString(json.message),
    language: optionalString(json.language),
    protocol: optionalString(json.protocol),
    conversationId: optionalString(json.conversation_id),
    bytes: optionalInt(json.bytes),
    event: "event" in json ? json.event : null,
  };
}

export function realtimeVoiceUrl(apiBase: string = API_BASE_URL): string {
  const base = new URL(apiBase);
  base.protocol = base.protocol === "https:" ? "wss:" : "ws:";
  base.pathname = REALTIME_VOICE_PATH;
  base.search = "";
  base.hash = "";
  return base.toString();
}

export function normalizeRealtimeAudioMime(mime: string | null | undefined): RealtimeAudioMime | null {
  const clean = (mime ?? "").split(";", 1)[0]?.trim().toLowerCase() ?? "";
  if (clean === "audio/wav" || clean === "audio/x-wav" || clean === "audio/wave") return "audio/wav";
  if (clean === "audio/webm") return "audio/webm";
  if (clean === "audio/mpeg" || clean === "audio/mp3") return "audio/mpeg";
  return null;
}

export function encodeRealtimeAudioFrame(
  data: Uint8Array,
  mime: RealtimeAudioMime = "audio/wav",
): Extract<RealtimeVoiceOutgoing, { type: "audio" }> {
  return { type: "audio", mime, data: bytesToBase64(data) };
}

export function preferredRecordingMime(): RealtimeAudioMime {
  if (typeof MediaRecorder === "undefined") return "audio/webm";
  if (MediaRecorder.isTypeSupported("audio/webm;codecs=opus")) return "audio/webm";
  if (MediaRecorder.isTypeSupported("audio/webm")) return "audio/webm";
  if (MediaRecorder.isTypeSupported("audio/mpeg")) return "audio/mpeg";
  return "audio/webm";
}

async function toBytes(data: Uint8Array | ArrayBuffer | Blob): Promise<Uint8Array> {
  if (data instanceof Uint8Array) return data;
  if (data instanceof ArrayBuffer) return new Uint8Array(data);
  return new Uint8Array(await data.arrayBuffer());
}

export class RealtimeVoiceClient {
  private socket: WebSocket | null = null;
  private readonly url: string;
  private readonly token: string;
  private readonly conversationId: string | null;
  private readonly listeners = new Set<(event: RealtimeVoiceEvent) => void>();
  private sendQueue: Promise<void> = Promise.resolve();
  private ready = false;
  private closed = false;

  constructor(options: { url: string; token: string; conversationId?: string | null }) {
    this.url = options.url;
    this.token = options.token;
    this.conversationId = options.conversationId ?? null;
  }

  get isReady(): boolean {
    return this.ready && this.socket?.readyState === WebSocket.OPEN;
  }

  addEventListener(listener: (event: RealtimeVoiceEvent) => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  waitForEvent(
    types: string | readonly string[],
    timeoutMs = 45_000,
    signal?: AbortSignal,
  ): Promise<RealtimeVoiceEvent> {
    const wanted = new Set(typeof types === "string" ? [types] : types);
    return new Promise((resolve, reject) => {
      const finish = (fn: () => void) => {
        clearTimeout(timer);
        unsub();
        signal?.removeEventListener("abort", onAbort);
        fn();
      };
      const onAbort = () => finish(() => reject(new RealtimeVoiceError("Espera realtime cancelada.")));
      const timer = setTimeout(() => {
        finish(() => reject(new RealtimeVoiceError(`Tiempo de espera agotado (${[...wanted].join(", ")}).`)));
      }, timeoutMs);
      const unsub = this.addEventListener((event) => {
        if (wanted.has(event.type)) {
          finish(() => resolve(event));
        } else if (event.type === "error") {
          finish(() => reject(new RealtimeVoiceError(event.message || "Error de voz realtime.")));
        }
      });
      if (signal?.aborted) {
        onAbort();
        return;
      }
      signal?.addEventListener("abort", onAbort, { once: true });
    });
  }

  async connect(): Promise<RealtimeVoiceEvent> {
    if (this.closed) throw new RealtimeVoiceError("La sesión realtime ya se cerró.");
    if (typeof WebSocket === "undefined") {
      throw new RealtimeVoiceError("Este entorno no soporta WebSocket.");
    }

    const abort = new AbortController();
    const ready = this.waitForEvent("ready", 12_000, abort.signal);
    this.socket = new WebSocket(this.url);
    this.socket.addEventListener("message", (event) => this.handleMessage(event.data));
    this.socket.addEventListener("close", (event) => {
      this.ready = false;
      if (this.closed) return;
      this.emit(
        parseRealtimeVoiceEvent({
          type: "error",
          message: `La sesión realtime se cerró (${event.code}).`,
        }),
      );
    });

    try {
      await new Promise<void>((resolve, reject) => {
        const socket = this.socket;
        if (!socket) {
          reject(new RealtimeVoiceError("No se pudo abrir el WebSocket."));
          return;
        }
        socket.addEventListener("open", () => resolve(), { once: true });
        socket.addEventListener(
          "error",
          () => reject(new RealtimeVoiceError("No se pudo conectar al WebSocket de voz.")),
          { once: true },
        );
      });

      await this.send(
        this.conversationId
          ? { type: "authenticate", token: this.token, conversation_id: this.conversationId }
          : { type: "authenticate", token: this.token },
      );
      const event = await ready;
      if (event.type !== "ready") {
        throw new RealtimeVoiceError("La sesión realtime no quedó lista.");
      }
      this.ready = true;
      return event;
    } catch (error) {
      abort.abort();
      this.close();
      throw error;
    }
  }

  async speak(text: string): Promise<void> {
    await this.send({ type: "speak", text });
  }

  async sendAudio(
    data: Uint8Array | ArrayBuffer | Blob,
    mime: RealtimeAudioMime = "audio/wav",
  ): Promise<void> {
    const bytes = await toBytes(data);
    await this.send(encodeRealtimeAudioFrame(bytes, mime));
  }

  async sendImage(
    data: Uint8Array | ArrayBuffer | Blob,
    mime: RealtimeImageMime = "image/jpeg",
  ): Promise<void> {
    const bytes = await toBytes(data);
    await this.send({ type: "image", mime, data: bytesToBase64(bytes) });
  }

  async commitAudio(): Promise<void> {
    await this.send({ type: "commit" });
  }

  async interrupt(): Promise<void> {
    if (!this.isReady) return;
    await this.send({ type: "interrupt" });
  }

  async ping(): Promise<void> {
    await this.send({ type: "ping" });
  }

  close(): void {
    this.closed = true;
    this.ready = false;
    const socket = this.socket;
    this.socket = null;
    if (!socket) return;
    if (socket.readyState === WebSocket.OPEN) {
      try {
        socket.send(JSON.stringify({ type: "close" } satisfies RealtimeVoiceOutgoing));
      } catch {
        // El cierre local no debe fallar por un socket que ya se está yendo.
      }
    }
    if (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING) {
      socket.close(1000, "client closed");
    }
  }

  private handleMessage(raw: unknown): void {
    if (typeof raw !== "string") {
      this.emit(parseRealtimeVoiceEvent({ type: "error", message: "Frame realtime no compatible." }));
      return;
    }
    try {
      this.emit(parseRealtimeVoiceEvent(JSON.parse(raw)));
    } catch {
      this.emit(parseRealtimeVoiceEvent({ type: "error", message: "El servidor envió un evento realtime inválido." }));
    }
  }

  private emit(event: RealtimeVoiceEvent): void {
    for (const listener of this.listeners) listener(event);
  }

  private send(message: RealtimeVoiceOutgoing): Promise<void> {
    this.sendQueue = this.sendQueue.then(() => {
      const socket = this.socket;
      if (!socket || socket.readyState !== WebSocket.OPEN) {
        throw new RealtimeVoiceError("La sesión realtime no está conectada.");
      }
      socket.send(JSON.stringify(message));
    });
    return this.sendQueue;
  }
}

/**
 * Reproduce chunks TTS en cuanto llegan. `audio/mpeg` usa MediaSource;
 * `audio/wav` (stub) suele venir en un solo frame y se reproduce como Blob.
 */
export class IncrementalTtsPlayer {
  private audio: HTMLAudioElement | null = null;
  private mediaSource: MediaSource | null = null;
  private sourceBuffer: SourceBuffer | null = null;
  private pending: Uint8Array[] = [];
  private blobChunks: Uint8Array[] = [];
  private objectUrl: string | null = null;
  private mime: string | null = null;
  private usingMediaSource = false;
  private ended = false;
  private stopped = true;
  private resolveEnded: (() => void) | null = null;
  private endedPromise: Promise<void> | null = null;

  get isPlaying(): boolean {
    return !this.stopped && this.audio !== null && !this.audio.paused;
  }

  /** El `<audio>` de la sesión vigente — para medidores de nivel / barge-in. */
  get audioElement(): HTMLAudioElement | null {
    return this.audio;
  }

  beginSession(): Promise<void> {
    this.notifyEnded();
    this.teardown();
    this.stopped = false;
    this.ended = false;
    this.pending = [];
    this.blobChunks = [];
    this.endedPromise = new Promise((resolve) => {
      this.resolveEnded = resolve;
    });
    return this.endedPromise;
  }

  append(chunk: Uint8Array, mime: string): void {
    if (this.stopped || chunk.byteLength === 0) return;
    if (!this.audio) this.start(mime);
    if (this.usingMediaSource) {
      this.pending.push(chunk);
      this.flush();
      return;
    }
    this.blobChunks.push(chunk);
    if (this.mime === "audio/wav" && this.blobChunks.length === 1) {
      this.playConcatenatedBlob();
    }
  }

  end(): void {
    if (this.stopped) return;
    this.ended = true;
    if (this.usingMediaSource) {
      this.flush();
      return;
    }
    if (this.blobChunks.length > 0 && (!this.audio || this.audio.paused)) {
      this.playConcatenatedBlob();
      return;
    }
    if (this.blobChunks.length === 0) this.notifyEnded();
  }

  stop(): void {
    this.stopped = true;
    this.teardown();
    this.notifyEnded();
  }

  waitUntilEnded(): Promise<void> {
    return this.endedPromise ?? Promise.resolve();
  }

  private start(mime: string): void {
    if (typeof window === "undefined" || typeof Audio === "undefined") return;
    this.mime = mime;
    const audio = new Audio();
    audio.autoplay = true;
    audio.addEventListener("ended", () => {
      if (this.ended || !this.usingMediaSource) this.notifyEnded();
    });
    audio.addEventListener("error", () => this.notifyEnded());
    this.audio = audio;

    const mseSupported =
      typeof MediaSource !== "undefined" &&
      typeof MediaSource.isTypeSupported === "function" &&
      MediaSource.isTypeSupported(mime);

    if (mseSupported) {
      const mediaSource = new MediaSource();
      this.mediaSource = mediaSource;
      this.usingMediaSource = true;
      this.objectUrl = URL.createObjectURL(mediaSource);
      audio.src = this.objectUrl;
      mediaSource.addEventListener("sourceopen", () => {
        try {
          const sourceBuffer = mediaSource.addSourceBuffer(mime);
          sourceBuffer.mode = "sequence";
          sourceBuffer.addEventListener("updateend", () => this.flush());
          this.sourceBuffer = sourceBuffer;
          this.flush();
        } catch {
          this.usingMediaSource = false;
        }
      });
      void audio.play().catch(() => undefined);
      return;
    }

    this.usingMediaSource = false;
  }

  private flush(): void {
    const sourceBuffer = this.sourceBuffer;
    const mediaSource = this.mediaSource;
    if (!sourceBuffer || sourceBuffer.updating) return;
    const next = this.pending.shift();
    if (next) {
      sourceBuffer.appendBuffer(new Uint8Array(next));
      return;
    }
    if (this.ended && mediaSource && mediaSource.readyState === "open") {
      try {
        mediaSource.endOfStream();
      } catch {
        // endOfStream puede fallar si el buffer ya cerró.
      }
      if (this.audio?.paused || this.audio?.ended) this.notifyEnded();
    }
  }

  private playConcatenatedBlob(): void {
    if (!this.audio || this.blobChunks.length === 0) return;
    const blob = new Blob(this.blobChunks as BlobPart[], { type: this.mime ?? "audio/wav" });
    if (this.objectUrl) URL.revokeObjectURL(this.objectUrl);
    this.objectUrl = URL.createObjectURL(blob);
    this.audio.src = this.objectUrl;
    void this.audio.play().catch(() => this.notifyEnded());
  }

  private teardown(): void {
    this.audio?.pause();
    this.audio = null;
    if (this.mediaSource && this.mediaSource.readyState === "open") {
      try {
        this.mediaSource.endOfStream();
      } catch {
        // ignore
      }
    }
    this.mediaSource = null;
    this.sourceBuffer = null;
    this.usingMediaSource = false;
    if (this.objectUrl) {
      URL.revokeObjectURL(this.objectUrl);
      this.objectUrl = null;
    }
    this.mime = null;
    this.pending = [];
    this.blobChunks = [];
  }

  private notifyEnded(): void {
    const resolve = this.resolveEnded;
    this.resolveEnded = null;
    this.endedPromise = null;
    resolve?.();
  }
}
