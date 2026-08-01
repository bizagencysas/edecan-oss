import assert from "node:assert/strict";
import test from "node:test";

import { sendMessageStream } from "./src/lib/api.ts";
import { clearTokens, setTokens } from "./src/lib/tokens.ts";

class MemoryStorage {
  #values = new Map();

  getItem(key) {
    return this.#values.get(key) ?? null;
  }

  setItem(key, value) {
    this.#values.set(key, String(value));
  }

  removeItem(key) {
    this.#values.delete(key);
  }
}

test("envía IDs adjuntos y consume CRLF más el frame final sin separador", async () => {
  globalThis.window = {
    sessionStorage: new MemoryStorage(),
    localStorage: new MemoryStorage(),
    location: { pathname: "/app", assign() {} },
  };
  setTokens("access-chat", "refresh-chat");
  const fileId = "11111111-1111-4111-a111-111111111111";
  let requestBody;
  let requestHeaders;
  globalThis.fetch = async (_url, init = {}) => {
    requestBody = JSON.parse(String(init.body));
    requestHeaders = new Headers(init.headers);
    return new Response(
      'event: message.delta\r\ndata: {"type":"text_delta","text":"Listo"}\r\n\r\n' +
        'event: message.done\r\ndata: {"type":"done",\r\ndata: "usage":{"output_tokens":1}}',
      { headers: { "Content-Type": "text/event-stream" } },
    );
  };
  const events = [];

  try {
    const idempotencyKey = "22222222-2222-4222-a222-222222222222";
    await sendMessageStream(
      "conversation-1",
      "Revisa el archivo",
      (event) => events.push(event),
      undefined,
      [fileId],
      idempotencyKey,
    );
    assert.deepEqual(requestBody, { text: "Revisa el archivo", attachments: [fileId] });
    assert.equal(requestHeaders.get("Idempotency-Key"), idempotencyKey);
    assert.deepEqual(events, [
      { type: "text_delta", text: "Listo" },
      { type: "done", usage: { output_tokens: 1 } },
    ]);
  } finally {
    clearTokens();
    delete globalThis.fetch;
    delete globalThis.window;
  }
});

test("propaga un evento de error SSE para que el turno conserve su clave de reintento", async () => {
  globalThis.window = {
    sessionStorage: new MemoryStorage(),
    localStorage: new MemoryStorage(),
    location: { pathname: "/app", assign() {} },
  };
  setTokens("access-chat", "refresh-chat");
  globalThis.fetch = async () =>
    new Response('data: {"type":"error","message":"Se cortó la ejecución"}\n\n', {
      headers: { "Content-Type": "text/event-stream" },
    });

  try {
    await assert.rejects(
      sendMessageStream("conversation-1", "Hazlo", () => undefined),
      /Se cortó la ejecución/,
    );
  } finally {
    clearTokens();
    delete globalThis.fetch;
    delete globalThis.window;
  }
});
