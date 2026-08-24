import assert from "node:assert/strict";
import test from "node:test";

import { ApiError, speakText, speakTextStream } from "./src/lib/api.ts";
import { IncrementalTtsPlayer } from "./src/lib/realtime-voice.ts";
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

function installBrowser() {
  globalThis.window = {
    sessionStorage: new MemoryStorage(),
    localStorage: new MemoryStorage(),
    location: { pathname: "/app", assign() {} },
  };
  setTokens("access-speak", "refresh-speak");
}

function cleanup() {
  clearTokens();
  delete globalThis.fetch;
  delete globalThis.window;
}

test("speakTextStream POSTea /speak/stream y arranca el fetch antes de iterar", async () => {
  installBrowser();
  let resolveStarted;
  const started = new Promise((resolve) => {
    resolveStarted = resolve;
  });
  globalThis.fetch = async (url, init = {}) => {
    resolveStarted({ url: String(url), init });
    return new Response(new Uint8Array(), {
      status: 200,
      headers: { "Content-Type": "audio/mpeg" },
    });
  };

  try {
    const iterable = speakTextStream("[warmly] Hola mundo.", "voice-1");
    const { url, init } = await started;
    assert.match(url, /\/v1\/voice\/speak\/stream$/);
    assert.equal(init.method, "POST");
    assert.deepEqual(JSON.parse(String(init.body)), {
      text: "[warmly] Hola mundo.",
      voice_id: "voice-1",
    });
    for await (const _chunk of iterable) {
      // El cuerpo vacío no debe rendir chunks; el contrato es que el iterable
      // se pueda consumir sin colgarse.
    }
  } finally {
    cleanup();
  }
});

test("speakTextStream rinde el primer chunk MPEG antes de que cierre el cuerpo", async () => {
  installBrowser();
  let controller;
  const body = new ReadableStream({
    start(c) {
      controller = c;
    },
  });
  globalThis.fetch = async () =>
    new Response(body, { status: 200, headers: { "Content-Type": "audio/mpeg; charset=utf-8" } });

  const received = [];
  let resolveFirst;
  const firstChunk = new Promise((resolve) => {
    resolveFirst = resolve;
  });

  try {
    const consume = (async () => {
      for await (const part of speakTextStream("Oración completa [pause].")) {
        received.push(part);
        if (received.length === 1) resolveFirst();
      }
    })();

    await new Promise((resolve) => setImmediate(resolve));
    controller.enqueue(Uint8Array.from([1, 2, 3]));
    await firstChunk;
    assert.equal(received.length, 1);
    assert.deepEqual(Array.from(received[0].chunk), [1, 2, 3]);
    assert.equal(received[0].mime, "audio/mpeg");

    controller.enqueue(Uint8Array.from([4, 5]));
    controller.close();
    await consume;
    assert.equal(received.length, 2);
    assert.deepEqual(Array.from(received[1].chunk), [4, 5]);
  } finally {
    cleanup();
  }
});

test("speakTextStream propaga el error HTTP del backend", async () => {
  installBrowser();
  globalThis.fetch = async () =>
    new Response(JSON.stringify({ detail: "sin cuota de voz" }), {
      status: 429,
      headers: { "Content-Type": "application/json" },
    });

  try {
    await assert.rejects(async () => {
      for await (const _chunk of speakTextStream("Hola.")) {
        // no debe rendir
      }
    }, (error) => {
      assert.ok(error instanceof ApiError);
      assert.equal(error.status, 429);
      assert.match(error.message, /sin cuota de voz/);
      return true;
    });
  } finally {
    cleanup();
  }
});

test("speakText sigue pidiendo el blob completo en /speak", async () => {
  installBrowser();
  let requested;
  globalThis.fetch = async (url, init = {}) => {
    requested = { url: String(url), init };
    return new Response(Uint8Array.from([9, 8, 7]), {
      status: 200,
      headers: { "Content-Type": "audio/mpeg" },
    });
  };

  try {
    const blob = await speakText("muestra", "preview-voice");
    assert.match(requested.url, /\/v1\/voice\/speak$/);
    assert.doesNotMatch(requested.url, /stream/);
    assert.deepEqual(JSON.parse(String(requested.init.body)), {
      text: "muestra",
      voice_id: "preview-voice",
    });
    assert.equal(blob.size, 3);
  } finally {
    cleanup();
  }
});

test("IncrementalTtsPlayer acepta chunks audio/mpeg sin esperar un blob", () => {
  const player = new IncrementalTtsPlayer();
  const finished = player.beginSession();
  player.append(Uint8Array.from([1, 2, 3, 4]), "audio/mpeg");
  player.append(Uint8Array.from([5, 6]), "audio/mpeg");
  player.end();
  player.stop();
  return finished;
});
