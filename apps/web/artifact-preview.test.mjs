import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("./src/components/chat/ArtifactLinks.tsx", import.meta.url), "utf8");

test("los artefactos visuales se abren dentro de Edecan y conservan la descarga", () => {
  assert.match(source, /Vista previa/);
  assert.match(source, /Descargar \{artifact\.filename\}/);
  assert.match(source, /application\/pdf/);
  assert.match(source, /text\/html/);
  assert.match(source, /mime\.startsWith\("image\/"\)/);
});

test("la vista HTML queda aislada sin scripts, origen compartido ni referer", () => {
  assert.match(source, /sandbox=""/);
  assert.doesNotMatch(source, /allow-scripts/);
  assert.doesNotMatch(source, /allow-same-origin/);
  assert.match(source, /referrerPolicy="no-referrer"/);
  assert.match(source, /default-src 'none'/);
  assert.match(source, /connect-src 'none'/);
  assert.match(source, /isolatedHtmlBlob/);
  assert.match(source, /URL\.revokeObjectURL/);
});

test("el modal es accesible, responsive y puede cerrarse con Escape", () => {
  assert.match(source, /role="dialog"/);
  assert.match(source, /aria-modal="true"/);
  assert.match(source, /event\.key === "Escape"/);
  assert.match(source, /90dvh/);
});
