import assert from "node:assert/strict";
import test from "node:test";

import { SseDataParser } from "./src/lib/sse.ts";

test("parsea CRLF aunque el separador llegue partido entre chunks", () => {
  const parser = new SseDataParser();
  assert.deepEqual(parser.push("event: message.done\r\ndata: {\"type\":\"done\",\"usage\":{}}\r"), []);
  assert.deepEqual(parser.push("\n\r\n"), ['{"type":"done","usage":{}}']);
});

test("une múltiples líneas data según el contrato SSE", () => {
  const parser = new SseDataParser();
  const payloads = parser.push(
    'event: message.done\r\ndata: {"type":\r\ndata: "done", "usage": {}}\r\n\r\n',
  );
  assert.deepEqual(payloads, ['{"type":\n"done", "usage": {}}']);
  assert.deepEqual(JSON.parse(payloads[0]), { type: "done", usage: {} });
});

test("entrega varios frames e ignora comentarios o eventos sin data", () => {
  const parser = new SseDataParser();
  assert.deepEqual(
    parser.push(': keepalive\n\nevent: tool.start\ndata: one\n\nevent: empty\n\nevent: tool.end\ndata: two\n\n'),
    ["one", "two"],
  );
});

test("hace flush del último frame cuando EOF llega sin línea vacía", () => {
  const parser = new SseDataParser();
  assert.deepEqual(parser.push('data: {"type":"error",'), []);
  assert.deepEqual(parser.push('"message":"fin"}', true), ['{"type":"error","message":"fin"}']);
});
