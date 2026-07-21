import type { ChatAttachmentDraft, ChatMessageInput } from "./types";

/** Coincide con `ChatMessageIn.attachments.max_length` del contrato público. */
export const MAX_CHAT_ATTACHMENTS = 10;

export function readyAttachmentIds(attachments: readonly ChatAttachmentDraft[]): string[] {
  return attachments.flatMap((attachment) =>
    attachment.status === "ready" && attachment.fileId ? [attachment.fileId] : [],
  );
}

export function attachmentsBlockSend(attachments: readonly ChatAttachmentDraft[]): boolean {
  return attachments.some((attachment) => attachment.status !== "ready");
}

export function canSubmitChat(
  text: string,
  attachments: readonly ChatAttachmentDraft[],
  sending: boolean,
): boolean {
  if (sending || attachmentsBlockSend(attachments)) return false;
  return Boolean(text.trim()) || readyAttachmentIds(attachments).length > 0;
}

export function buildChatMessageInput(text: string, attachments: readonly string[]): ChatMessageInput {
  const uniqueAttachments = [...new Set(attachments)];
  if (uniqueAttachments.length > MAX_CHAT_ATTACHMENTS) {
    throw new Error(`Puedes adjuntar como máximo ${MAX_CHAT_ATTACHMENTS} archivos por mensaje.`);
  }
  if (!text.trim() && uniqueAttachments.length === 0) {
    throw new Error("El mensaje necesita texto o al menos un archivo.");
  }
  return { text, attachments: uniqueAttachments };
}
