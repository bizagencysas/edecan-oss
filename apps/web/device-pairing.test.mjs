import assert from "node:assert/strict";
import test from "node:test";

import { createDevicePairing } from "./src/lib/api.ts";
import {
  formatPairingTimeLeft,
  pairingExpiryMs,
  pairingSecondsLeft,
} from "./src/lib/device-pairing.ts";
import { clearTokens, setTokens } from "./src/lib/tokens.ts";

class MemoryStorage {
  #values = new Map();
  getItem(key) { return this.#values.get(key) ?? null; }
  setItem(key, value) { this.#values.set(key, String(value)); }
  removeItem(key) { this.#values.delete(key); }
}

test("calcula y presenta la expiración del QR sin bajar de cero", () => {
  const now = Date.parse("2026-07-21T12:00:00Z");
  const absolute = pairingExpiryMs({
    pairing_uri: "edecan://pair/example",
    expires_at: "2026-07-21T12:02:05Z",
    expires_in_seconds: 999,
  }, now);

  assert.equal(absolute, Date.parse("2026-07-21T12:02:05Z"));
  assert.equal(pairingSecondsLeft(absolute, now), 125);
  assert.equal(formatPairingTimeLeft(125), "2:05");
  assert.equal(pairingSecondsLeft(absolute, absolute + 1), 0);
});

test("usa expires_in_seconds si expires_at no es válido", () => {
  const now = 1_000;
  assert.equal(pairingExpiryMs({
    pairing_uri: "edecan://pair/example",
    expires_at: "",
    expires_in_seconds: 90,
  }, now), 91_000);
});

test("solicita un pairing autenticado por POST y conserva el URI solo como dato", async () => {
  globalThis.window = {
    sessionStorage: new MemoryStorage(),
    localStorage: new MemoryStorage(),
    location: { pathname: "/app/ajustes", assign() {} },
  };
  setTokens("access-for-pairing", "refresh-for-pairing");
  let request;
  globalThis.fetch = async (url, init = {}) => {
    request = { url: String(url), method: init.method, headers: new Headers(init.headers) };
    return Response.json({
      pairing_uri: "edecan://pair/one-time-secret",
      expires_at: "2026-07-21T12:10:00Z",
      expires_in_seconds: 600,
    });
  };

  try {
    const pairing = await createDevicePairing();
    assert.equal(request.url.endsWith("/v1/devices/pairing"), true);
    assert.equal(request.method, "POST");
    assert.equal(request.headers.get("Authorization"), "Bearer access-for-pairing");
    assert.equal(pairing.pairing_uri, "edecan://pair/one-time-secret");
  } finally {
    clearTokens();
    delete globalThis.fetch;
    delete globalThis.window;
  }
});
