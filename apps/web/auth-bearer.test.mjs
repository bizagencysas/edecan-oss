import assert from "node:assert/strict";
import test from "node:test";

import { enableTotp, verifyTotp, disableTotp } from "./src/lib/api.ts";
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

test("las tres operaciones TOTP envían el Bearer de la sesión", async () => {
  globalThis.window = {
    sessionStorage: new MemoryStorage(),
    localStorage: new MemoryStorage(),
    location: { pathname: "/app", assign() {} },
  };
  setTokens("access-for-totp", "refresh-for-totp");
  const requests = [];
  globalThis.fetch = async (url, init = {}) => {
    requests.push({ url: String(url), headers: new Headers(init.headers) });
    if (String(url).endsWith("/enable")) {
      return Response.json({ secret: "secret", provisioning_uri: "otpauth://example" });
    }
    if (String(url).endsWith("/verify")) return Response.json({ verified: true });
    return Response.json({ disabled: true });
  };

  try {
    await enableTotp();
    await verifyTotp("123456");
    await disableTotp("correct horse battery staple");
    assert.equal(requests.length, 3);
    for (const request of requests) {
      assert.equal(request.headers.get("Authorization"), "Bearer access-for-totp", request.url);
    }
  } finally {
    clearTokens();
    delete globalThis.fetch;
    delete globalThis.window;
  }
});
