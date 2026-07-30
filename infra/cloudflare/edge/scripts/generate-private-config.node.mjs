import assert from "node:assert/strict";
import { test } from "node:test";

import {
  buildPrivateConfig,
  normalizeDomain
} from "./generate-private-config.mjs";

const BASE_CONFIG = Object.freeze({
  name: "edecan-edge",
  main: "src/index.ts",
  env: {
    staging: {
      name: "edecan-edge-staging",
      vars: { ENVIRONMENT: "staging" }
    },
    production: {
      name: "edecan-edge-production",
      vars: { ENVIRONMENT: "production" }
    }
  }
});

test("normaliza un hostname DNS sin convertirlo en URL pública", () => {
  assert.equal(normalizeDomain(" Edge.Private.Example "), "edge.private.example");
});

test("rechaza protocolos, rutas, puertos y hostnames locales", () => {
  for (const value of [
    "",
    "https://edge.example.com",
    "edge.example.com/path",
    "edge.example.com:443",
    "localhost",
    "single-label",
    "edge..example.com"
  ]) {
    assert.throws(() => normalizeDomain(value));
  }
});

test("inyecta la ruta solo en el entorno solicitado", () => {
  const configured = buildPrivateConfig(
    structuredClone(BASE_CONFIG),
    "production",
    "edge.example.com"
  );

  assert.deepEqual(configured.env.production.routes, [
    { pattern: "edge.example.com", custom_domain: true }
  ]);
  assert.equal(configured.env.staging.routes, undefined);
  assert.equal(BASE_CONFIG.env.production.routes, undefined);
});

test("rechaza entornos que no estén declarados", () => {
  assert.throws(
    () => buildPrivateConfig(structuredClone(BASE_CONFIG), "preview", "edge.example.com"),
    /entorno no admitido/
  );
});
