"use client";

import { PlayIcon, SquareIcon } from "@/components/icons";
import { Spinner } from "@/components/ui";
import { messageBlocks, toolTimelineFromCalls } from "@/lib/chat-blocks";
import type { MessageOut } from "@/lib/types";

import { ArtifactLinks } from "./ArtifactLinks";
import { MarkdownText } from "./markdown";
import { RichMessageBlocks } from "./RichMessageBlocks";
import { ToolTimeline } from "./ToolTimeline";
import { messageArtifacts, messageAttachments, messageText } from "./utils";

function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

export function MessageBubble({
  message,
  canSpeak,
  speaking,
  onToggleSpeak,
  onPrefillMessage,
}: {
  message: MessageOut;
  canSpeak?: boolean;
  speaking?: "loading" | "playing" | null;
  onToggleSpeak?: () => void;
  onPrefillMessage?: (message: string) => void;
}) {
  const isUser = message.role === "user";
  const text = messageText(message.content);
  const blocks = messageBlocks(message.tool_calls);
  const toolTimeline = toolTimelineFromCalls(message.tool_calls);
  const mediaFileIds = new Set(
    blocks.flatMap((block) => (block.type === "media" ? [block.artifact.file_id] : [])),
  );
  const artifacts = [...messageAttachments(message.content), ...messageArtifacts(message.tool_calls)].filter(
    (artifact, index, all) =>
      !mediaFileIds.has(artifact.file_id) &&
      all.findIndex((candidate) => candidate.file_id === artifact.file_id) === index,
  );
  if (!text && message.role !== "assistant" && artifacts.length === 0) return null;

  return (
    <div className={cx("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cx(
          "group rounded-2xl px-4 py-3 text-[15px] leading-7 shadow-sm",
          isUser ? "max-w-[min(42rem,88%)]" : "max-w-[min(48rem,94%)]",
          isUser
            ? "rounded-br-md bg-gradient-to-br from-brand-600 to-indigo-600 text-white"
            : "rounded-bl-md border border-slate-200 bg-white text-slate-800 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-100",
        )}
      >
        {(text || (message.role === "assistant" && blocks.length === 0)) && (
          <div className="whitespace-pre-wrap break-words [&_hr]:my-2 [&_ul]:my-1">
            {text ? <MarkdownText text={text} /> : "…"}
          </div>
        )}
        {!isUser && <RichMessageBlocks blocks={blocks} onPrefillMessage={onPrefillMessage} />}
        {!isUser && toolTimeline.length > 0 && (
          <ToolTimeline events={toolTimeline} showResultPreview={!text} />
        )}
        <ArtifactLinks artifacts={artifacts} />
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
