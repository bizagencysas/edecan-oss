import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

function source(path) {
  return readFileSync(new URL(path, import.meta.url), "utf8");
}

test("las muestras de voz se sintetizan por el backend autenticado", () => {
  const voices = source("./src/components/voz/VocesTab.tsx");

  assert.match(voices, /speakText\(/);
  assert.match(voices, /voz\.voice_id/);
  assert.match(voices, /URL\.createObjectURL/);
  assert.match(voices, /Escuchar/);
  assert.match(voices, /Detener/);
  assert.doesNotMatch(voices, /<audio controls src=\{voz\.preview_url\}/);
});

test("la vista previa muestra el error real en vez del error nativo del reproductor", () => {
  const voices = source("./src/components/voz/VocesTab.tsx");

  assert.match(voices, /previewErrors/);
  assert.match(voices, /err instanceof ApiError/);
  assert.match(voices, /Revisa la conexión de ElevenLabs/);
});
