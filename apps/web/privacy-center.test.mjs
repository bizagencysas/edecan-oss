import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";

const apiSource = fs.readFileSync(new URL("./src/lib/api.ts", import.meta.url), "utf8");
const componentSource = fs.readFileSync(
  new URL("./src/components/configuracion/PrivacyCenter.tsx", import.meta.url),
  "utf8",
);

test("Privacy Center usa endpoints autenticados y exige confirmación para acciones destructivas", () => {
  assert.match(apiSource, /\/v1\/privacy\/export/);
  assert.match(apiSource, /\/v1\/memory/);
  assert.match(componentSource, /erase_account\.available/);
  assert.match(componentSource, /ELIMINAR MI CUENTA/);
  assert.match(apiSource, /\/v1\/privacy\/account/);
  assert.match(apiSource, /\/v1\/privacy\/account\/preflight/);
  assert.doesNotMatch(componentSource, /window\.confirm/);
});
