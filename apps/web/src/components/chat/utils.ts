import type { MessageOut } from "@/lib/types";

/** `MessageOut.content` puede llegar como `{text}`, string suelto o `null` (§10.7). */
export function messageText(content: MessageOut["content"]): string {
  if (content === null || content === undefined) return "";
  if (typeof content === "string") return content;
  return content.text ?? "";
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
