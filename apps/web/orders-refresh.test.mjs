import assert from "node:assert/strict";
import test from "node:test";

import { listOrdenes } from "./src/lib/api-ordenes.ts";
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

test("Órdenes comparte la rotación y reintenta con el Bearer nuevo", async () => {
  globalThis.window = {
    sessionStorage: new MemoryStorage(),
    localStorage: new MemoryStorage(),
    location: { pathname: "/app/ordenes", assign() {} },
    prompt: () => null,
  };
  setTokens("access-old", "refresh-old");
  const authorizations = [];
  let orderCalls = 0;
  let refreshCalls = 0;
  globalThis.fetch = async (url, init = {}) => {
    const path = new URL(String(url)).pathname;
    if (path === "/v1/auth/refresh") {
      refreshCalls += 1;
      assert.deepEqual(JSON.parse(String(init.body)), { refresh_token: "refresh-old" });
      return Response.json({ access_token: "access-next", refresh_token: "refresh-next" });
    }
    orderCalls += 1;
    authorizations.push(new Headers(init.headers).get("Authorization"));
    if (orderCalls === 1) return Response.json({ detail: "expired" }, { status: 401 });
    return Response.json([]);
  };

  try {
    assert.deepEqual(await listOrdenes(), []);
    assert.equal(refreshCalls, 1);
    assert.deepEqual(authorizations, ["Bearer access-old", "Bearer access-next"]);
  } finally {
    clearTokens();
    delete globalThis.fetch;
    delete globalThis.window;
  }
});
