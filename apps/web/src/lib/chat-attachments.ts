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

/** Espejo de `_DIRECT_VISION_MIMES` (`routers/conversations.py`): son los mime
 * que el backend inserta como IMAGEN dentro del turno en vez de dejarlos para
 * `leer_archivo`. Solo con uno de estos aplica el aviso de degradación. */
const MIMES_VISION_DIRECTA = new Set(["image/jpeg", "image/png", "image/gif", "image/webp"]);

/** ¿El próximo turno llevaría una imagen que el modelo va a mirar? Cuenta
 * cualquier adjunto todavía en el composer, incluso subiendo: el aviso tiene
 * que aparecer mientras se decide, no cuando ya se envió. */
export function turnoTraeImagen(attachments: readonly ChatAttachmentDraft[]): boolean {
  return attachments.some((attachment) =>
    MIMES_VISION_DIRECTA.has((attachment.mime ?? "").split(";", 1)[0].trim().toLowerCase()),
  );
}

export function canSubmitChat(
  text: string,
  attachments: readonly ChatAttachmentDraft[],
  blocked: boolean,
): boolean {
  if (blocked || attachmentsBlockSend(attachments)) return false;
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
