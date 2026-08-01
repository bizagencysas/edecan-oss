import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { redactChatSecrets } from "./src/lib/chat-secret-redaction.ts";

test("la burbuja optimista nunca pinta una API key", () => {
  const secret = "sk-proj-example-secret-1234567890";
  const result = redactChatSecrets(`Configura OpenAI con ${secret}`);

  assert.equal(result, "Configura OpenAI con [credencial protegida]");
  assert.ok(!result.includes(secret));
});

test("el chat muestra la versión redactada pero conserva el texto original para el request", async () => {
  const source = await readFile(new URL("./src/app/(app)/app/page.tsx", import.meta.url), "utf8");

  assert.match(source, /text:\s*redactChatSecrets\(text\)/);
  assert.match(source, /sendMessageStream\([\s\S]*?activeId,[\s\S]*?text,/);
});
