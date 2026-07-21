import assert from "node:assert/strict";
import test from "node:test";

import {
  SPEECH_RECOGNITION_LOCALE,
  normalizeWakePhrase,
  transcriptContainsWakePhrase,
} from "./src/lib/wake-word-detection.ts";

test("usa español de Venezuela y normaliza acentos", () => {
  assert.equal(SPEECH_RECOGNITION_LOCALE, "es-VE");
  assert.equal(normalizeWakePhrase("  ¡Tío!  "), "tio");
});

test("reconoce la frase exacta y dictados que separan Edecán", () => {
  assert.equal(transcriptContainsWakePhrase("Oye Edecán, organiza esto", "Oye Edecán"), true);
  assert.equal(transcriptContainsWakePhrase("oye de can organiza esto", "Oye Edecán"), true);
  assert.equal(transcriptContainsWakePhrase("oye edekán organiza esto", "Oye Edecán"), true);
});

test("una palabra personalizada corta exige coincidencia real", () => {
  assert.equal(transcriptContainsWakePhrase("Tío, revisa el correo", "Tío"), true);
  assert.equal(transcriptContainsWakePhrase("mío, revisa el correo", "Tío"), false);
  assert.equal(transcriptContainsWakePhrase("ruido de fondo", ""), false);
});
