import assert from "node:assert/strict";
import test from "node:test";

import {
  SPEECH_RECOGNITION_LOCALE,
  normalizeWakePhrase,
  transcriptContainsWakePhrase,
  transcriptRequestsSleep,
} from "./src/lib/wake-word-detection.ts";

test("usa español de Venezuela y normaliza acentos", () => {
  assert.equal(SPEECH_RECOGNITION_LOCALE, "es-VE");
  assert.equal(normalizeWakePhrase("  ¡Árbol!  "), "arbol");
});

test("reconoce la frase exacta y dictados que separan Edecán", () => {
  assert.equal(transcriptContainsWakePhrase("Oye Edecán, organiza esto", "Oye Edecán"), true);
  assert.equal(transcriptContainsWakePhrase("oye de can organiza esto", "Oye Edecán"), true);
  assert.equal(transcriptContainsWakePhrase("oye edekán organiza esto", "Oye Edecán"), true);
});

test("una palabra personalizada corta exige coincidencia real", () => {
  assert.equal(transcriptContainsWakePhrase("Sol, revisa el correo", "Sol"), true);
  assert.equal(transcriptContainsWakePhrase("sal, revisa el correo", "Sol"), false);
  assert.equal(transcriptContainsWakePhrase("ruido de fondo", ""), false);
});

test("la conversación continua solo duerme con una orden completa", () => {
  assert.equal(transcriptRequestsSleep("Descansa, Edecán."), true);
  assert.equal(transcriptRequestsSleep("puedes dormir"), true);
  assert.equal(transcriptRequestsSleep("deja de escuchar Edecán"), true);
  assert.equal(transcriptRequestsSleep("recuérdame dormir a las diez"), false);
  assert.equal(transcriptRequestsSleep("¿puedes dormir poco?"), false);
});
