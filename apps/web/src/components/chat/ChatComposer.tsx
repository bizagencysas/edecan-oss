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
import { listSkills, type SkillSummary } from "@/lib/api-skills";
import { collectMentionTargets, type MentionableItem } from "@/lib/mentions";

type MenuKind = "mention" | "command";

interface CommandItem {
  label: string;
  hint?: string;
  insert: string;
}

interface TriggerState {
  kind: MenuKind;
  query: string;
  start: number;
  end: number;
}

/** Localiza el gatillo activo (si lo hay) justo antes del cursor. */
function detectTrigger(text: string, cursor: number): TriggerState | null {
  const before = text.slice(0, cursor);
  const match = /(?:^|[\s\n])([@/])([^\s\n]*)$/.exec(before);
  if (!match) return null;
  const trigger = match[1] as "@" | "/";
  const query = match[2] ?? "";
  const start = cursor - query.length - 1;
  return { kind: trigger === "@" ? "mention" : "command", query, start, end: cursor };
}

const BUILTIN_COMMANDS: CommandItem[] = [
  { label: "Limpiar contexto", hint: "Reinicia la memoria de esta conversación", insert: "/clear " },
  { label: "Ramificar", hint: "Abre una rama desde este punto", insert: "/branch " },
  { label: "Rebobinar", hint: "Vuelve al estado anterior de la conversación", insert: "/rewind " },
];

function commandsFromSkills(skills: SkillSummary[]): CommandItem[] {
  return skills.map((skill) => ({
    label: skill.nombre,
    hint: skill.descripcion || "Skill instalada",
    insert: `/${skill.slug} `,
  }));
}

/** Pinta el token de una mención como chip dentro de un elemento del menú. */
function mentionBadge(item: MentionableItem): string {
  switch (item.kind) {
    case "agente":
      return "Compañero";
    case "team":
      return "Equipo";
    case "workspace":
      return "Workspace";
    case "conector":
      return "Conector";
  }
}

export function ChatComposer({
  value,
  onChange,
  onSend,
  sending,
  streaming = false,
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
  /** Bloquea envío (p. ej. confirmación de tool peligrosa pendiente). */
  sending: boolean;
  /** Hay un turno del agente en curso; el composer sigue usable para encolar. */
  streaming?: boolean;
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
  // Autocompletado de @menciones y /comandos.
  const [menuKind, setMenuKind] = useState<MenuKind | null>(null);
  const [menuQuery, setMenuQuery] = useState("");
  const [menuActiveIndex, setMenuActiveIndex] = useState(0);
  const [mentionItems, setMentionItems] = useState<MentionableItem[]>([]);
  const [commandItems, setCommandItems] = useState<CommandItem[]>(BUILTIN_COMMANDS);
  const triggerRef = useRef<TriggerState | null>(null);
  const pendingCaretRef = useRef<number | null>(null);
  const mentionCacheRef = useRef<MentionableItem[] | null>(null);
  const skillsCacheRef = useRef<SkillSummary[] | null>(null);
  const canSubmit = canSubmitChat(value, attachments, sending);
  const visibleAttachmentError =
    attachmentError ??
    captureError ??
    localCaptureError ??
    attachments.find((attachment) => attachment.status === "error")?.error ??
    null;

  function updateMenu(trigger: TriggerState | null) {
    triggerRef.current = trigger;
    if (!trigger) {
      setMenuKind(null);
      return;
    }
    if (trigger.kind === "mention") {
      setMenuKind("mention");
      setMenuQuery(trigger.query);
      setMenuActiveIndex(0);
      if (mentionCacheRef.current) return;
      collectMentionTargets()
        .then((items) => {
          mentionCacheRef.current = items;
          if (triggerRef.current?.kind === "mention") setMentionItems(items);
        })
        .catch(() => undefined);
    } else {
      setMenuKind("command");
      setMenuQuery(trigger.query);
      setMenuActiveIndex(0);
      if (skillsCacheRef.current) return;
      listSkills()
        .then((skills) => {
          skillsCacheRef.current = skills;
          if (triggerRef.current?.kind === "command") {
            setCommandItems([...BUILTIN_COMMANDS, ...commandsFromSkills(skills)]);
          }
        })
        .catch(() => undefined);
    }
  }

  function handleTextareaChange(event: React.ChangeEvent<HTMLTextAreaElement>) {
    const next = event.target.value;
    onChange(next);
    const cursor = event.target.selectionStart ?? next.length;
    updateMenu(detectTrigger(next, cursor));
  }

  function visibleItems(): Array<MentionableItem | CommandItem> {
    if (menuKind === "mention") {
      const q = menuQuery.toLowerCase();
      return mentionItems
        .filter((item) => item.label.toLowerCase().includes(q) || item.token.includes(q))
        .slice(0, 6);
    }
    if (menuKind === "command") {
      const q = menuQuery.toLowerCase();
      return commandItems
        .filter((item) => item.label.toLowerCase().includes(q) || item.insert.includes(q))
        .slice(0, 6);
    }
    return [];
  }

  function applyMenuSelection(index: number) {
    const trigger = triggerRef.current;
    const items = visibleItems();
    if (!trigger || !menuKind || index < 0 || index >= items.length) return;
    const item = items[index];
    const insert = menuKind === "mention" ? `@${(item as MentionableItem).token} ` : (item as CommandItem).insert;
    const next = value.slice(0, trigger.start) + insert + value.slice(trigger.end);
    pendingCaretRef.current = trigger.start + insert.length;
    onChange(next);
    setMenuKind(null);
    triggerRef.current = null;
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (menuKind !== null) {
      const items = visibleItems();
      if (items.length > 0) {
        if (event.key === "ArrowDown") {
          event.preventDefault();
          setMenuActiveIndex((current) => (current + 1) % items.length);
          return;
        }
        if (event.key === "ArrowUp") {
          event.preventDefault();
          setMenuActiveIndex((current) => (current - 1 + items.length) % items.length);
          return;
        }
        if (event.key === "Enter" && !event.shiftKey) {
          event.preventDefault();
          applyMenuSelection(menuActiveIndex);
          return;
        }
        if (event.key === "Escape") {
          event.preventDefault();
          setMenuKind(null);
          triggerRef.current = null;
          return;
        }
      }
    }
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
    if (pendingCaretRef.current !== null) {
      const position = pendingCaretRef.current;
      pendingCaretRef.current = null;
      textarea.focus();
      textarea.setSelectionRange(position, position);
    }
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

  const menuItems = visibleItems();
  const menuOpen = menuKind !== null && menuItems.length > 0;

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
          className="relative overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-[0_8px_30px_rgba(15,23,42,0.08)] transition-colors focus-within:border-brand-400 focus-within:ring-2 focus-within:ring-brand-500/10 dark:border-slate-700 dark:bg-slate-900"
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
          {menuOpen && (
            <div
              className="absolute left-2 right-2 top-2 z-30 max-h-64 overflow-y-auto rounded-xl border border-slate-200 bg-white p-1 shadow-lg dark:border-slate-700 dark:bg-slate-900"
              role="listbox"
              aria-label={menuKind === "mention" ? "Mencionar" : "Comandos"}
            >
              {menuItems.map((item, index) => {
                const isMention = menuKind === "mention";
                const label = item.label;
                const sublabel = isMention
                  ? mentionBadge(item as MentionableItem)
                  : (item as CommandItem).hint;
                return (
                  <button
                    key={isMention ? (item as MentionableItem).token : (item as CommandItem).insert}
                    type="button"
                    role="option"
                    aria-selected={index === menuActiveIndex}
                    onMouseEnter={() => setMenuActiveIndex(index)}
                    onMouseDown={(event) => {
                      event.preventDefault();
                      applyMenuSelection(index);
                    }}
                    className={`flex w-full items-center justify-between gap-3 rounded-lg px-2.5 py-2 text-left text-sm ${
                      index === menuActiveIndex
                        ? "bg-slate-100 text-slate-900 dark:bg-slate-800 dark:text-slate-100"
                        : "text-slate-700 dark:text-slate-200"
                    }`}
                  >
                    <span className="min-w-0 truncate">
                      {isMention ? "@" : ""}
                      {label}
                    </span>
                    {sublabel && (
                      <span className="shrink-0 text-[11px] text-slate-400 dark:text-slate-500">
                        {sublabel}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          )}
          <Textarea
            ref={textareaRef}
            value={value}
            onChange={handleTextareaChange}
            onKeyDown={handleKeyDown}
            rows={1}
            placeholder={workMode ? "Describe el trabajo que Edecan debe terminar…" : "Escríbele a Edecán…"}
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
              loading={sending && !streaming}
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
