"use client";

import { useState } from "react";

import { PlayIcon, RetryIcon, ShareIcon, SquareIcon } from "@/components/icons";
import { Spinner } from "@/components/ui";

function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

export function MessageActions({
  text,
  canSpeak,
  speaking,
  onToggleSpeak,
  onFeedback,
  onRegenerate,
  feedbackSent,
  pinned,
  bookmarked,
  onTogglePin,
  onToggleBookmark,
  onReply,
}: {
  text: string;
  canSpeak?: boolean;
  speaking?: "loading" | "playing" | null;
  onToggleSpeak?: () => void;
  onFeedback?: (kind: "thumb_up" | "thumb_down" | "correction", detail?: string) => Promise<void>;
  onRegenerate?: () => void;
  feedbackSent?: "thumb_up" | "thumb_down" | null;
  pinned?: boolean;
  bookmarked?: boolean;
  onTogglePin?: () => void;
  onToggleBookmark?: () => void;
  onReply?: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const [sent, setSent] = useState<"thumb_up" | "thumb_down" | "correction" | null>(feedbackSent ?? null);
  const [correcting, setCorrecting] = useState(false);
  const [correction, setCorrection] = useState("");
  if (!text.trim()) return null;

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    } catch {
      setCopied(false);
    }
  }

  async function share() {
    if (typeof navigator.share === "function") {
      try {
        await navigator.share({ text });
        return;
      } catch {
        // El usuario canceló o el share nativo no está disponible.
      }
    }
    await copy();
  }

  return (
    <div className="mt-2 flex flex-wrap items-center gap-1 text-xs text-slate-500 dark:text-slate-400">
      <button
        type="button"
        onClick={() => void copy()}
        className="rounded-md px-1.5 py-1 hover:bg-slate-100 hover:text-slate-800 dark:hover:bg-slate-800 dark:hover:text-slate-100"
      >
        {copied ? "Copiado" : "Copiar"}
      </button>
      {canSpeak && onToggleSpeak && (
        <button
          type="button"
          onClick={onToggleSpeak}
          className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 hover:bg-slate-100 hover:text-slate-800 dark:hover:bg-slate-800 dark:hover:text-slate-100"
        >
          {speaking === "loading" ? (
            <Spinner className="h-3 w-3" />
          ) : speaking === "playing" ? (
            <SquareIcon className="h-3 w-3" />
          ) : (
            <PlayIcon className="h-3 w-3" />
          )}
          {speaking === "playing" ? "Detener" : "Escuchar"}
        </button>
      )}
      <button
        type="button"
        onClick={() => void share()}
        className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 hover:bg-slate-100 hover:text-slate-800 dark:hover:bg-slate-800 dark:hover:text-slate-100"
      >
        <ShareIcon className="h-3 w-3" />
        Compartir
      </button>
      {onReply && (
        <button
          type="button"
          onClick={onReply}
          className="rounded-md px-1.5 py-1 hover:bg-slate-100 hover:text-slate-800 dark:hover:bg-slate-800 dark:hover:text-slate-100"
        >
          Responder
        </button>
      )}
      {onTogglePin && (
        <button
          type="button"
          onClick={onTogglePin}
          className={cx(
            "rounded-md px-1.5 py-1 hover:bg-slate-100 dark:hover:bg-slate-800",
            pinned && "text-amber-600",
          )}
        >
          {pinned ? "Fijado" : "Fijar"}
        </button>
      )}
      {onToggleBookmark && (
        <button
          type="button"
          onClick={onToggleBookmark}
          className={cx(
            "rounded-md px-1.5 py-1 hover:bg-slate-100 dark:hover:bg-slate-800",
            bookmarked && "text-brand-600",
          )}
        >
          {bookmarked ? "Guardado" : "Guardar"}
        </button>
      )}
      {onRegenerate && (
        <button
          type="button"
          onClick={onRegenerate}
          className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 hover:bg-slate-100 hover:text-slate-800 dark:hover:bg-slate-800 dark:hover:text-slate-100"
        >
          <RetryIcon className="h-3 w-3" />
          Regenerar
        </button>
      )}
      {onFeedback && (
        <>
          <button
            type="button"
            aria-label="La respuesta fue útil"
            disabled={sent !== null}
            onClick={() => void onFeedback("thumb_up").then(() => setSent("thumb_up")).catch(() => undefined)}
            className={cx(
              "rounded-md px-1.5 py-1 hover:bg-slate-100 dark:hover:bg-slate-800",
              sent === "thumb_up" && "text-emerald-600",
            )}
          >
            Útil
          </button>
          <button
            type="button"
            aria-label="La respuesta necesita corrección"
            disabled={sent !== null}
            onClick={() => setCorrecting(true)}
            className={cx(
              "rounded-md px-1.5 py-1 hover:bg-slate-100 dark:hover:bg-slate-800",
              (sent === "thumb_down" || sent === "correction") && "text-rose-600",
            )}
          >
            Corregir
          </button>
          {sent && <span className="px-1">Gracias</span>}
        </>
      )}
      {correcting && onFeedback && (
        <form
          className="mt-2 flex w-full flex-wrap items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            const detail = correction.trim();
            if (!detail) return;
            void onFeedback("correction", detail)
              .then(() => {
                setSent("correction");
                setCorrecting(false);
                setCorrection("");
              })
              .catch(() => undefined);
          }}
        >
          <input
            value={correction}
            onChange={(event) => setCorrection(event.target.value)}
            placeholder="¿Qué había que corregir?"
            className="min-w-[12rem] flex-1 rounded-md border border-slate-200 bg-white px-2 py-1 text-xs text-slate-800 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
          />
          <button type="submit" className="rounded-md px-1.5 py-1 text-xs hover:bg-slate-100 dark:hover:bg-slate-800">
            Enviar
          </button>
        </form>
      )}
    </div>
  );
}
