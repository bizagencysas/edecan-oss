"use client";

import { type KeyboardEvent } from "react";

import { MicIcon, SendIcon, SquareIcon } from "@/components/icons";
import { Button, Spinner, Textarea } from "@/components/ui";

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
}) {
  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (value.trim() && !sending) onSend();
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
        <div className="flex shrink-0 flex-col gap-2">
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
            disabled={!value.trim() || sending}
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
