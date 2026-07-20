import assert from "node:assert/strict";
import test from "node:test";

import {
  apiOriginForCsp,
  buildContentSecurityPolicy,
  buildSecurityHeaders,
} from "./security-headers.mjs";

test("la CSP productiva limita conexiones al origen de API configurado", () => {
  const csp = buildContentSecurityPolicy({
    apiUrl: "https://api.example.com/v1/",
    development: false,
  });

  assert.match(csp, /connect-src 'self' https:\/\/api\.example\.com(?:;|$)/);
  assert.doesNotMatch(csp, /unsafe-eval|ws:\/\/localhost:\*/);
  assert.match(csp, /frame-ancestors 'none'/);
  assert.match(csp, /media-src 'self' data: blob:/);
});

test("same-origin no agrega fuentes de red innecesarias", () => {
  assert.equal(apiOriginForCsp(""), null);
  assert.equal(apiOriginForCsp("/api"), null);
  assert.match(
    buildContentSecurityPolicy({ apiUrl: "", development: false }),
    /connect-src 'self'(?:;|$)/,
  );
});

test("el modo desarrollo limita el wildcard a WebSocket local para HMR", () => {
  const csp = buildContentSecurityPolicy({ apiUrl: "", development: true });
  assert.match(csp, /'unsafe-eval'/);
  assert.match(csp, /ws:\/\/localhost:\*/);
  assert.doesNotMatch(csp, /https?:\/\/\*/);
});

test("rechaza esquemas y credenciales no aptos para connect-src", () => {
  assert.throws(() => apiOriginForCsp("javascript:alert(1)"), /http\(s\)/);
  assert.throws(() => apiOriginForCsp("https://user:secret@example.com"), /credenciales/);
});

test("publica el conjunto completo de headers defensivos", () => {
  const headers = new Map(
    buildSecurityHeaders({ apiUrl: "", development: false }).map(({ key, value }) => [key, value]),
  );

  assert.equal(headers.get("X-Content-Type-Options"), "nosniff");
  assert.equal(headers.get("X-Frame-Options"), "DENY");
  assert.equal(headers.get("Referrer-Policy"), "strict-origin-when-cross-origin");
  assert.match(headers.get("Permissions-Policy"), /microphone=\(self\)/);
  assert.match(headers.get("Content-Security-Policy"), /object-src 'none'/);
});
