"use client";

import { PlayIcon, SquareIcon } from "@/components/icons";
import { Spinner } from "@/components/ui";
import type { MessageOut } from "@/lib/types";

import { ArtifactLinks } from "./ArtifactLinks";
import { MarkdownText } from "./markdown";
import { messageArtifacts, messageText } from "./utils";

function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

export function MessageBubble({
  message,
  canSpeak,
  speaking,
  onToggleSpeak,
}: {
  message: MessageOut;
  canSpeak?: boolean;
  speaking?: "loading" | "playing" | null;
  onToggleSpeak?: () => void;
}) {
  const isUser = message.role === "user";
  const text = messageText(message.content);
  const artifacts = messageArtifacts(message.tool_calls);
  if (!text && message.role !== "assistant") return null;

  return (
    <div className={cx("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cx(
          "group max-w-[85%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed shadow-sm sm:max-w-[75%]",
          isUser
            ? "bg-brand-600 text-white"
            : "border border-slate-200 bg-white text-slate-800 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-100",
        )}
      >
        <div className="whitespace-pre-wrap break-words [&_hr]:my-2 [&_ul]:my-1">
          {text ? <MarkdownText text={text} /> : "…"}
        </div>
        {!isUser && <ArtifactLinks artifacts={artifacts} />}
        {!isUser && canSpeak && text && (
          <button
            onClick={onToggleSpeak}
            className="mt-1.5 inline-flex items-center gap-1 text-xs text-slate-400 opacity-0 transition-opacity hover:text-brand-600 group-hover:opacity-100 dark:hover:text-brand-400"
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
      </div>
    </div>
  );
}
