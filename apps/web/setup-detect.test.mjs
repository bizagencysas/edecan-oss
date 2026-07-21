import assert from "node:assert/strict";
import test from "node:test";

import { getSetupDetect } from "./src/lib/api-configuracion.ts";
import { clearTokens, setTokens } from "./src/lib/tokens.ts";

class MemoryStorage {
  #values = new Map();
  getItem(key) { return this.#values.get(key) ?? null; }
  setItem(key, value) { this.#values.set(key, String(value)); }
  removeItem(key) { this.#values.delete(key); }
}

test("deduplica la detección CLI concurrente del onboarding", async () => {
  globalThis.window = {
    sessionStorage: new MemoryStorage(),
    localStorage: new MemoryStorage(),
    location: { pathname: "/app/bienvenida", assign() {} },
  };
  setTokens("access-detect", "refresh-detect");
  let requests = 0;
  globalThis.fetch = async () => {
    requests += 1;
    await new Promise((resolve) => setTimeout(resolve, 20));
    return Response.json({
      local_mode: true,
      claude_cli: { installed: false, path: null, version: null },
      codex_cli: { installed: true, path: "/usr/local/bin/codex", version: "codex-cli 1" },
      ollama: { running: false, base_url: null, models: [] },
    });
  };

  try {
    const [first, second] = await Promise.all([getSetupDetect(), getSetupDetect()]);
    assert.equal(requests, 1);
    assert.deepEqual(first, second);
    assert.equal(first.codex_cli.installed, true);
  } finally {
    clearTokens();
    delete globalThis.fetch;
    delete globalThis.window;
  }
});
