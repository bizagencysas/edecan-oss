import assert from "node:assert/strict";
import test from "node:test";

import {
  recoverSessionAfterUnauthorized,
  isRefreshResultCurrent,
  refreshSession,
} from "./src/lib/session-refresh.ts";
import {
  clearTokens,
  getAccessToken,
  getRefreshToken,
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

function installBrowser() {
  globalThis.window = {
    sessionStorage: new MemoryStorage(),
    localStorage: new MemoryStorage(),
    prompt: () => null,
  };
}

function deferredResponse() {
  let resolve;
  const promise = new Promise((resolver) => {
    resolve = resolver;
  });
  return { promise, resolve };
}

test.beforeEach(() => {
  installBrowser();
  globalThis.fetch = undefined;
});

test.afterEach(() => {
  clearTokens();
  delete globalThis.fetch;
  delete globalThis.window;
});

test("un refresh tardío no revive una sesión cerrada", async () => {
  setTokens("access-old", "refresh-old");
  const response = deferredResponse();
  let markStarted;
  const started = new Promise((resolve) => {
    markStarted = resolve;
  });
  globalThis.fetch = async () => {
    markStarted();
    return response.promise;
  };

  const refreshing = refreshSession("https://api.example.test");
  await started;
  clearTokens();
  response.resolve(
    Response.json({ access_token: "access-rotated", refresh_token: "refresh-rotated" }),
  );

  assert.deepEqual(await refreshing, { ok: false, reason: "superseded" });
  assert.equal(getAccessToken(), null);
  assert.equal(getRefreshToken(), null);
});

test("un refresh tardío no pisa un login más reciente", async () => {
  setTokens("access-old", "refresh-old");
  const response = deferredResponse();
  let markStarted;
  const started = new Promise((resolve) => {
    markStarted = resolve;
  });
  globalThis.fetch = async () => {
    markStarted();
    return response.promise;
  };

  const refreshing = refreshSession("https://api.example.test");
  await started;
  setTokens("access-new-login", "refresh-new-login");
  response.resolve(
    Response.json({ access_token: "access-rotated", refresh_token: "refresh-rotated" }),
  );

  assert.deepEqual(await refreshing, { ok: false, reason: "superseded" });
  assert.equal(getAccessToken(), "access-new-login");
  assert.equal(getRefreshToken(), "refresh-new-login");
});

test("un 401 tardío del refresh viejo no invalida un login más reciente", async () => {
  setTokens("access-old", "refresh-old");
  const response = deferredResponse();
  let markStarted;
  const started = new Promise((resolve) => {
    markStarted = resolve;
  });
  globalThis.fetch = async () => {
    markStarted();
    return response.promise;
  };

  const refreshing = refreshSession("https://api.example.test");
  await started;
  setTokens("access-new-login", "refresh-new-login");
  response.resolve(Response.json({ detail: "Token inválido" }, { status: 401 }));

  assert.deepEqual(await refreshing, { ok: false, reason: "superseded" });
  assert.equal(getAccessToken(), "access-new-login");
  assert.equal(getRefreshToken(), "refresh-new-login");
});

test("el flujo TOTP queda ligado a la sesión que recibió el primer 401", async () => {
  setTokens("access-old", "refresh-old");
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    return Response.json(
      { detail: "Se requiere un código TOTP válido para esta cuenta." },
      { status: 401 },
    );
  };
  window.prompt = () => {
    setTokens("access-new-login", "refresh-new-login");
    return "123456";
  };

  assert.deepEqual(await recoverSessionAfterUnauthorized("https://api.example.test"), {
    ok: false,
    reason: "superseded",
  });
  assert.equal(calls, 1, "no debe refrescar la sesión nueva con el TOTP de la anterior");
  assert.equal(getRefreshToken(), "refresh-new-login");
});

test("clasifica red, rate limit y 5xx como transitorios sin borrar tokens", async (t) => {
  for (const scenario of [
    { name: "red", response: () => Promise.reject(new TypeError("offline")) },
    { name: "429", response: () => Promise.resolve(new Response(null, { status: 429 })) },
    { name: "503", response: () => Promise.resolve(new Response(null, { status: 503 })) },
  ]) {
    await t.test(scenario.name, async () => {
      setTokens(`access-${scenario.name}`, `refresh-${scenario.name}`);
      globalThis.fetch = scenario.response;
      assert.deepEqual(await refreshSession("https://api.example.test"), {
        ok: false,
        reason: "transient",
      });
      assert.equal(getRefreshToken(), `refresh-${scenario.name}`);
    });
  }
});

test("distingue TOTP requerido de un refresh definitivamente inválido", async () => {
  setTokens("access", "refresh");
  globalThis.fetch = async () =>
    Response.json(
      { detail: "Se requiere un código TOTP válido para esta cuenta." },
      { status: 401 },
    );
  assert.deepEqual(await refreshSession("https://api.example.test"), {
    ok: false,
    reason: "totp_required",
  });

  globalThis.fetch = async () => Response.json({ detail: "Token inválido" }, { status: 401 });
  assert.deepEqual(await refreshSession("https://api.example.test"), {
    ok: false,
    reason: "invalid",
  });
});

test("deduplica el refresh real y envía una sola rotación", async () => {
  setTokens("access", "refresh");
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    return Response.json({ access_token: "next-access", refresh_token: "next-refresh" });
  };

  const [first, second] = await Promise.all([
    refreshSession("https://api.example.test"),
    refreshSession("https://api.example.test"),
  ]);
  assert.equal(first.ok, true);
  assert.equal(second.ok, true);
  assert.equal(calls, 1);
});

test("el flujo completo conserva éxito después de avanzar la generación", async () => {
  setTokens("access", "refresh");
  globalThis.fetch = async () =>
    Response.json({ access_token: "next-access", refresh_token: "next-refresh" });

  assert.equal((await recoverSessionAfterUnauthorized("https://api.example.test")).ok, true);
  assert.equal(getAccessToken(), "next-access");
  assert.equal(getRefreshToken(), "next-refresh");
});

test("un éxito de refresh no autoriza replay si otro login ocurre después", async () => {
  setTokens("access", "refresh");
  globalThis.fetch = async () =>
    Response.json({ access_token: "rotated-access", refresh_token: "rotated-refresh" });

  const result = await recoverSessionAfterUnauthorized("https://api.example.test");
  assert.equal(isRefreshResultCurrent(result), true);
  setTokens("other-login-access", "other-login-refresh");
  assert.equal(isRefreshResultCurrent(result), false);
});
