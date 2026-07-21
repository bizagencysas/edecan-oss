"use client";

import { useEffect, useRef, useState } from "react";

import { AlwaysListenMode } from "@/components/chat/AlwaysListenMode";
import { ChatComposer } from "@/components/chat/ChatComposer";
import { ConfirmationCard } from "@/components/chat/ConfirmationCard";
import { ConversationList } from "@/components/chat/ConversationList";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { ToolTimeline, type ToolEvent } from "@/components/chat/ToolTimeline";
import { messageText } from "@/components/chat/utils";
import { MenuIcon } from "@/components/icons";
import { Alert, Button, EmptyState, Spinner } from "@/components/ui";
import {
  confirmToolCallStream,
  createConversation,
  deleteConversation,
  getConversation,
  getPersona,
  listConversations,
  sendMessageStream,
  speakText,
  transcribeAudio,
  uploadFile,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { canSubmitChat, MAX_CHAT_ATTACHMENTS } from "@/lib/chat-attachments";
import { reduceToolTimeline } from "@/lib/chat-blocks";
import { splitIntoSentences } from "@/lib/speech";
import { isTauriApp, tauriListenEvent } from "@/lib/tauriListen";
import {
  FLAG_VOICE_WEB,
  type AgentEvent,
  type ChatAttachmentDraft,
  type ConversationOut,
  type MessageOut,
  type PendingConfirmationOut,
} from "@/lib/types";

type SpeakState = "loading" | "playing" | null;

const STARTER_REQUESTS = [
  "Organiza mis pendientes para hoy",
  "Crea un PDF, un Word y una presentación desde esta idea",
  "Revisa este documento y dime lo importante",
  "Recuérdame pagar mañana",
];

/** Payload de `edecan://wake-detected` (evento nativo, Tauri): el listener
 * en segundo plano ya detectó la wake word entrenada Y ya grabó -- con corte
 * por silencio -- el comando de voz que siguió, ver
 * `AlwaysListenMode.pendingNativeAudio`. */
interface WakeDetectedPayload {
  audio_base64: string;
  mime: string;
}

function base64ToBlob(base64: string, mime: string): Blob {
  const binario = atob(base64);
  const bytes = new Uint8Array(binario.length);
  for (let i = 0; i < binario.length; i++) bytes[i] = binario.charCodeAt(i);
  return new Blob([bytes], { type: mime || "audio/wav" });
}

function attachmentLocalId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return `attachment-${crypto.randomUUID()}`;
  return `attachment-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/** Chat principal (ARCHITECTURE.md §9, §10.7): conversaciones + streaming SSE + voz. */
export default function ChatPage() {
  const { me } = useAuth();
  const canVoice = Boolean(me?.flags?.[FLAG_VOICE_WEB]);
  const canRecordAudio =
    canVoice &&
    typeof navigator !== "undefined" &&
    typeof window !== "undefined" &&
    Boolean(navigator.mediaDevices?.getUserMedia) &&
    "MediaRecorder" in window;

  const [conversations, setConversations] = useState<ConversationOut[]>([]);
  const [convLoading, setConvLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [mobileListOpen, setMobileListOpen] = useState(false);

  const [messages, setMessages] = useState<MessageOut[]>([]);
  const [messagesLoading, setMessagesLoading] = useState(false);

  const [input, setInput] = useState("");
  const [attachments, setAttachments] = useState<ChatAttachmentDraft[]>([]);
  const [attachmentError, setAttachmentError] = useState<string | null>(null);
  const uploadControllersRef = useRef(new Map<string, AbortController>());
  const [sending, setSending] = useState(false);
  const [streamingText, setStreamingText] = useState("");
  const [toolEvents, setToolEvents] = useState<ToolEvent[]>([]);
  const [pendingConfirmation, setPendingConfirmation] = useState<PendingConfirmationOut | null>(null);
  const retryTurnRef = useRef<{ fingerprint: string; idempotencyKey: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [voiceId, setVoiceId] = useState<string | null>(null);
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const [alwaysListenOpen, setAlwaysListenOpen] = useState(false);
  // Audio de un comando ya capturado por el wake word nativo (Tauri), en
  // espera de que `AlwaysListenMode` lo consuma -- ver el `useEffect` de
  // `edecan://wake-detected` más abajo.
  const [pendingNativeAudio, setPendingNativeAudio] = useState<Blob | null>(null);
  // Copia en ref de `activeId`: el handler del evento nativo se suscribe una
  // sola vez al montar y necesita el valor MÁS RECIENTE en el momento en que
  // llega el evento (no el que tenía cuando se suscribió).
  const activeIdRef = useRef<string | null>(null);
  activeIdRef.current = activeId;

  const [speakingId, setSpeakingId] = useState<string | null>(null);
  const [speakingState, setSpeakingState] = useState<SpeakState>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  // Incrementado cada vez que arranca/para una reproducción -- los callbacks
  // async de `playSentencesSequentially` lo comparan contra su propia copia
  // para saber si los superó una reproducción más nueva (o un stop) y deben
  // abandonar en silencio, sin pisar el estado de la reproducción vigente.
  const speakSessionRef = useRef(0);

  const scrollRef = useRef<HTMLDivElement | null>(null);
  const initializedRef = useRef(false);

  useEffect(() => {
    if (initializedRef.current) return;
    initializedRef.current = true;
    // La primera vez Edecan queda listo para recibir una frase sin obligar a
    // entender ni crear manualmente el concepto de "conversación".
    void loadConversations(undefined, false, true);
    getPersona()
      .then((p) => setVoiceId(p.voice_id))
      .catch(() => undefined);
  }, []);

  useEffect(
    () => () => {
      for (const controller of uploadControllersRef.current.values()) controller.abort();
      uploadControllersRef.current.clear();
    },
    [],
  );

  // Enganche del wake word NATIVO (app de escritorio, Tauri): se suscribe de
  // forma INCONDICIONAL -- no solo cuando el usuario ya abrió el overlay a
  // mano con el botón "Escuchar siempre" -- porque el punto de esto es
  // justamente no depender de ese click. Rust ya trajo la ventana al frente
  // y ya grabó (con corte por silencio) el comando que siguió a la wake
  // word antes de emitir el evento, así que acá solo hace falta abrir (o
  // reusar) el overlay y entregarle ese audio ya listo para
  // transcribir+enviar -- sin pedirle permiso de micrófono al navegador de
  // nuevo ni hacerlo esperar la palabra clave otra vez.
  useEffect(() => {
    if (!isTauriApp()) return;
    let cancelled = false;
    let unlisten: (() => void) | null = null;

    tauriListenEvent<WakeDetectedPayload>("edecan://wake-detected", (payload) => {
      const blob = base64ToBlob(payload.audio_base64, payload.mime);
      void (async () => {
        if (!activeIdRef.current) {
          // Sin conversación activa (p. ej. recién instalado) no hay a dónde
          // mandar el comando -- se crea una, igual que hace el botón
          // "Nueva conversación", para no perder el turno que el usuario ya
          // dijo en voz alta.
          try {
            const conv = await createConversation();
            setConversations((prev) => [conv, ...prev]);
            setActiveId(conv.id);
          } catch (err) {
            setError(
              err instanceof Error ? err.message : "No se pudo crear una conversación para el comando de voz.",
            );
            return;
          }
        }
        setPendingNativeAudio(blob);
        setAlwaysListenOpen(true);
      })();
    }).then((fn) => {
      if (cancelled) fn();
      else unlisten = fn;
    });

    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, []);

  useEffect(() => {
    if (!activeId) {
      setMessages([]);
      return;
    }
    let cancelled = false;
    setMessagesLoading(true);
    getConversation(activeId)
      .then((conv) => {
        if (!cancelled) {
          setMessages(conv.messages ?? []);
          setPendingConfirmation(conv.pending_confirmation ?? null);
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "No se pudo cargar la conversación.");
      })
      .finally(() => {
        if (!cancelled) setMessagesLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, streamingText, toolEvents, pendingConfirmation]);

  async function loadConversations(preferId?: string, silent = false, createIfEmpty = false) {
    if (!silent) setConvLoading(true);
    try {
      let list = await listConversations();
      if (createIfEmpty && list.length === 0) {
        const firstConversation = await createConversation();
        list = [firstConversation];
      }
      setConversations(list);
      setActiveId((current) => preferId ?? current ?? list[0]?.id ?? null);
    } catch (err) {
      // Un refresh silencioso (tras un turno) que falle no debe tapar el chat con un error.
      if (!silent) setError(err instanceof Error ? err.message : "No se pudieron cargar las conversaciones.");
    } finally {
      if (!silent) setConvLoading(false);
    }
  }

  function handleSelect(id: string) {
    if (id === activeId) {
      setMobileListOpen(false);
      return;
    }
    // Evita mezclar el stream SSE en curso (atado a la conversación anterior)
    // con la vista de la conversación recién seleccionada.
    if (sending) return;
    setActiveId(id);
    setStreamingText("");
    setToolEvents([]);
    setPendingConfirmation(null);
    setMobileListOpen(false);
  }

  async function handleCreate() {
    if (sending) return;
    setCreating(true);
    setError(null);
    try {
      const conv = await createConversation();
      setConversations((prev) => [conv, ...prev]);
      setActiveId(conv.id);
      setMobileListOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo crear la conversación.");
    } finally {
      setCreating(false);
    }
  }

  async function handleDelete(id: string) {
    try {
      await deleteConversation(id);
      const remaining = conversations.filter((c) => c.id !== id);
      setConversations(remaining);
      if (activeId === id) {
        setActiveId(remaining[0]?.id ?? null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo borrar la conversación.");
    }
  }

  async function runStream(action: () => Promise<void>): Promise<boolean> {
    setError(null);
    setSending(true);
    setStreamingText("");
    setToolEvents([]);
    try {
      await action();
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error de conexión con el asistente.");
      setStreamingText("");
      setToolEvents([]);
      return false;
    } finally {
      setSending(false);
      // Refresco silencioso: solo para reordenar/actualizar título en la lista,
      // sin tapar la conversación activa con el spinner de carga.
      void loadConversations(activeId ?? undefined, true);
    }
  }

  function makeEventHandler(assistantMessageId?: string) {
    let text = "";
    let events: ToolEvent[] = [];
    const toolLog: AgentEvent[] = [];
    return (event: AgentEvent) => {
      switch (event.type) {
        case "text_delta":
          text += event.text;
          setStreamingText(text);
          break;
        case "tool_start": {
          toolLog.push(event);
          events = reduceToolTimeline(events, event);
          setToolEvents([...events]);
          break;
        }
        case "tool_progress": {
          events = reduceToolTimeline(events, event);
          setToolEvents([...events]);
          break;
        }
        case "tool_end": {
          toolLog.push(event);
          events = reduceToolTimeline(events, event);
          setToolEvents([...events]);
          break;
        }
        case "confirmation_required":
          setPendingConfirmation({ tool_call_id: event.tool_call_id, name: event.name, args: event.args });
          break;
        case "done":
          if (text || toolLog.length > 0) {
            setMessages((prev) => [
              ...prev,
              {
                id: assistantMessageId ?? `local-${Date.now()}`,
                role: "assistant",
                content: { text },
                tool_calls: toolLog.length > 0 ? toolLog : null,
                tokens_in: event.usage.input_tokens ?? 0,
                tokens_out: event.usage.output_tokens ?? 0,
                created_at: new Date().toISOString(),
              },
            ]);
          }
          setStreamingText("");
          setToolEvents([]);
          break;
        case "error":
          setError(event.message);
          break;
      }
    };
  }

  /** Compartida entre el composer normal (`handleSend`, lee `input`) y el
   * modo "Escuchar siempre" (`AlwaysListenMode`, manda el texto ya
   * transcrito directo) -- mismo turno, misma validación, dos orígenes de
   * texto distintos. */
  async function sendText(
    text: string,
    outgoingAttachments: ChatAttachmentDraft[] = [],
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<boolean> {
    if ((!text.trim() && outgoingAttachments.length === 0) || !activeId || sending) return false;
    const localId = `local-${Date.now()}`;
    setMessages((prev) => [
      ...prev,
      {
        id: localId,
        role: "user",
        content: {
          text,
          attachments: outgoingAttachments.flatMap((attachment) =>
            attachment.fileId
              ? [{ file_id: attachment.fileId, filename: attachment.filename, mime: attachment.mime }]
              : [],
          ),
        },
        tool_calls: null,
        tokens_in: 0,
        tokens_out: 0,
        created_at: new Date().toISOString(),
      },
    ]);
    const assistantMessageId = `local-assistant-${localId}`;
    const succeeded = await runStream(() =>
      sendMessageStream(
        activeId,
        text,
        makeEventHandler(assistantMessageId),
        undefined,
        outgoingAttachments.flatMap((attachment) => (attachment.fileId ? [attachment.fileId] : [])),
        idempotencyKey,
      ),
    );
    if (!succeeded) {
      setMessages((prev) =>
        prev.filter((message) => message.id !== localId && message.id !== assistantMessageId),
      );
    }
    return succeeded;
  }

  async function handleSend() {
    if (!canSubmitChat(input, attachments, sending)) return;
    const text = input;
    const outgoingAttachments = attachments.filter(
      (attachment) => attachment.status === "ready" && attachment.fileId,
    );
    const fingerprint = JSON.stringify({
      conversationId: activeId,
      text,
      attachments: outgoingAttachments.map((attachment) => attachment.fileId),
    });
    const idempotencyKey =
      retryTurnRef.current?.fingerprint === fingerprint
        ? retryTurnRef.current.idempotencyKey
        : crypto.randomUUID();
    const sentLocalIds = new Set(outgoingAttachments.map((attachment) => attachment.localId));
    setInput("");
    setAttachments((current) => current.filter((attachment) => !sentLocalIds.has(attachment.localId)));
    setAttachmentError(null);
    const succeeded = await sendText(text, outgoingAttachments, idempotencyKey);
    if (succeeded) {
      retryTurnRef.current = null;
      return;
    }
    retryTurnRef.current = { fingerprint, idempotencyKey };
    // El mensaje o la subida nunca deben desaparecer ante un fallo de red.
    setInput(text);
    setAttachments((current) => [...outgoingAttachments, ...current].slice(0, MAX_CHAT_ATTACHMENTS));
  }

  function handleSelectFiles(files: File[]) {
    const availableSlots = Math.max(0, MAX_CHAT_ATTACHMENTS - attachments.length);
    const selected = files.slice(0, availableSlots);
    setAttachmentError(
      files.length > availableSlots
        ? `Puedes adjuntar como máximo ${MAX_CHAT_ATTACHMENTS} archivos por mensaje.`
        : null,
    );
    if (selected.length === 0) return;

    const drafts = selected.map<ChatAttachmentDraft>((file) => ({
      localId: attachmentLocalId(),
      filename: file.name || "archivo",
      sizeBytes: file.size,
      status: "uploading",
      fileId: null,
      mime: file.type || null,
      error: null,
    }));
    setAttachments((current) => [...current, ...drafts].slice(0, MAX_CHAT_ATTACHMENTS));

    selected.forEach((file, index) => {
      const draft = drafts[index];
      const controller = new AbortController();
      uploadControllersRef.current.set(draft.localId, controller);
      uploadFile(file, controller.signal)
        .then((uploaded) => {
          setAttachments((current) =>
            current.map((attachment) =>
              attachment.localId === draft.localId
                ? {
                    ...attachment,
                    status: "ready",
                    fileId: uploaded.id,
                    filename: uploaded.filename,
                    mime: uploaded.mime,
                    error: null,
                  }
                : attachment,
            ),
          );
        })
        .catch((reason: unknown) => {
          if (controller.signal.aborted) return;
          const message = reason instanceof Error ? reason.message : "No se pudo subir el archivo.";
          setAttachments((current) =>
            current.map((attachment) =>
              attachment.localId === draft.localId
                ? { ...attachment, status: "error", error: message }
                : attachment,
            ),
          );
        })
        .finally(() => uploadControllersRef.current.delete(draft.localId));
    });
  }

  function handleRemoveAttachment(localId: string) {
    uploadControllersRef.current.get(localId)?.abort();
    uploadControllersRef.current.delete(localId);
    setAttachments((current) => current.filter((attachment) => attachment.localId !== localId));
    setAttachmentError(null);
  }

  async function handleConfirm(approved: boolean) {
    if (!pendingConfirmation || !activeId) return;
    const { tool_call_id } = pendingConfirmation;
    const conversationId = activeId;
    const succeeded = await runStream(() =>
      confirmToolCallStream(conversationId, tool_call_id, approved, makeEventHandler()),
    );
    if (succeeded) {
      setPendingConfirmation(null);
      return;
    }
    // La confirmación puede haberse consumido justo antes de un corte. El
    // servidor es la fuente de verdad y solo expone el resumen público seguro.
    try {
      const conversation = await getConversation(conversationId);
      setMessages(conversation.messages ?? []);
      setPendingConfirmation(conversation.pending_confirmation ?? null);
    } catch {
      // Conserva la tarjeta actual: es más seguro volver a preguntar que
      // esconder una acción cuya resolución todavía es ambigua.
    }
  }

  async function toggleRecording() {
    if (recording) {
      mediaRecorderRef.current?.stop();
      return;
    }
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      recorder.onstop = () => {
        stream.getTracks().forEach((track) => track.stop());
        setRecording(false);
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || "audio/webm" });
        if (blob.size === 0) return;
        setTranscribing(true);
        transcribeAudio(blob)
          .then(({ text }) => setInput((prev) => (prev ? `${prev} ${text}` : text)))
          .catch((err) => setError(err instanceof Error ? err.message : "No se pudo transcribir el audio."))
          .finally(() => setTranscribing(false));
      };
      mediaRecorderRef.current = recorder;
      recorder.start();
      setRecording(true);
    } catch {
      setError("No se pudo acceder al micrófono. Revisa los permisos del navegador.");
    }
  }

  function stopSpeaking() {
    speakSessionRef.current += 1; // invalida cualquier callback en vuelo
    audioRef.current?.pause();
    audioRef.current = null;
    setSpeakingId(null);
    setSpeakingState(null);
  }

  async function toggleSpeak(message: MessageOut) {
    const text = messageText(message.content);
    if (!text) return;
    if (speakingId === message.id && speakingState !== null) {
      stopSpeaking();
      return;
    }
    stopSpeaking();
    const sentences = splitIntoSentences(text);
    if (sentences.length === 0) return;

    speakSessionRef.current += 1;
    const session = speakSessionRef.current;
    setSpeakingId(message.id);
    setSpeakingState("loading");
    setError(null);
    await playSentencesSequentially(sentences, session, message.id);
  }

  /** Sintetiza y reproduce oración por oración en vez de esperar el audio
   * completo antes de empezar: la síntesis de la SIGUIENTE oración arranca
   * en paralelo mientras suena la actual, así que después del primer
   * fragmento (normalmente corto) ya no hay más espera perceptible entre
   * oraciones -- esto es lo que hace que "empiece a hablar de una vez" en
   * vez de esperar el audio del mensaje completo. */
  async function playSentencesSequentially(
    sentences: string[],
    session: number,
    messageId: string,
  ) {
    let nextFetch: Promise<Blob> | null = speakText(sentences[0], voiceId);
    for (let i = 0; i < sentences.length; i++) {
      if (nextFetch === null) return; // no debería pasar: solo es null tras la última oración
      let blob: Blob;
      try {
        blob = await nextFetch;
      } catch (err) {
        if (speakSessionRef.current === session) {
          setError(err instanceof Error ? err.message : "No se pudo generar el audio.");
          setSpeakingId(null);
          setSpeakingState(null);
        }
        return;
      }
      if (speakSessionRef.current !== session) return; // otra reproducción/stop nos superó

      nextFetch = i + 1 < sentences.length ? speakText(sentences[i + 1], voiceId) : null;

      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audioRef.current = audio;
      setSpeakingState("playing");
      await new Promise<void>((resolve) => {
        audio.onended = () => {
          URL.revokeObjectURL(url);
          resolve();
        };
        audio.onerror = () => {
          URL.revokeObjectURL(url);
          resolve();
        };
        audio.play().catch(() => resolve());
      });
      if (speakSessionRef.current !== session) return;
    }
    if (speakSessionRef.current === session) {
      setSpeakingId((current) => (current === messageId ? null : current));
      setSpeakingState((current) => (current === "playing" ? null : current));
    }
  }

  const turnBlocked = sending || pendingConfirmation !== null;
  const showStreamingBubble = sending && (streamingText.length > 0 || toolEvents.length === 0);

  return (
    <div className="flex h-[calc(100vh-7.5rem)] min-h-[28rem] gap-4">
      <div className="hidden w-64 shrink-0 rounded-2xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900 md:block">
        <ConversationList
          conversations={conversations}
          activeId={activeId}
          loading={convLoading}
          creating={creating}
          onSelect={handleSelect}
          onCreate={handleCreate}
          onDelete={handleDelete}
        />
      </div>

      {mobileListOpen && (
        <div className="fixed inset-0 z-40 md:hidden">
          <button
            aria-label="Cerrar"
            className="absolute inset-0 bg-slate-900/50"
            onClick={() => setMobileListOpen(false)}
          />
          <div className="absolute inset-y-0 left-0 w-72 bg-white shadow-xl dark:bg-slate-900">
            <ConversationList
              conversations={conversations}
              activeId={activeId}
              loading={convLoading}
              creating={creating}
              onSelect={handleSelect}
              onCreate={handleCreate}
              onDelete={handleDelete}
            />
          </div>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
        <div className="flex items-center gap-2 border-b border-slate-100 px-4 py-2.5 dark:border-slate-800 md:hidden">
          <button
            onClick={() => setMobileListOpen(true)}
            className="rounded-md p-1.5 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
            aria-label="Conversaciones"
          >
            <MenuIcon className="h-4 w-4" />
          </button>
          <span className="truncate text-sm font-medium text-slate-700 dark:text-slate-200">
            {conversations.find((c) => c.id === activeId)?.title || "Conversación"}
          </span>
        </div>

        {error && (
          <div className="px-4 pt-3">
            <Alert variant="error">{error}</Alert>
          </div>
        )}

        {!activeId && !convLoading ? (
          <div className="flex flex-1 items-center justify-center p-6">
            <EmptyState
              title="Aún no tienes conversaciones"
              description='Pulsa "Nueva conversación" para empezar a hablar con tu asistente.'
              action={
                <Button onClick={handleCreate} loading={creating}>
                  Nueva conversación
                </Button>
              }
            />
          </div>
        ) : (
          <>
            <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto p-4 thin-scrollbar">
              {messagesLoading ? (
                <div className="flex justify-center py-8">
                  <Spinner className="h-5 w-5 text-slate-400" />
                </div>
              ) : (
                <>
                  {messages.length === 0 && !sending && (
                    <div className="mx-auto flex max-w-xl flex-col items-center px-4 py-10 text-center">
                      <p className="text-lg font-semibold text-slate-800 dark:text-slate-100">¿Qué necesitas?</p>
                      <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
                        Escríbelo como se lo dirías a una persona. Edecan decidirá cómo hacerlo.
                      </p>
                      <div className="mt-5 flex flex-wrap justify-center gap-2">
                        {STARTER_REQUESTS.map((request) => (
                          <button
                            key={request}
                            type="button"
                            onClick={() => setInput(request)}
                            className="rounded-full border border-slate-200 bg-white px-3 py-2 text-xs text-slate-600 transition-colors hover:border-brand-300 hover:text-brand-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
                          >
                            {request}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                  {messages.map((m) => (
                    <MessageBubble
                      key={m.id}
                      message={m}
                      canSpeak={canVoice}
                      speaking={speakingId === m.id ? speakingState : null}
                      onToggleSpeak={() => toggleSpeak(m)}
                      onPrefillMessage={setInput}
                    />
                  ))}
                  {showStreamingBubble && (
                    <MessageBubble
                      message={{
                        id: "streaming",
                        role: "assistant",
                        content: { text: streamingText },
                        tool_calls: null,
                        tokens_in: 0,
                        tokens_out: 0,
                        created_at: new Date().toISOString(),
                      }}
                    />
                  )}
                  {toolEvents.length > 0 && <ToolTimeline events={toolEvents} />}
                  {pendingConfirmation && (
                    <ConfirmationCard
                      name={pendingConfirmation.name}
                      args={pendingConfirmation.args}
                      onApprove={() => handleConfirm(true)}
                      onDeny={() => handleConfirm(false)}
                      loading={sending}
                    />
                  )}
                </>
              )}
            </div>
            {canRecordAudio && activeId && (
              <div className="mb-2 flex justify-end">
                <Button
                  type="button"
                  size="sm"
                  variant="secondary"
                  onClick={() => setAlwaysListenOpen(true)}
                >
                  Escuchar siempre
                </Button>
              </div>
            )}
            <ChatComposer
              value={input}
              onChange={setInput}
              onSend={handleSend}
              sending={turnBlocked}
              canVoice={canRecordAudio}
              voiceFlagEnabled={canVoice}
              recording={recording}
              transcribing={transcribing}
              onToggleRecording={toggleRecording}
              attachments={attachments}
              attachmentError={attachmentError}
              onSelectFiles={handleSelectFiles}
              onRemoveAttachment={handleRemoveAttachment}
            />
          </>
        )}
      </div>
      {alwaysListenOpen && activeId && (
        <AlwaysListenMode
          onClose={() => {
            setAlwaysListenOpen(false);
            setPendingNativeAudio(null);
          }}
          onSendText={sendText}
          messages={messages}
          sending={sending}
          pendingConfirmation={pendingConfirmation}
          onConfirm={handleConfirm}
          voiceId={voiceId}
          pendingNativeAudio={pendingNativeAudio}
          onNativeAudioConsumed={() => setPendingNativeAudio(null)}
        />
      )}
    </div>
  );
}
