import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  clearTokens,
  getAccessToken,
  getRefreshToken,
  hasSession,
  setTokens,
} from "./src/lib/tokens.ts";

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

test("Rust marca la WebView HTTP con la identidad persistente de desktop", () => {
  const rustBackend = readFileSync(
    new URL("../desktop/src-tauri/src/backend.rs", import.meta.url),
    "utf8",
  );
  assert.match(rustBackend, /const DESKTOP_USER_AGENT: &str = "EdecanDesktop\/0\.7"/);
  assert.match(rustBackend, /\.user_agent\(DESKTOP_USER_AGENT\)/);
});

test("la app de escritorio restaura la sesión sin persistir el access token", () => {
  const persistentStorage = new MemoryStorage();
  globalThis.window = {
    navigator: { userAgent: "EdecanDesktop/0.7" },
    sessionStorage: new MemoryStorage(),
    localStorage: persistentStorage,
  };

  try {
    setTokens("access-short-lived", "refresh-rotating");
    assert.equal(getAccessToken(), "access-short-lived");
    assert.equal(window.sessionStorage.getItem("edecan_refresh_token"), null);
    assert.equal(persistentStorage.getItem("edecan_access_token"), null);
    assert.equal(persistentStorage.getItem("edecan_refresh_token"), "refresh-rotating");

    window.sessionStorage = new MemoryStorage();
    assert.equal(getAccessToken(), null);
    assert.equal(getRefreshToken(), "refresh-rotating");
    assert.equal(hasSession(), true);

    clearTokens();
    assert.equal(hasSession(), false);
  } finally {
    clearTokens();
    delete globalThis.window;
  }
});

test("el global Tauri sigue identificando desktop en ventanas locales", () => {
  globalThis.window = {
    __TAURI__: {},
    navigator: { userAgent: "Mozilla/5.0" },
    sessionStorage: new MemoryStorage(),
    localStorage: new MemoryStorage(),
  };
  try {
    setTokens("access", "refresh-tauri");
    assert.equal(window.localStorage.getItem("edecan_refresh_token"), "refresh-tauri");
    assert.equal(window.sessionStorage.getItem("edecan_refresh_token"), null);
  } finally {
    clearTokens();
    delete globalThis.window;
  }
});

test("el navegador normal mantiene la sesión limitada a la pestaña", () => {
  globalThis.window = {
    sessionStorage: new MemoryStorage(),
    localStorage: new MemoryStorage(),
  };
  try {
    setTokens("access", "refresh");
    assert.equal(window.sessionStorage.getItem("edecan_refresh_token"), "refresh");
    assert.equal(window.localStorage.getItem("edecan_refresh_token"), null);
  } finally {
    clearTokens();
    delete globalThis.window;
  }
});
