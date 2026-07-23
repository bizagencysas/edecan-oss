"use client";

import { type KeyboardEvent, useEffect, useRef } from "react";

import { CheckIcon, FileIcon, MicIcon, PlusIcon, RetryIcon, SendIcon, SquareIcon, XIcon } from "@/components/icons";
import { Button, Spinner, Textarea } from "@/components/ui";
import { canSubmitChat, MAX_CHAT_ATTACHMENTS } from "@/lib/chat-attachments";
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
}) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const canSubmit = canSubmitChat(value, attachments, sending);
  const visibleAttachmentError =
    attachmentError ?? attachments.find((attachment) => attachment.status === "error")?.error ?? null;

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
        <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-[0_8px_30px_rgba(15,23,42,0.08)] transition-colors focus-within:border-brand-400 focus-within:ring-2 focus-within:ring-brand-500/10 dark:border-slate-700 dark:bg-slate-900">
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
          {recording && (
            <div className="flex items-center gap-2 px-3 pt-2 text-xs font-medium text-rose-600 dark:text-rose-400">
              <span className="h-2 w-2 animate-pulse rounded-full bg-rose-600" />
              Grabando… toca el micrófono para detener
            </div>
          )}
          <Textarea
            ref={textareaRef}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={handleKeyDown}
            rows={1}
            placeholder="Pídele cualquier cosa a Edecan…"
            className="min-h-12 max-h-48 resize-none border-0 bg-transparent px-4 py-3 text-[15px] shadow-none focus:border-transparent focus:ring-0 dark:bg-transparent"
            disabled={sending}
          />
          <div className="flex items-center justify-between gap-3 px-2.5 pb-2.5">
            <div className="flex items-center gap-1.5">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => fileInputRef.current?.click()}
                disabled={sending || attachments.length >= MAX_CHAT_ATTACHMENTS}
                title={`Adjuntar archivos (máximo ${MAX_CHAT_ATTACHMENTS})`}
                aria-label="Adjuntar archivos"
                className="h-9 w-9 rounded-full px-0"
              >
                <PlusIcon className="h-4 w-4" />
              </Button>
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
              <span className="hidden text-[11px] text-slate-400 sm:inline">Enter para enviar · Shift + Enter para una línea nueva</span>
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
