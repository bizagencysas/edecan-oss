"use client";

import { type DragEvent, type KeyboardEvent, useEffect, useRef, useState } from "react";

import {
  CameraIcon,
  CheckIcon,
  ChevronDownIcon,
  FileIcon,
  MicIcon,
  PlusIcon,
  RetryIcon,
  ScreenIcon,
  SendIcon,
  SquareIcon,
  XIcon,
} from "@/components/icons";
import { Button, Spinner, Textarea } from "@/components/ui";
import { canSubmitChat, MAX_CHAT_ATTACHMENTS } from "@/lib/chat-attachments";
import { captureCameraPhoto, captureDisplayFrame } from "@/lib/desktop-capture";
import type { ChatAttachmentDraft } from "@/lib/types";

export function ChatComposer({
  value,
  onChange,
  onSend,
  sending,
  canVoice,
  voiceFlagEnabled,
  recording,
  transcribing,
  onToggleRecording,
  attachments,
  attachmentError,
  onSelectFiles,
  onRetryAttachment,
  onRemoveAttachment,
  modelLabel,
  onOpenModelSelector,
  modelSelectorDisabled = false,
  visionDegradationNote = null,
  workMode = false,
  onWorkModeChange,
  captureError = null,
  liveTranscript = null,
  ttsPlaying = false,
  onInterruptTts,
}: {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  sending: boolean;
  /** Puede grabar ahora: la voz está configurada y el navegador soporta MediaRecorder. */
  canVoice: boolean;
  /** La instalación tiene voz web habilitada, independiente del soporte del navegador. */
  voiceFlagEnabled: boolean;
  recording: boolean;
  transcribing: boolean;
  onToggleRecording: () => void;
  attachments: ChatAttachmentDraft[];
  attachmentError: string | null;
  onSelectFiles: (files: File[]) => void;
  onRetryAttachment: (localId: string) => void;
  onRemoveAttachment: (localId: string) => void;
  /** Texto de la pastilla del selector ("Oda · Alto", "Scout", "Automático").
   * `null` mientras el catálogo no cargó: la pastilla no se pinta y nadie ve un
   * control que todavía no puede cumplir. */
  modelLabel?: string | null;
  onOpenModelSelector?: () => void;
  modelSelectorDisabled?: boolean;
  /** Aviso de que ESTE turno se atenderá con otro modelo por traer una imagen
   * y el elegido no verla. */
  visionDegradationNote?: string | null;
  workMode?: boolean;
  onWorkModeChange?: (enabled: boolean) => void;
  captureError?: string | null;
  /** Transcripción provisional mientras el micrófono está abierto. */
  liveTranscript?: string | null;
  ttsPlaying?: boolean;
  onInterruptTts?: () => void;
}) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const [attachOpen, setAttachOpen] = useState(false);
  const [capturing, setCapturing] = useState<"camera" | "screen" | null>(null);
  const [localCaptureError, setLocalCaptureError] = useState<string | null>(null);
  const canSubmit = canSubmitChat(value, attachments, sending);
  const visibleAttachmentError =
    attachmentError ??
    captureError ??
    localCaptureError ??
    attachments.find((attachment) => attachment.status === "error")?.error ??
    null;

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (canSubmit) onSend();
    }
  }

  const voiceTitle = !voiceFlagEnabled
    ? "La voz aún no está configurada en esta instalación. Puedes activarla en Ajustes."
    : !canVoice
      ? "Tu navegador no soporta grabación de audio."
      : recording
        ? "Detener grabación"
        : "Hablar (push-to-talk)";

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "0px";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 192)}px`;
  }, [value]);

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    const files = Array.from(event.dataTransfer.files ?? []);
    if (files.length > 0) onSelectFiles(files);
  }

  async function capture(kind: "camera" | "screen") {
    setAttachOpen(false);
    setCapturing(kind);
    setLocalCaptureError(null);
    try {
      const file = kind === "camera" ? await captureCameraPhoto() : await captureDisplayFrame();
      onSelectFiles([file]);
    } catch (error) {
      const denied = error instanceof DOMException && error.name === "NotAllowedError";
      setLocalCaptureError(
        denied
          ? kind === "camera"
            ? "Edecán no tiene permiso de cámara. Concédelo en el sistema y reintenta."
            : "Edecán no tiene permiso para capturar la pantalla."
          : error instanceof Error
            ? error.message
            : "No se pudo capturar.",
      );
    } finally {
      setCapturing(null);
    }
  }

  return (
    <div className="shrink-0 border-t border-slate-200 bg-slate-50/90 px-3 py-3 backdrop-blur dark:border-slate-800 dark:bg-slate-950/70">
      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="hidden"
        disabled={sending || attachments.length >= MAX_CHAT_ATTACHMENTS}
        onChange={(event) => {
          const files = Array.from(event.currentTarget.files ?? []);
          event.currentTarget.value = "";
          if (files.length > 0) onSelectFiles(files);
        }}
      />
      <div className="mx-auto w-full max-w-4xl">
        <div
          className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-[0_8px_30px_rgba(15,23,42,0.08)] transition-colors focus-within:border-brand-400 focus-within:ring-2 focus-within:ring-brand-500/10 dark:border-slate-700 dark:bg-slate-900"
          onDragOver={(event) => event.preventDefault()}
          onDrop={handleDrop}
        >
          {attachments.length > 0 && (
            <div className="flex flex-wrap gap-2 border-b border-slate-100 px-3 py-2.5 dark:border-slate-800" aria-live="polite">
              {attachments.map((attachment) => (
                <div
                  key={attachment.localId}
                  className={`flex max-w-full items-center gap-1.5 rounded-lg border px-2 py-1.5 text-xs ${
                    attachment.status === "error"
                      ? "border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-900 dark:bg-rose-950/40 dark:text-rose-300"
                      : "border-slate-200 bg-slate-50 text-slate-600 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
                  }`}
                  title={attachment.error ?? attachment.filename}
                >
                  {attachment.status === "uploading" ? (
                    <Spinner className="h-3.5 w-3.5 shrink-0" />
                  ) : attachment.status === "ready" ? (
                    <CheckIcon className="h-3.5 w-3.5 shrink-0 text-emerald-500" />
                  ) : (
                    <FileIcon className="h-3.5 w-3.5 shrink-0" />
                  )}
                  <span className="max-w-48 truncate">{attachment.filename}</span>
                  <span className="shrink-0 text-[10px] opacity-70">
                    {attachment.status === "uploading" ? "Subiendo" : attachment.status === "ready" ? "Listo" : "Falló"}
                  </span>
                  {attachment.status === "error" && (
                    <button
                      type="button"
                      className="ml-0.5 rounded p-0.5 hover:bg-rose-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 dark:hover:bg-rose-900"
                      onClick={() => onRetryAttachment(attachment.localId)}
                      aria-label={`Reintentar carga de ${attachment.filename}`}
                      title="Reintentar"
                    >
                      <RetryIcon className="h-3.5 w-3.5" />
                    </button>
                  )}
                  <button
                    type="button"
                    className="ml-0.5 rounded p-0.5 hover:bg-slate-200/70 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 dark:hover:bg-slate-700"
                    onClick={() => onRemoveAttachment(attachment.localId)}
                    aria-label={`${attachment.status === "uploading" ? "Cancelar carga de" : "Quitar"} ${attachment.filename}`}
                  >
                    <XIcon className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}
            </div>
          )}
          {visibleAttachmentError && (
            <p className="px-3 pt-2 text-xs text-rose-600 dark:text-rose-400">{visibleAttachmentError}</p>
          )}
          {visionDegradationNote && (
            <p className="px-3 pt-2 text-xs text-amber-700 dark:text-amber-300" aria-live="polite">
              {visionDegradationNote}
            </p>
          )}
          {workMode && (
            <p className="px-3 pt-2 text-xs text-brand-700 dark:text-brand-300">
              Modo trabajar: esto se lanza como misión en segundo plano, no como un chat corto.
            </p>
          )}
          {recording && (
            <div className="px-3 pt-2 text-xs font-medium text-rose-600 dark:text-rose-400">
              <div className="flex items-center gap-2">
                <span className="h-2 w-2 animate-pulse rounded-full bg-rose-600" />
                Grabando… toca el micrófono para detener
              </div>
              {liveTranscript ? (
                <p className="mt-1 truncate font-normal text-slate-500 dark:text-slate-400">{liveTranscript}</p>
              ) : null}
            </div>
          )}
          {ttsPlaying && !recording && (
            <div className="flex items-center justify-between gap-2 px-3 pt-2 text-xs text-slate-500 dark:text-slate-400">
              <span>Hablando…</span>
              {onInterruptTts ? (
                <button
                  type="button"
                  onClick={onInterruptTts}
                  className="rounded-full border border-slate-200 px-2 py-0.5 text-[11px] font-medium text-slate-600 hover:border-rose-300 hover:text-rose-700 dark:border-slate-700 dark:text-slate-300"
                >
                  Interrumpir
                </button>
              ) : null}
            </div>
          )}
          <Textarea
            ref={textareaRef}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={handleKeyDown}
            rows={1}
            placeholder={workMode ? "Describe el trabajo que Edecan debe terminar…" : "Pídele cualquier cosa a Edecan…"}
            className="min-h-12 max-h-48 resize-none border-0 bg-transparent px-4 py-3 text-[15px] shadow-none focus:border-transparent focus:ring-0 dark:bg-transparent"
            disabled={sending}
          />
          <div className="flex items-center justify-between gap-3 px-2.5 pb-2.5">
            <div className="relative flex items-center gap-1.5">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => setAttachOpen((current) => !current)}
                disabled={sending || attachments.length >= MAX_CHAT_ATTACHMENTS || capturing !== null}
                title={`Adjuntar archivos (máximo ${MAX_CHAT_ATTACHMENTS})`}
                aria-label="Adjuntar"
                aria-expanded={attachOpen}
                className="h-9 w-9 rounded-full px-0"
              >
                {capturing ? <Spinner className="h-4 w-4" /> : <PlusIcon className="h-4 w-4" />}
              </Button>
              {attachOpen && (
                <div className="absolute bottom-11 left-0 z-20 w-52 rounded-xl border border-slate-200 bg-white p-1 text-sm shadow-sm dark:border-slate-700 dark:bg-slate-900">
                  <button
                    type="button"
                    className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-slate-700 hover:bg-slate-50 dark:text-slate-200 dark:hover:bg-slate-800"
                    onClick={() => {
                      setAttachOpen(false);
                      fileInputRef.current?.click();
                    }}
                  >
                    <FileIcon className="h-4 w-4" />
                    Archivos
                  </button>
                  <button
                    type="button"
                    className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-slate-700 hover:bg-slate-50 dark:text-slate-200 dark:hover:bg-slate-800"
                    onClick={() => void capture("camera")}
                  >
                    <CameraIcon className="h-4 w-4" />
                    Foto
                  </button>
                  <button
                    type="button"
                    className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-slate-700 hover:bg-slate-50 dark:text-slate-200 dark:hover:bg-slate-800"
                    onClick={() => void capture("screen")}
                  >
                    <ScreenIcon className="h-4 w-4" />
                    Captura de pantalla
                  </button>
                </div>
              )}
              <Button
                type="button"
                variant={recording ? "danger" : "ghost"}
                size="sm"
                onClick={onToggleRecording}
                disabled={sending || transcribing || !canVoice}
                title={voiceTitle}
                aria-label={recording ? "Detener grabación" : "Hablar"}
                className="h-9 w-9 rounded-full px-0"
              >
                {transcribing ? (
                  <Spinner className="h-4 w-4" />
                ) : recording ? (
                  <SquareIcon className="h-4 w-4" />
                ) : (
                  <MicIcon className="h-4 w-4" />
                )}
              </Button>
              {onWorkModeChange && (
                <button
                  type="button"
                  onClick={() => onWorkModeChange(!workMode)}
                  className={`rounded-full px-2.5 py-1.5 text-[11px] font-medium transition-colors ${
                    workMode
                      ? "bg-brand-600 text-white"
                      : "border border-slate-200 text-slate-600 hover:border-brand-300 hover:text-brand-700 dark:border-slate-700 dark:text-slate-300"
                  }`}
                  aria-pressed={workMode}
                >
                  Trabajar
                </button>
              )}
              {modelLabel && onOpenModelSelector && (
                <button
                  type="button"
                  onClick={onOpenModelSelector}
                  disabled={modelSelectorDisabled}
                  className="inline-flex max-w-[11rem] items-center gap-1 rounded-full border border-slate-200 bg-white px-2.5 py-1.5 text-[11px] font-medium text-slate-600 transition-colors hover:border-brand-300 hover:text-brand-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500 disabled:cursor-not-allowed disabled:opacity-60 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300 dark:hover:border-brand-700 dark:hover:text-brand-300"
                  title="Elegir el modelo de esta conversación"
                  aria-label={`Modelo: ${modelLabel}. Cambiar`}
                >
                  <span className="truncate">{modelLabel}</span>
                  <ChevronDownIcon className="h-3 w-3 shrink-0 opacity-70" />
                </button>
              )}
              <span className="hidden text-[11px] text-slate-400 lg:inline">Enter para enviar · Shift + Enter para una línea nueva</span>
            </div>
            <Button
              type="button"
              onClick={onSend}
              disabled={!canSubmit}
              loading={sending}
              aria-label="Enviar"
              className="h-9 w-9 rounded-full px-0"
            >
              <SendIcon className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
