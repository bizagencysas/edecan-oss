import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";

const page = fs.readFileSync(
  new URL("./src/app/(app)/app/misiones/page.tsx", import.meta.url),
  "utf8",
);
const api = fs.readFileSync(new URL("./src/lib/api-misiones.ts", import.meta.url), "utf8");

test("Misiones permite descargar el manifiesto seguro sin ejecutar la misión", () => {
  assert.match(api, /getMissionReproduction/);
  assert.match(page, /getMissionReproduction\(selectedId\)/);
  assert.match(page, /sin ejecutar la misión/);
  assert.match(page, /application\/json/);
});

