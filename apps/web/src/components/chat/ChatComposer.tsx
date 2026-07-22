"use client";

import { type KeyboardEvent, useRef } from "react";

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

  return (
    <div className="border-t border-slate-200 bg-white p-3 dark:border-slate-800 dark:bg-slate-900">
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
      {attachments.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-2" aria-live="polite">
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
        <p className="mb-2 text-xs text-rose-600 dark:text-rose-400">{visibleAttachmentError}</p>
      )}
      {recording && (
        <div className="mb-2 flex items-center gap-2 text-xs font-medium text-rose-600 dark:text-rose-400">
          <span className="h-2 w-2 animate-pulse rounded-full bg-rose-600" />
          Grabando… toca el micrófono para detener
        </div>
      )}
      <div className="flex items-end gap-2">
        <Textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          rows={2}
          placeholder="Pídele cualquier cosa a Edecan…"
          className="flex-1 resize-none"
          disabled={sending}
        />
        <div className="flex shrink-0 gap-2">
          <Button
            type="button"
            variant="secondary"
            size="md"
            onClick={() => fileInputRef.current?.click()}
            disabled={sending || attachments.length >= MAX_CHAT_ATTACHMENTS}
            title={`Adjuntar archivos (máximo ${MAX_CHAT_ATTACHMENTS})`}
            aria-label="Adjuntar archivos"
          >
            <PlusIcon className="h-4 w-4" />
          </Button>
          <Button
            type="button"
            variant={recording ? "danger" : "secondary"}
            size="md"
            onClick={onToggleRecording}
            disabled={sending || transcribing || !canVoice}
            title={voiceTitle}
            aria-label={recording ? "Detener grabación" : "Hablar"}
          >
            {transcribing ? (
              <Spinner className="h-4 w-4" />
            ) : recording ? (
              <SquareIcon className="h-4 w-4" />
            ) : (
              <MicIcon className="h-4 w-4" />
            )}
          </Button>
          <Button
            type="button"
            onClick={onSend}
            disabled={!canSubmit}
            loading={sending}
            aria-label="Enviar"
          >
            <SendIcon className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  );
}
