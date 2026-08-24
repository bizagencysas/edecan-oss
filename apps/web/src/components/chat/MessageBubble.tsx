"use client";

import { messageBlocks } from "@/lib/chat-blocks";
import { messageSources } from "@/lib/chat-sources";
import type { MessageOut } from "@/lib/types";

import { ArtifactLinks } from "./ArtifactLinks";
import { MarkdownText } from "./markdown";
import { MessageActions } from "./MessageActions";
import { RichMessageBlocks } from "./RichMessageBlocks";
import { SourceChips } from "./SourceChips";
import { messageArtifacts, messageAttachments, messageText, stripSpeechTags } from "./utils";

function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

export function MessageBubble({
  message,
  canSpeak,
  speaking,
  onToggleSpeak,
  onPrefillMessage,
  onFeedback,
  onRegenerate,
  onTogglePin,
  onToggleBookmark,
  onReply,
}: {
  message: MessageOut;
  canSpeak?: boolean;
  speaking?: "loading" | "playing" | null;
  onToggleSpeak?: () => void;
  onPrefillMessage?: (message: string) => void;
  onFeedback?: (kind: "thumb_up" | "thumb_down" | "correction", detail?: string) => Promise<void>;
  onRegenerate?: () => void;
  onTogglePin?: () => void;
  onToggleBookmark?: () => void;
  onReply?: () => void;
}) {
  const isUser = message.role === "user";
  const text = messageText(message.content);
  const blocks = messageBlocks(message.tool_calls);
  const sources = isUser ? [] : messageSources(message.tool_calls);
  const mediaFileIds = new Set(
    blocks.flatMap((block) => (block.type === "media" ? [block.artifact.file_id] : [])),
  );
  const artifacts = [...messageAttachments(message.content), ...messageArtifacts(message.tool_calls)].filter(
    (artifact, index, all) =>
      !mediaFileIds.has(artifact.file_id) &&
      all.findIndex((candidate) => candidate.file_id === artifact.file_id) === index,
  );
  const flags = typeof message.content === "string" ? {} : message.content ?? {};
  if (!text && message.role !== "assistant" && artifacts.length === 0) return null;

  return (
    <div className={cx("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cx(
          "rounded-2xl px-4 py-3 text-[15px] leading-7 shadow-sm",
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
        <ArtifactLinks artifacts={artifacts} />
        {!isUser && <SourceChips sources={sources} />}
        {!isUser && typeof message.content !== "string" && message.content?.explanation && (
          <details className="mt-2 rounded-lg border border-slate-200/80 px-2.5 py-1.5 text-xs dark:border-slate-700">
            <summary className="cursor-pointer text-slate-500 dark:text-slate-400">Por qué</summary>
            <p className="mt-1 whitespace-pre-wrap text-slate-600 dark:text-slate-300">
              {message.content.explanation}
            </p>
          </details>
        )}
        <MessageActions
          text={stripSpeechTags(text)}
          canSpeak={!isUser && canSpeak}
          speaking={speaking}
          onToggleSpeak={isUser ? undefined : onToggleSpeak}
          onFeedback={isUser ? undefined : onFeedback}
          onRegenerate={onRegenerate}
          pinned={flags.pinned === true}
          bookmarked={flags.bookmark === true}
          onTogglePin={onTogglePin}
          onToggleBookmark={onToggleBookmark}
          onReply={onReply}
        />
      </div>
    </div>
  );
}
