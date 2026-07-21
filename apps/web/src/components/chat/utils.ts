import type { ArtifactRef, MessageOut } from "@/lib/types";
import { parseArtifactRefs } from "@/lib/chat-blocks";

/** `MessageOut.content` puede llegar como `{text}`, string suelto o `null` (§10.7). */
export function messageText(content: MessageOut["content"]): string {
  if (content === null || content === undefined) return "";
  if (typeof content === "string") return content;
  return content.text ?? "";
}

/** Adjuntos privados guardados junto al mensaje del usuario. */
export function messageAttachments(content: MessageOut["content"]): ArtifactRef[] {
  if (!content || typeof content === "string") return [];
  return parseArtifactRefs(content.attachments);
}

export function newLocalId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return `local-${crypto.randomUUID()}`;
  }
  return `local-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function localMessage(role: MessageOut["role"], text: string): MessageOut {
  return {
    id: newLocalId(),
    role,
    content: { text },
    tool_calls: null,
    tokens_in: 0,
    tokens_out: 0,
    created_at: new Date().toISOString(),
  };
}

/** Referencias de archivo seguras persistidas dentro del tool log del mensaje. */
export function messageArtifacts(toolCalls: unknown[] | null): ArtifactRef[] {
  if (!Array.isArray(toolCalls)) return [];
  const artifacts: ArtifactRef[] = [];
  const seen = new Set<string>();
  for (const call of toolCalls) {
    if (!call || typeof call !== "object") continue;
    for (const ref of parseArtifactRefs((call as { artifacts?: unknown }).artifacts)) {
      if (seen.has(ref.file_id)) continue;
      artifacts.push(ref);
      seen.add(ref.file_id);
    }
  }
  return artifacts;
}
