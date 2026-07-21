import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  clearTokens,
  getAccessToken,
  getDesktopCapability,
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
  const rustTray = readFileSync(
    new URL("../desktop/src-tauri/src/tray.rs", import.meta.url),
    "utf8",
  );
  assert.match(rustBackend, /const DESKTOP_USER_AGENT: &str = "EdecanDesktop\/0\.7"/);
  assert.match(rustBackend, /\.user_agent\(DESKTOP_USER_AGENT\)/);
  assert.match(rustBackend, /http:\/\/127\.0\.0\.1:\{port\}\/\?edecan_desktop=1/);
  assert.match(rustBackend, /#edecan_capability=\{capability\}/);
  assert.match(rustTray, /backend::current_local_ui_url/);
});

test("la señal desktop sobrevive la navegación y los JWT mueren con el proceso", () => {
  const persistentStorage = new MemoryStorage();
  globalThis.window = {
    location: {
      hash: "#edecan_capability=capability-from-rust",
      pathname: "/",
      search: "?edecan_desktop=1",
    },
    history: { replaceState: () => {} },
    navigator: { userAgent: "Mozilla/5.0" },
    sessionStorage: new MemoryStorage(),
    localStorage: persistentStorage,
  };

  try {
    assert.equal(hasSession(), false);
    window.location.search = "";
    setTokens("access-short-lived", "refresh-rotating");
    assert.equal(getAccessToken(), "access-short-lived");
    assert.equal(getDesktopCapability(), "capability-from-rust");
    assert.equal(window.sessionStorage.getItem("edecan_desktop_runtime"), "1");
    assert.equal(window.sessionStorage.getItem("edecan_refresh_token"), "refresh-rotating");
    assert.equal(persistentStorage.getItem("edecan_access_token"), null);
    assert.equal(persistentStorage.getItem("edecan_refresh_token"), null);

    window.sessionStorage = new MemoryStorage();
    window.location.hash = "#edecan_capability=next-process-capability";
    window.location.search = "?edecan_desktop=1";
    assert.equal(getAccessToken(), null);
    assert.equal(getRefreshToken(), null);
    assert.equal(hasSession(), false);
    assert.equal(getDesktopCapability(), "next-process-capability");

    clearTokens();
    assert.equal(hasSession(), false);
  } finally {
    clearTokens();
    delete globalThis.window;
  }
});

test("el global Tauri identifica desktop sin persistir credenciales", () => {
  globalThis.window = {
    __TAURI__: {},
    navigator: { userAgent: "Mozilla/5.0" },
    sessionStorage: new MemoryStorage(),
    localStorage: new MemoryStorage(),
  };
  try {
    setTokens("access", "refresh-tauri");
    assert.equal(window.localStorage.getItem("edecan_refresh_token"), null);
    assert.equal(window.sessionStorage.getItem("edecan_refresh_token"), "refresh-tauri");
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
