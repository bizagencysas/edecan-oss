"use client";

import { useEffect, useRef, useState } from "react";

import { AlwaysListenMode } from "@/components/chat/AlwaysListenMode";
import { ChatComposer } from "@/components/chat/ChatComposer";
import { ChatHome } from "@/components/chat/ChatHome";
import { ConfirmationCard } from "@/components/chat/ConfirmationCard";
import { ConversationList } from "@/components/chat/ConversationList";
import { MessageBubble } from "@/components/chat/MessageBubble";
import {
  ModelSelector,
  etiquetaSeleccion,
  modeloConVisionPorDefecto,
} from "@/components/chat/ModelSelector";
import { ToolTimeline, type ToolEvent } from "@/components/chat/ToolTimeline";
import { messageText } from "@/components/chat/utils";
import { ChatIcon, MenuIcon, MicIcon, PlusIcon, UndoIcon } from "@/components/icons";
import { Alert, Button, EmptyState, Spinner } from "@/components/ui";
import {
  confirmToolCallStream,
  branchConversation,
  clearConversationContext,
  createConversation,
  deleteConversation,
  rewindConversation,
  setMessageFlags,
  getChatModels,
  getConversation,
  getPersona,
  listConversations,
  renameConversation,
  sendMessageStream,
  submitFeedback,
  setConversationModel,
  API_BASE_URL,
  speakTextStream,
  transcribeAudio,
  undoLastAction,
  uploadFile,
} from "@/lib/api";
import { createMission, getMission, steerMission, type Mission } from "@/lib/api-misiones";
import { useAuth } from "@/lib/auth-context";
import {
  IncrementalTtsPlayer,
  RealtimeVoiceClient,
  normalizeRealtimeAudioMime,
  preferredRecordingMime,
  realtimeVoiceUrl,
} from "@/lib/realtime-voice";
import { splitIntoSentences, startLiveTranscript } from "@/lib/speech";
import { getAccessToken, isDesktopMiniWindow } from "@/lib/tokens";
import { SPEECH_RECOGNITION_LOCALE } from "@/lib/wake-word-detection";
import { canSubmitChat, MAX_CHAT_ATTACHMENTS, turnoTraeImagen } from "@/lib/chat-attachments";
import { reduceToolTimeline } from "@/lib/chat-blocks";
import { redactChatSecrets } from "@/lib/chat-secret-redaction";
import {
  ASSISTANT_INTENT_EVENT,
  assistantPromptForIntent,
  assistantPromptFromSearch,
} from "@/lib/assistant-intents";
import { isTauriApp, tauriInvoke, tauriListenEvent } from "@/lib/tauriListen";
import {
  FLAG_VOICE_WEB,
  type AgentEvent,
  type ChatAttachmentDraft,
  type ChatModelCatalog,
  type ConversationOut,
  type MessageOut,
  type PendingConfirmationOut,
} from "@/lib/types";

type SpeakState = "loading" | "playing" | null;

const ATTACHMENT_UPLOAD_TIMEOUT_MS = 90_000;
const CONVERSATION_PANEL_KEY = "edecan:conversation-panel-collapsed";

/** Payload de `edecan://wake-detected` (evento nativo, Tauri): el listener
 * en segundo plano ya detectó la wake word entrenada Y ya grabó -- con corte
 * por silencio -- el comando de voz que siguió, ver
 * `AlwaysListenMode.pendingNativeAudio`. */
interface WakeDetectedPayload {
  audio_base64: string;
  mime: string;
}

interface NavigatePayload {
  path?: string;
}

interface DeeplinkPayload {
  url?: string;
}

interface AskWithContextPayload {
  text?: string;
  error?: string;
}

function assignAppPath(path: string) {
  if (!path.startsWith("/app")) return;
  const current = window.location.pathname.replace(/\/+$/, "") || "/";
  const next = path.replace(/\/+$/, "") || "/";
  if (current === next) return;
  window.location.assign(path);
}

function pathFromDeeplink(url: string): string | null {
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "edecan:") return null;
    const pathname = parsed.pathname || "";
    if (pathname.startsWith("/app")) return `${pathname}${parsed.search}`;
    const combined = `${parsed.hostname || ""}${pathname}`.replace(/^\/+/, "");
    if (combined.startsWith("app/")) return `/${combined}${parsed.search}`;
    if (combined) return `/app/${combined}${parsed.search}`;
    return null;
  } catch {
    return null;
  }
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

function MiniAskPage() {
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const conversationIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (!isTauriApp()) return;
    let cancelled = false;
    let unlisten: (() => void) | null = null;
    tauriListenEvent<AskWithContextPayload>("edecan://ask-with-context", (payload) => {
      if (payload?.error && !payload.text) {
        setError(payload.error);
        return;
      }
      const text = payload?.text?.trim();
      if (text) setInput((current) => (current.trim() ? `${current.trim()}\n\n${text}` : text));
    }).then((fn) => {
      if (cancelled) fn();
      else unlisten = fn;
    });
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, []);

  async function ensureConversation(): Promise<string> {
    if (conversationIdRef.current) return conversationIdRef.current;
    const list = await listConversations();
    const conversation = list[0] ?? (await createConversation());
    conversationIdRef.current = conversation.id;
    return conversation.id;
  }

  async function handleSend() {
    const text = input.trim();
    if (!text || sending) return;
    setSending(true);
    setError(null);
    setStatus(null);
    try {
      const conversationId = await ensureConversation();
      let reply = "";
      await sendMessageStream(conversationId, text, (event) => {
        if (event.type === "text_delta") reply += event.text;
        if (event.type === "error") setError(event.message);
      });
      setInput("");
      if (reply.trim()) setStatus(reply.trim().slice(0, 280));
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo enviar.");
    } finally {
      setSending(false);
    }
  }

  async function handleCapture() {
    setError(null);
    try {
      await tauriInvoke<string>("capture_clipboard_context");
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo leer el portapapeles.");
    }
  }

  return (
    <div className="flex h-dvh min-h-0 flex-col gap-2 bg-white p-3 dark:bg-slate-900">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-semibold text-slate-700 dark:text-slate-200">Preguntar rápido</p>
        {isTauriApp() && (
          <Button type="button" size="sm" variant="ghost" onClick={() => void handleCapture()}>
            Capturar ventana
          </Button>
        )}
      </div>
      <textarea
        className="min-h-0 flex-1 resize-none rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 placeholder:text-slate-400 focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/30 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
        value={input}
        onChange={(event) => setInput(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            void handleSend();
          }
        }}
        placeholder="Pregúntale a Edecán…"
        disabled={sending}
      />
      {error && <p className="text-[11px] text-red-600">{error}</p>}
      {status && !error && <p className="line-clamp-2 text-[11px] text-slate-500">{status}</p>}
      <Button type="button" size="sm" onClick={() => void handleSend()} loading={sending} disabled={!input.trim()}>
        Enviar
      </Button>
    </div>
  );
}

/** Chat principal (ARCHITECTURE.md §9, §10.7): conversaciones + streaming SSE + voz. */
function ChatPage() {
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
  const [historyCollapsed, setHistoryCollapsed] = useState(false);

  const [messages, setMessages] = useState<MessageOut[]>([]);
  const [messagesLoading, setMessagesLoading] = useState(false);

  // Selector de modelos: el catálogo se pide UNA vez (es configuración de la
  // instalación, igual para todos), y la selección es de la conversación
  // activa — `null` en las dos = automático.
  const [modelCatalog, setModelCatalog] = useState<ChatModelCatalog | null>(null);
  const [chatModel, setChatModel] = useState<string | null>(null);
  const [chatEffort, setChatEffort] = useState<string | null>(null);
  const [modelSheetOpen, setModelSheetOpen] = useState(false);
  const [savingModel, setSavingModel] = useState(false);

  const [input, setInput] = useState("");
  const [attachments, setAttachments] = useState<ChatAttachmentDraft[]>([]);
  const [attachmentError, setAttachmentError] = useState<string | null>(null);
  const uploadControllersRef = useRef(new Map<string, AbortController>());
  const uploadFilesRef = useRef(new Map<string, File>());
  const [sending, setSending] = useState(false);
  const [streamingText, setStreamingText] = useState("");
  const [toolEvents, setToolEvents] = useState<ToolEvent[]>([]);
  const [pendingConfirmation, setPendingConfirmation] = useState<PendingConfirmationOut | null>(null);
  const retryTurnRef = useRef<{ fingerprint: string; idempotencyKey: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [voiceId, setVoiceId] = useState<string | null>("0uHpKhb0ymsdvmCtPV8y");
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [liveTranscript, setLiveTranscript] = useState("");
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamedVoiceFramesRef = useRef(false);
  const stopLiveTranscriptRef = useRef<(() => void) | null>(null);
  const voiceClientRef = useRef<RealtimeVoiceClient | null>(null);
  const ttsPlayerRef = useRef<IncrementalTtsPlayer | null>(null);
  const unsubVoiceRef = useRef<(() => void) | null>(null);
  const [alwaysListenOpen, setAlwaysListenOpen] = useState(false);
  const [workMode, setWorkMode] = useState(false);
  const [liveMission, setLiveMission] = useState<Mission | null>(null);
  const [steerText, setSteerText] = useState("");
  const [steerBusy, setSteerBusy] = useState(false);
  const [replyTo, setReplyTo] = useState<{ id: string; text: string } | null>(null);
  const [undoBusy, setUndoBusy] = useState(false);
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
  // Incrementado cada vez que arranca/para una reproducción -- los callbacks
  // async de `playSentencesSequentially` lo comparan contra su propia copia
  // para saber si los superó una reproducción más nueva (o un stop) y deben
  // abandonar en silencio, sin pisar el estado de la reproducción vigente.
  const speakSessionRef = useRef(0);
  const speakAbortRef = useRef<AbortController | null>(null);

  const scrollRef = useRef<HTMLDivElement | null>(null);
  const initializedRef = useRef(false);

  useEffect(() => {
    if (initializedRef.current) return;
    initializedRef.current = true;
    const assistantPrompt = assistantPromptFromSearch(window.location.search);
    if (assistantPrompt) {
      setInput(assistantPrompt);
      const cleanUrl = new URL(window.location.href);
      cleanUrl.searchParams.delete("intent");
      window.history.replaceState(window.history.state, "", cleanUrl);
    }
    // La primera vez Edecan queda listo para recibir una frase sin obligar a
    // entender ni crear manualmente el concepto de "conversación".
    void loadConversations(undefined, false, true);
    getPersona()
      .then((p) => setVoiceId(p.voice_id))
      .catch(() => undefined);
    // Best-effort: si el catálogo no carga, la pastilla no se pinta y el chat
    // sigue funcionando en automático. Nunca un error de aquí tapa el chat.
    getChatModels()
      .then(setModelCatalog)
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!liveMission || ["done", "error", "cancelled"].includes(liveMission.status)) return;
    const timer = window.setInterval(() => {
      void getMission(liveMission.id)
        .then((detail) => setLiveMission(detail.mission))
        .catch(() => undefined);
    }, 4000);
    return () => window.clearInterval(timer);
  }, [liveMission]);

  useEffect(() => {
    function prefillAssistantIntent(event: Event) {
      const prompt = assistantPromptForIntent((event as CustomEvent<unknown>).detail);
      if (prompt) setInput(prompt);
    }

    window.addEventListener(ASSISTANT_INTENT_EVENT, prefillAssistantIntent);
    return () => window.removeEventListener(ASSISTANT_INTENT_EVENT, prefillAssistantIntent);
  }, []);

  useEffect(() => {
    try {
      setHistoryCollapsed(window.localStorage.getItem(CONVERSATION_PANEL_KEY) === "true");
    } catch {
      // El panel sigue siendo plegable durante la sesión aunque storage esté bloqueado.
    }
  }, []);

  useEffect(
    () => () => {
      for (const controller of uploadControllersRef.current.values()) controller.abort();
      uploadControllersRef.current.clear();
      uploadFilesRef.current.clear();
      stopLiveTranscriptRef.current?.();
      unsubVoiceRef.current?.();
      ttsPlayerRef.current?.stop();
      voiceClientRef.current?.close();
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
    if (!isTauriApp()) return;
    let cancelled = false;
    const unlistens: Array<() => void> = [];

    tauriListenEvent<NavigatePayload>("edecan://navigate", (payload) => {
      const path = payload?.path;
      if (typeof path === "string") assignAppPath(path);
    }).then((fn) => {
      if (cancelled) fn();
      else unlistens.push(fn);
    });

    tauriListenEvent<DeeplinkPayload>("edecan://deeplink", (payload) => {
      const url = payload?.url;
      if (typeof url !== "string") return;
      const path = pathFromDeeplink(url);
      if (path) assignAppPath(path);
    }).then((fn) => {
      if (cancelled) fn();
      else unlistens.push(fn);
    });

    tauriListenEvent<AskWithContextPayload>("edecan://ask-with-context", (payload) => {
      const text = payload?.text?.trim();
      if (text) setInput((current) => (current.trim() ? `${current.trim()}\n\n${text}` : text));
    }).then((fn) => {
      if (cancelled) fn();
      else unlistens.push(fn);
    });

    return () => {
      cancelled = true;
      for (const unlisten of unlistens) unlisten();
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
          // El backend es la autoridad de la selección: al abrir una
          // conversación se adopta la suya, nunca la de la anterior.
          setChatModel(conv.model ?? null);
          setChatEffort(conv.effort ?? null);
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
    setLiveMission(null);
    setMobileListOpen(false);
    // Adelanto de la selección con lo que ya trae la lista, para que la
    // pastilla no muestre el modelo de la conversación anterior durante el
    // viaje del GET (que después manda la palabra final).
    const elegida = conversations.find((conversation) => conversation.id === id);
    setChatModel(elegida?.model ?? null);
    setChatEffort(elegida?.effort ?? null);
  }

  async function handleCreate() {
    if (sending) return;
    setCreating(true);
    setError(null);
    try {
      const conv = await createConversation();
      setConversations((prev) => [conv, ...prev]);
      setActiveId(conv.id);
      setChatModel(conv.model ?? null);
      setChatEffort(conv.effort ?? null);
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

  async function handleRename(id: string, title: string) {
    try {
      const updated = await renameConversation(id, title);
      setConversations((current) =>
        current.map((conversation) => (conversation.id === id ? updated : conversation)),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo renombrar la conversación.");
      throw err;
    }
  }

  /**
   * Persiste modelo + esfuerzo de la conversación activa. Optimista para que
   * la pastilla cambie al instante, con rollback si el `PUT` falla: mentirle al
   * dueño sobre qué modelo está corriendo es peor que no dejarlo cambiarlo.
   * La respuesta del backend gana sobre el optimista (puede normalizar valores).
   */
  async function handleSelectModel(model: string | null, effort: string | null) {
    const conversationId = activeId;
    if (!conversationId) return;
    const modeloPrevio = chatModel;
    const esfuerzoPrevio = chatEffort;
    setChatModel(model);
    setChatEffort(effort);
    setSavingModel(true);
    try {
      const guardado = await setConversationModel(conversationId, model, effort);
      // Si mientras viajaba el PUT se cambió de conversación, esta respuesta ya
      // no habla de lo que está en pantalla.
      if (activeIdRef.current !== conversationId) return;
      setChatModel(guardado.model);
      setChatEffort(guardado.effort);
      setConversations((current) =>
        current.map((conversation) =>
          conversation.id === conversationId
            ? { ...conversation, model: guardado.model, effort: guardado.effort }
            : conversation,
        ),
      );
    } catch (err) {
      if (activeIdRef.current !== conversationId) return;
      setChatModel(modeloPrevio);
      setChatEffort(esfuerzoPrevio);
      setError(err instanceof Error ? err.message : "No se pudo cambiar el modelo.");
    } finally {
      setSavingModel(false);
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

  async function handleMessageFlags(message: MessageOut, flags: { pinned?: boolean; bookmark?: boolean }) {
    if (!activeId || message.id.startsWith("local-")) return;
    try {
      const updated = await setMessageFlags(activeId, message.id, flags);
      setMessages((prev) => prev.map((item) => (item.id === message.id ? updated : item)));
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo guardar el mensaje.");
    }
  }

  async function sendFeedback(
    messageId: string,
    kind: "thumb_up" | "thumb_down" | "correction",
    detail?: string,
  ): Promise<void> {
    if (!activeId) return;
    try {
      await submitFeedback({
        kind,
        conversation_id: activeId,
        message_id: messageId,
        detail,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo guardar el feedback.");
      throw err;
    }
  }

  function makeEventHandler(assistantMessageId?: string) {
    let text = "";
    let events: ToolEvent[] = [];
    const toolLog: AgentEvent[] = [];
    let explanation: string | null = null;
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
          explanation = event.explanation ?? null;
          if (text || toolLog.length > 0) {
            setMessages((prev) => [
              ...prev,
              {
                id: assistantMessageId ?? `local-${Date.now()}`,
                role: "assistant",
                content: { text, ...(explanation ? { explanation } : {}) },
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
    const quoted = replyTo?.text.trim()
      ? `> ${replyTo.text.trim().slice(0, 280)}\n\n${text}`
      : text;
    setReplyTo(null);
    const localId = `local-${Date.now()}`;
    setMessages((prev) => [
      ...prev,
      {
        id: localId,
        role: "user",
        content: {
          text: redactChatSecrets(quoted),
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
        quoted,
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

  async function handleWorkMission(text: string) {
    setSending(true);
    setError(null);
    try {
      const mission = await createMission(text.trim());
      setInput("");
      setLiveMission(mission);
      setMessages((prev) => [
        ...prev,
        {
          id: `local-mission-${mission.id}`,
          role: "assistant",
          content: {
            text: `Lanzé la misión «${mission.objetivo}». La sigo aquí abajo y también en Misiones.`,
          },
          tool_calls: null,
          tokens_in: 0,
          tokens_out: 0,
          created_at: new Date().toISOString(),
        },
      ]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo crear la misión.");
    } finally {
      setSending(false);
    }
  }

  async function handleSend() {
    if (!canSubmitChat(input, attachments, sending)) return;
    if (input.trim() === "/clear" && activeId) {
      setSending(true);
      setError(null);
      try {
        await clearConversationContext(activeId);
        setInput("");
        setMessages([]);
        setPendingConfirmation(null);
        setLiveMission(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : "No se pudo limpiar el contexto.");
      } finally {
        setSending(false);
      }
      return;
    }
    if (input.trim() === "/branch" && activeId) {
      setSending(true);
      setError(null);
      try {
        const branched = await branchConversation(activeId);
        setInput("");
        setConversations((prev) => [branched, ...prev]);
        setActiveId(branched.id);
        setChatModel(branched.model ?? null);
        setChatEffort(branched.effort ?? null);
      } catch (err) {
        setError(err instanceof Error ? err.message : "No se pudo ramificar el chat.");
      } finally {
        setSending(false);
      }
      return;
    }
    if (input.trim() === "/rewind" && activeId) {
      setSending(true);
      setError(null);
      try {
        const rewound = await rewindConversation(activeId);
        setInput("");
        setConversations((prev) => [rewound, ...prev]);
        setActiveId(rewound.id);
        setChatModel(rewound.model ?? null);
        setChatEffort(rewound.effort ?? null);
      } catch (err) {
        setError(err instanceof Error ? err.message : "No se pudo rebobinar el chat.");
      } finally {
        setSending(false);
      }
      return;
    }
    if (workMode) {
      await handleWorkMission(input);
      return;
    }
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
      uploadFilesRef.current.set(draft.localId, file);
      startAttachmentUpload(draft.localId, file);
    });
  }

  function startAttachmentUpload(localId: string, file: File) {
    uploadControllersRef.current.get(localId)?.abort();
    const controller = new AbortController();
    let timedOut = false;
    const timeoutId = window.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, ATTACHMENT_UPLOAD_TIMEOUT_MS);
    uploadControllersRef.current.set(localId, controller);
    uploadFile(file, controller.signal)
        .then((uploaded) => {
          setAttachments((current) =>
            current.map((attachment) =>
              attachment.localId === localId
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
          uploadFilesRef.current.delete(localId);
        })
        .catch((reason: unknown) => {
          if (controller.signal.aborted && !timedOut) return;
          const message = timedOut
            ? "La carga tardó demasiado. Reintenta; el archivo no se perdió."
            : reason instanceof Error
              ? reason.message
              : "No se pudo subir el archivo.";
          setAttachments((current) =>
            current.map((attachment) =>
              attachment.localId === localId
                ? { ...attachment, status: "error", error: message }
                : attachment,
            ),
          );
        })
        .finally(() => {
          window.clearTimeout(timeoutId);
          if (uploadControllersRef.current.get(localId) === controller) {
            uploadControllersRef.current.delete(localId);
          }
        });
  }

  function handleRetryAttachment(localId: string) {
    const file = uploadFilesRef.current.get(localId);
    if (!file) {
      setAttachmentError("Vuelve a elegir el archivo para reintentar la carga.");
      return;
    }
    setAttachmentError(null);
    setAttachments((current) =>
      current.map((attachment) =>
        attachment.localId === localId
          ? { ...attachment, status: "uploading", error: null }
          : attachment,
      ),
    );
    startAttachmentUpload(localId, file);
  }

  function handleRemoveAttachment(localId: string) {
    uploadControllersRef.current.get(localId)?.abort();
    uploadControllersRef.current.delete(localId);
    uploadFilesRef.current.delete(localId);
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

  async function handleRegenerate() {
    if (!activeId || sending) return;
    const lastUser = [...messages].reverse().find((message) => message.role === "user");
    if (!lastUser) return;
    const text = messageText(lastUser.content);
    if (!text.trim()) return;
    setMessages((prev) => (prev[prev.length - 1]?.role === "assistant" ? prev.slice(0, -1) : prev));
    const assistantMessageId = `local-assistant-regen-${Date.now()}`;
    await runStream(() =>
      sendMessageStream(activeId, text, makeEventHandler(assistantMessageId), undefined, [], crypto.randomUUID()),
    );
  }

  async function handleUndo() {
    if (undoBusy) return;
    setUndoBusy(true);
    setError(null);
    try {
      const result = await undoLastAction();
      const inverse = result.inverse_op ? JSON.stringify(result.inverse_op) : "";
      await sendText(
        inverse
          ? `Deshaz lo último (${result.tool_name}). Ejecuta esta operación inversa y confirma el resultado: ${inverse}`
          : `Deshaz lo último que hiciste con la herramienta ${result.tool_name}.`,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "No hay nada reciente que deshacer.");
    } finally {
      setUndoBusy(false);
    }
  }

  function getTtsPlayer(): IncrementalTtsPlayer {
    if (!ttsPlayerRef.current) ttsPlayerRef.current = new IncrementalTtsPlayer();
    return ttsPlayerRef.current;
  }

  async function ensureVoiceClient(): Promise<RealtimeVoiceClient> {
    const existing = voiceClientRef.current;
    if (existing?.isReady) return existing;
    existing?.close();
    const token = getAccessToken();
    if (!token) throw new Error("No hay sesión activa para voz realtime.");
    const client = new RealtimeVoiceClient({ url: realtimeVoiceUrl(API_BASE_URL), token });
    voiceClientRef.current = client;
    unsubVoiceRef.current?.();
    const player = getTtsPlayer();
    unsubVoiceRef.current = client.addEventListener((event) => {
      if (event.type === "audio" && event.audio) {
        player.append(event.audio, event.mime ?? "audio/mpeg");
        if (speakSessionRef.current > 0) setSpeakingState("playing");
      }
      if (event.type === "done") player.end();
      if (event.type === "interrupted") player.stop();
      if (event.type === "transcript" && event.text) {
        setLiveTranscript(event.text);
      }
    });
    await client.connect();
    return client;
  }

  async function transcribeViaRealtime(blob: Blob, alreadyStreamed = false): Promise<string> {
    const mime = normalizeRealtimeAudioMime(blob.type) ?? preferredRecordingMime();
    const client = await ensureVoiceClient();
    const abort = new AbortController();
    try {
      const pending = client.waitForEvent("transcript", 45_000, abort.signal);
      if (!alreadyStreamed) {
        if (blob.size === 0) return "";
        await client.sendAudio(blob, mime);
      }
      await client.commitAudio();
      return (await pending).text?.trim() ?? "";
    } catch (error) {
      abort.abort();
      if (blob.size === 0) throw error;
      const result = await transcribeAudio(blob);
      return result.text.trim();
    }
  }

  async function finishRecordedTurn(blob: Blob) {
    stopLiveTranscriptRef.current?.();
    stopLiveTranscriptRef.current = null;
    if (blob.size === 0 && !streamedVoiceFramesRef.current) {
      setLiveTranscript("");
      return;
    }
    setTranscribing(true);
    try {
      const text = await transcribeViaRealtime(blob, streamedVoiceFramesRef.current);
      if (text) {
        setLiveTranscript(text);
        setInput((prev) => (prev ? `${prev} ${text}` : text));
      } else {
        setLiveTranscript("");
      }
    } catch (err) {
      setLiveTranscript("");
      setError(err instanceof Error ? err.message : "No se pudo transcribir el audio.");
    } finally {
      streamedVoiceFramesRef.current = false;
      setTranscribing(false);
    }
  }

  async function toggleRecording() {
    if (speakingState) stopSpeaking();
    if (recording) {
      mediaRecorderRef.current?.stop();
      return;
    }
    setError(null);
    setLiveTranscript("");
    try {
      const client = await ensureVoiceClient().catch(() => null);
      const mime = preferredRecordingMime();
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = MediaRecorder.isTypeSupported(`${mime};codecs=opus`)
        ? new MediaRecorder(stream, { mimeType: `${mime};codecs=opus` })
        : MediaRecorder.isTypeSupported(mime)
          ? new MediaRecorder(stream, { mimeType: mime })
          : new MediaRecorder(stream);
      chunksRef.current = [];
      streamedVoiceFramesRef.current = false;
      recorder.ondataavailable = (e) => {
        if (e.data.size === 0) return;
        chunksRef.current.push(e.data);
        const frameMime = normalizeRealtimeAudioMime(e.data.type) ?? mime;
        if (client?.isReady) {
          streamedVoiceFramesRef.current = true;
          void client.sendAudio(e.data, frameMime);
        }
      };
      recorder.onstop = () => {
        stream.getTracks().forEach((track) => track.stop());
        setRecording(false);
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || mime });
        void finishRecordedTurn(blob);
      };
      mediaRecorderRef.current = recorder;
      stopLiveTranscriptRef.current?.();
      stopLiveTranscriptRef.current = startLiveTranscript(SPEECH_RECOGNITION_LOCALE, setLiveTranscript);
      recorder.start(250);
      setRecording(true);
    } catch {
      setError("No se pudo acceder al micrófono. Revisa los permisos del navegador.");
    }
  }

  function stopSpeaking() {
    speakSessionRef.current += 1; // invalida cualquier callback en vuelo
    speakAbortRef.current?.abort();
    speakAbortRef.current = null;
    ttsPlayerRef.current?.stop();
    void voiceClientRef.current?.interrupt();
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
    speakSessionRef.current += 1;
    const session = speakSessionRef.current;
    setSpeakingId(message.id);
    setSpeakingState("loading");
    setError(null);
    try {
      await playRealtimeSpeech(text, session, message.id);
    } catch {
      if (speakSessionRef.current !== session) return;
      const sentences = splitIntoSentences(text);
      if (sentences.length === 0) {
        setSpeakingId(null);
        setSpeakingState(null);
        return;
      }
      await playSentencesSequentially(sentences, session, message.id);
    }
  }

  async function playRealtimeSpeech(text: string, session: number, messageId: string) {
    const client = await ensureVoiceClient();
    if (speakSessionRef.current !== session) return;
    const player = getTtsPlayer();
    const finished = player.beginSession();
    await client.speak(text);
    setSpeakingState("playing");
    await finished;
    if (speakSessionRef.current === session) {
      setSpeakingId((current) => (current === messageId ? null : current));
      setSpeakingState((current) => (current === "playing" || current === "loading" ? null : current));
    }
  }

  /** Fallback HTTP si el WebSocket realtime no está disponible.
   * `POST /v1/voice/speak/stream` + `IncrementalTtsPlayer`: el audio arranca
   * con el primer chunk MPEG, sin esperar el MP3 completo de la oración. */
  async function playSentencesSequentially(
    sentences: string[],
    session: number,
    messageId: string,
  ) {
    const player = getTtsPlayer();
    const abort = new AbortController();
    speakAbortRef.current = abort;
    const voice = voiceId ?? "0uHpKhb0ymsdvmCtPV8y";
    let nextStream = speakTextStream(sentences[0], voice, abort.signal);
    for (let i = 0; i < sentences.length; i++) {
      if (speakSessionRef.current !== session || abort.signal.aborted) return;
      const stream = nextStream;
      nextStream =
        i + 1 < sentences.length ? speakTextStream(sentences[i + 1], voice, abort.signal) : null;

      const finished = player.beginSession();
      try {
        for await (const { chunk, mime } of stream) {
          if (speakSessionRef.current !== session) {
            player.stop();
            abort.abort();
            return;
          }
          player.append(chunk, mime);
          setSpeakingState("playing");
        }
        player.end();
      } catch (err) {
        player.stop();
        if (abort.signal.aborted || speakSessionRef.current !== session) return;
        setError(err instanceof Error ? err.message : "No se pudo generar el audio.");
        setSpeakingId(null);
        setSpeakingState(null);
        return;
      }
      await finished;
      if (speakSessionRef.current !== session) return;
    }
    if (speakSessionRef.current === session) {
      setSpeakingId((current) => (current === messageId ? null : current));
      setSpeakingState((current) => (current === "playing" || current === "loading" ? null : current));
    }
  }

  const turnBlocked = sending || pendingConfirmation !== null;
  const modelLabel = modelCatalog ? etiquetaSeleccion(modelCatalog, chatModel, chatEffort) : null;
  // Aviso de degradación: el backend atiende ESE turno con el modelo con visión
  // por defecto y deja la selección intacta, así que se anuncia antes de enviar
  // en vez de sorprender después.
  const modeloActivoInfo = modelCatalog?.modelos.find((modelo) => modelo.id === chatModel) ?? null;
  const visionDegradationNote =
    modelCatalog && modeloActivoInfo && !modeloActivoInfo.ve_imagenes && turnoTraeImagen(attachments)
      ? `Este turno usará ${modeloConVisionPorDefecto(modelCatalog)?.nombre ?? "otro modelo"}: ${
          modeloActivoInfo.nombre
        } no ve imágenes.`
      : null;
  const showStreamingBubble = sending && (streamingText.length > 0 || toolEvents.length === 0);
  const activeConversationTitle =
    conversations.find((conversation) => conversation.id === activeId)?.title || "Conversación nueva";

  function toggleHistoryCollapsed() {
    setHistoryCollapsed((current) => {
      const next = !current;
      try {
        window.localStorage.setItem(CONVERSATION_PANEL_KEY, String(next));
      } catch {
        // La interacción actual no depende de que pueda persistirse.
      }
      return next;
    });
  }

  return (
    <div className="flex h-full min-h-0 gap-3">
      <aside
        className={`${
          historyCollapsed ? "w-16" : "w-72"
        } hidden min-h-0 shrink-0 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm transition-[width] duration-200 dark:border-slate-800 dark:bg-slate-900 lg:flex lg:flex-col`}
        data-testid="desktop-conversation-panel"
      >
        <ConversationList
          conversations={conversations}
          activeId={activeId}
          loading={convLoading}
          creating={creating}
          collapsed={historyCollapsed}
          onSelect={handleSelect}
          onCreate={handleCreate}
          onDelete={handleDelete}
          onRename={handleRename}
          onToggleCollapsed={toggleHistoryCollapsed}
        />
      </aside>

      {mobileListOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button
            aria-label="Cerrar"
            className="absolute inset-0 bg-slate-900/50"
            onClick={() => setMobileListOpen(false)}
          />
          <div className="absolute inset-y-0 left-0 w-[min(20rem,88vw)] bg-white shadow-xl dark:bg-slate-900">
            <ConversationList
              conversations={conversations}
              activeId={activeId}
              loading={convLoading}
              creating={creating}
              onSelect={handleSelect}
              onCreate={handleCreate}
              onDelete={handleDelete}
              onRename={handleRename}
            />
          </div>
        </div>
      )}

      <section className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <div className="flex h-14 shrink-0 items-center gap-3 border-b border-slate-100 px-3 sm:px-4 dark:border-slate-800">
          <button
            onClick={() => setMobileListOpen(true)}
            className="rounded-xl p-2 text-slate-500 transition-colors hover:bg-slate-100 dark:hover:bg-slate-800 lg:hidden"
            aria-label="Abrir historial"
          >
            <MenuIcon className="h-4 w-4" />
          </button>
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-brand-100 text-brand-700 dark:bg-brand-900/50 dark:text-brand-300">
            <ChatIcon className="h-4 w-4" />
          </span>
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-sm font-semibold text-slate-900 dark:text-slate-100">
              {activeConversationTitle}
            </h1>
            <p className="flex items-center gap-1.5 text-[11px] text-slate-400">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
              Edecan está listo
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            {activeId && (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => void handleUndo()}
                loading={undoBusy}
                className="rounded-xl"
                title="Deshacer lo último"
                aria-label="Deshacer lo último"
              >
                <UndoIcon className="h-4 w-4" />
                <span className="hidden xl:inline">Deshacer</span>
              </Button>
            )}
            {canRecordAudio && activeId && (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => setAlwaysListenOpen(true)}
                className="rounded-xl"
                title="Abrir conversación por voz"
              >
                <MicIcon className="h-4 w-4" />
                <span className="hidden xl:inline">Escuchar</span>
              </Button>
            )}
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={handleCreate}
              loading={creating}
              className="h-9 w-9 rounded-xl px-0"
              aria-label="Nueva conversación"
              title="Nueva conversación"
            >
              <PlusIcon className="h-4 w-4" />
            </Button>
          </div>
        </div>

        {error && (
          <div className="px-4 pt-3">
            <Alert variant="error">{error}</Alert>
          </div>
        )}

        {!activeId && !convLoading ? (
          <div className="flex min-h-0 flex-1 items-center justify-center p-6">
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
            <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto overscroll-contain thin-scrollbar">
              <div className="mx-auto flex min-h-full w-full max-w-4xl flex-col space-y-5 px-4 py-6 sm:px-6 lg:px-8">
                {messagesLoading ? (
                  <div className="flex flex-1 justify-center py-8">
                    <Spinner className="h-5 w-5 text-slate-400" />
                  </div>
                ) : (
                  <>
                    {messages.length === 0 && !sending && <ChatHome onPickStarter={setInput} />}
                    {messages.map((message, index) => {
                      const lastAssistant =
                        message.role === "assistant" &&
                        !messages.slice(index + 1).some((item) => item.role === "assistant");
                      return (
                        <MessageBubble
                          key={message.id}
                          message={message}
                          canSpeak={canVoice}
                          speaking={speakingId === message.id ? speakingState : null}
                          onToggleSpeak={() => toggleSpeak(message)}
                          onPrefillMessage={setInput}
                          onFeedback={(kind, detail) => sendFeedback(message.id, kind, detail)}
                          onRegenerate={lastAssistant ? () => void handleRegenerate() : undefined}
                          onTogglePin={() =>
                            void handleMessageFlags(message, {
                              pinned: !(typeof message.content !== "string" && message.content?.pinned),
                            })
                          }
                          onToggleBookmark={() =>
                            void handleMessageFlags(message, {
                              bookmark: !(typeof message.content !== "string" && message.content?.bookmark),
                            })
                          }
                          onReply={() => setReplyTo({ id: message.id, text: messageText(message.content) })}
                        />
                      );
                    })}
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
                    {liveMission && (
                      <div className="rounded-2xl border border-brand-200 bg-brand-50/60 p-3 text-sm dark:border-brand-900 dark:bg-brand-950/30">
                        <p className="font-medium text-slate-800 dark:text-slate-100">
                          Misión · {liveMission.status}
                        </p>
                        <p className="mt-1 text-slate-600 dark:text-slate-300">{liveMission.objetivo}</p>
                        {liveMission.resultado && (
                          <p className="mt-2 whitespace-pre-wrap text-slate-700 dark:text-slate-200">
                            {liveMission.resultado}
                          </p>
                        )}
                        {!["done", "error", "cancelled"].includes(liveMission.status) && (
                          <form
                            className="mt-3 flex flex-wrap gap-2"
                            onSubmit={(event) => {
                              event.preventDefault();
                              const instruction = steerText.trim();
                              if (!instruction || steerBusy) return;
                              setSteerBusy(true);
                              void steerMission(liveMission.id, instruction)
                                .then((updated) => {
                                  setLiveMission(updated);
                                  setSteerText("");
                                })
                                .catch((err) => {
                                  setError(err instanceof Error ? err.message : "No se pudo redirigir.");
                                })
                                .finally(() => setSteerBusy(false));
                            }}
                          >
                            <input
                              value={steerText}
                              onChange={(event) => setSteerText(event.target.value)}
                              placeholder="Redirigir esta misión…"
                              className="min-w-[12rem] flex-1 rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-xs dark:border-slate-700 dark:bg-slate-950"
                            />
                            <button
                              type="submit"
                              disabled={steerBusy}
                              className="rounded-lg bg-slate-900 px-2.5 py-1.5 text-xs text-white dark:bg-white dark:text-slate-900"
                            >
                              Redirigir
                            </button>
                          </form>
                        )}
                      </div>
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
            </div>
            {replyTo && (
              <div className="mx-auto flex w-full max-w-4xl items-center justify-between gap-3 px-4 pb-1 text-xs text-slate-500 sm:px-6 lg:px-8">
                <p className="truncate">Respondiendo: {replyTo.text}</p>
                <button type="button" onClick={() => setReplyTo(null)} className="shrink-0 hover:text-slate-800 dark:hover:text-slate-100">
                  Cancelar
                </button>
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
              onRetryAttachment={handleRetryAttachment}
              onRemoveAttachment={handleRemoveAttachment}
              modelLabel={modelLabel}
              onOpenModelSelector={() => setModelSheetOpen(true)}
              modelSelectorDisabled={savingModel}
              visionDegradationNote={visionDegradationNote}
              workMode={workMode}
              onWorkModeChange={setWorkMode}
              liveTranscript={liveTranscript}
              ttsPlaying={speakingState !== null}
              onInterruptTts={stopSpeaking}
            />
          </>
        )}
      </section>
      {modelSheetOpen && modelCatalog && activeId && (
        <ModelSelector
          catalogo={modelCatalog}
          model={chatModel}
          effort={chatEffort}
          onSelect={(model, effort) => void handleSelectModel(model, effort)}
          onClose={() => setModelSheetOpen(false)}
        />
      )}
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

export default function AppChatRoute() {
  const [miniWindow, setMiniWindow] = useState(false);

  useEffect(() => {
    setMiniWindow(isDesktopMiniWindow());
  }, []);

  if (miniWindow) return <MiniAskPage />;
  return <ChatPage />;
}
