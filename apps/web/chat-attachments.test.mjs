import assert from "node:assert/strict";
import test from "node:test";

import {
  attachmentsBlockSend,
  buildChatMessageInput,
  canSubmitChat,
  MAX_CHAT_ATTACHMENTS,
  readyAttachmentIds,
} from "./src/lib/chat-attachments.ts";

function attachment(overrides = {}) {
  return {
    localId: "local-1",
    filename: "documento.pdf",
    sizeBytes: 120,
    status: "ready",
    fileId: "11111111-1111-4111-a111-111111111111",
    mime: "application/pdf",
    error: null,
    ...overrides,
  };
}

test("permite texto, adjuntos listos o ambos, pero bloquea cargas incompletas", () => {
  assert.equal(canSubmitChat("Hola", [], false), true);
  assert.equal(canSubmitChat("", [attachment()], false), true);
  assert.equal(canSubmitChat("", [], false), false);
  assert.equal(canSubmitChat("No lo envíes aún", [attachment({ status: "uploading", fileId: null })], false), false);
  assert.equal(canSubmitChat("No lo envíes incompleto", [attachment({ status: "error", fileId: null })], false), false);
  assert.equal(canSubmitChat("Hola", [], true), false);
  assert.equal(attachmentsBlockSend([attachment()]), false);
});

test("solo extrae IDs de cargas confirmadas", () => {
  assert.deepEqual(
    readyAttachmentIds([
      attachment(),
      attachment({ localId: "local-2", status: "uploading", fileId: null }),
      attachment({ localId: "local-3", status: "error", fileId: null }),
    ]),
    ["11111111-1111-4111-a111-111111111111"],
  );
});

test("arma el body sin alterar el texto y deduplica adjuntos", () => {
  const body = buildChatMessageInput("  conserva mis espacios  ", ["file-a", "file-a", "file-b"]);
  assert.deepEqual(body, {
    text: "  conserva mis espacios  ",
    attachments: ["file-a", "file-b"],
  });
});

test("rechaza mensajes vacíos y más adjuntos que el contrato", () => {
  assert.throws(() => buildChatMessageInput("   ", []), /texto o al menos un archivo/);
  assert.throws(
    () => buildChatMessageInput("Analiza", Array.from({ length: MAX_CHAT_ATTACHMENTS + 1 }, (_, index) => `file-${index}`)),
    /como máximo 10/,
  );
});
