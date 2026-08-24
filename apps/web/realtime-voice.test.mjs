import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  bytesToBase64,
  encodeRealtimeAudioFrame,
  normalizeRealtimeAudioMime,
  parseRealtimeVoiceEvent,
  REALTIME_VOICE_PATH,
  REALTIME_VOICE_PROTOCOL,
  realtimeVoiceUrl,
} from "./src/lib/realtime-voice.ts";

test("decodifica audio y transcript como el cliente iOS", () => {
  const encoded = bytesToBase64(Uint8Array.from([1, 2, 3]));
  const evento = parseRealtimeVoiceEvent({
    type: "audio",
    turn_id: 3,
    sequence: 2,
    mime: "audio/wav",
    data: encoded,
    state: "speaking",
  });

  assert.equal(evento.type, "audio");
  assert.equal(evento.turnId, 3);
  assert.equal(evento.sequence, 2);
  assert.deepEqual(Array.from(evento.audio ?? []), [1, 2, 3]);
  assert.equal(evento.state, "speaking");
  assert.equal(evento.text, null);
});

test("evento sin audio no inventa bytes", () => {
  const evento = parseRealtimeVoiceEvent({
    type: "transcript",
    text: "hola",
    language: "es",
  });

  assert.equal(evento.type, "transcript");
  assert.equal(evento.text, "hola");
  assert.equal(evento.language, "es");
  assert.equal(evento.audio, null);
});

test("data inválida no inventa bytes y el tipo cae a unknown", () => {
  const invalid = parseRealtimeVoiceEvent({ type: "audio", data: "@@@not-base64@@@" });
  assert.equal(invalid.type, "audio");
  assert.equal(invalid.audio, null);

  const unknown = parseRealtimeVoiceEvent("no-json");
  assert.equal(unknown.type, "unknown");
});

test("arma el frame de audio y la URL ws/wss sin token", () => {
  const frame = encodeRealtimeAudioFrame(Uint8Array.from([9, 8]), "audio/webm");
  assert.deepEqual(frame, {
    type: "audio",
    mime: "audio/webm",
    data: bytesToBase64(Uint8Array.from([9, 8])),
  });
  assert.equal(realtimeVoiceUrl("http://localhost:8000"), `ws://localhost:8000${REALTIME_VOICE_PATH}`);
  assert.equal(realtimeVoiceUrl("https://api.edecan.example"), `wss://api.edecan.example${REALTIME_VOICE_PATH}`);
  assert.equal(REALTIME_VOICE_PROTOCOL, "edecan.voice.realtime.v1");
  assert.equal(normalizeRealtimeAudioMime("audio/webm;codecs=opus"), "audio/webm");
  assert.equal(normalizeRealtimeAudioMime("audio/mp4"), null);
});

test("el chat web usa el cliente realtime en vez de solo transcribe/speak HTTP", () => {
  const page = readFileSync(new URL("./src/app/(app)/app/page.tsx", import.meta.url), "utf8");
  const always = readFileSync(new URL("./src/components/chat/AlwaysListenMode.tsx", import.meta.url), "utf8");
  const client = readFileSync(new URL("./src/lib/realtime-voice.ts", import.meta.url), "utf8");

  assert.match(page, /RealtimeVoiceClient/);
  assert.match(page, /type:\s*"interrupt"|interrupt\(/);
  assert.match(always, /RealtimeVoiceClient/);
  assert.match(always, /interrupt\(/);
  assert.match(client, /type: "authenticate"/);
  assert.match(client, /type: "commit"/);
  assert.match(client, /type: "speak"/);
});

test("el fallback HTTP de Escuchar reproduce chunks MPEG sin esperar el blob", () => {
  const page = readFileSync(new URL("./src/app/(app)/app/page.tsx", import.meta.url), "utf8");
  const always = readFileSync(new URL("./src/components/chat/AlwaysListenMode.tsx", import.meta.url), "utf8");
  const client = readFileSync(new URL("./src/lib/realtime-voice.ts", import.meta.url), "utf8");

  assert.match(page, /speakTextStream/);
  assert.match(page, /player\.append\(chunk, mime\)/);
  assert.doesNotMatch(page, /speakText\(/);
  assert.match(always, /speakTextStream/);
  assert.match(always, /player\.append\(chunk, mime\)/);
  assert.doesNotMatch(always, /speakText\(/);
  assert.match(client, /MediaSource\.isTypeSupported/);
  assert.match(client, /audio\/mpeg/);
});
