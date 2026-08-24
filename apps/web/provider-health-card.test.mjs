import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";

const apiSource = fs.readFileSync(new URL("./src/lib/api.ts", import.meta.url), "utf8");
const componentSource = fs.readFileSync(
  new URL("./src/components/configuracion/ProviderHealthCard.tsx", import.meta.url),
  "utf8",
);

test("la tarjeta de salud consume el endpoint real y explica su alcance", () => {
  assert.match(apiSource, /\/v1\/health\/providers/);
  assert.match(componentSource, /Diagnóstico agregado/);
  assert.match(componentSource, /Este historial se reinicia/);
  assert.doesNotMatch(componentSource, /getAccessToken|Authorization/);
});
