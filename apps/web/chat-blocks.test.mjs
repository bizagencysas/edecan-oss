import assert from "node:assert/strict";
import test from "node:test";

import {
  chatScreenPath,
  flightStopsLabel,
  messageBlocks,
  parseAgentEvent,
  parseChatBlock,
  publicHttpUrl,
  reduceToolTimeline,
  sourceModeLabel,
} from "./src/lib/chat-blocks.ts";

const FILE_ID = "11111111-1111-4111-a111-111111111111";

test("acepta solo URLs públicas HTTP(S) sin credenciales", () => {
  assert.equal(publicHttpUrl("https://example.com/oferta"), "https://example.com/oferta");
  assert.equal(publicHttpUrl("javascript:alert(1)"), null);
  assert.equal(publicHttpUrl("https://user:secret@example.com"), null);
  assert.equal(publicHttpUrl("http://localhost:3000/admin"), null);
  assert.equal(publicHttpUrl("http://192.168.1.9/private"), null);
  assert.equal(publicHttpUrl("http://203.0.113.4/example"), null);
  assert.equal(publicHttpUrl("http://[::1]/private"), null);
});

test("parsea bloques allowlisted y descarta acciones o versiones inseguras", () => {
  const block = parseChatBlock({
    schema_version: 1,
    type: "link_preview",
    url: "https://example.com/doc",
    title: "Documento",
    source_mode: "live",
    actions: [
      { id: "open", label: "Abrir", action: "open_url", url: "https://example.com/doc" },
      { id: "bad", label: "Interno", action: "open_url", url: "http://127.0.0.1:8000" },
      { id: "draft", label: "Continuar", action: "prefill_message", message: "Resume este documento" },
    ],
  });

  assert.equal(block?.type, "link_preview");
  assert.deepEqual(block?.actions.map((action) => action.action), ["open_url", "prefill_message"]);
  assert.equal(parseChatBlock({ schema_version: 2, type: "link_preview", url: "https://example.com", title: "Futuro" }), null);
  assert.equal(parseChatBlock({ schema_version: 1, type: "html", html: "<script>alert(1)</script>" }), null);
});

test("valida media privada por referencia opaca y limpia el nombre persistido", () => {
  const block = parseChatBlock({
    schema_version: 1,
    type: "media",
    media_kind: "image",
    artifact: { file_id: FILE_ID, filename: "../reporte.png", mime: "image/png" },
    alt: "Gráfico mensual",
  });
  assert.equal(block?.type, "media");
  if (block?.type === "media") {
    assert.equal(block.artifact.filename, "reporte.png");
    assert.equal(block.artifact.file_id, FILE_ID);
  }
  assert.equal(
    parseChatBlock({
      schema_version: 1,
      type: "media",
      media_kind: "image",
      artifact: { file_id: "../../etc/passwd", filename: "x.png" },
    }),
    null,
  );
});

test("recupera bloques de tool_calls persistidos, deduplica y respeta blocks_version", () => {
  const flight = {
    schema_version: 1,
    type: "flight",
    offer_id: "offer-1",
    airline: "Avianca",
    origin: "BOG",
    destination: "MIA",
    stops: 0,
    price: "420.00",
    currency: "USD",
    source_mode: "live",
  };
  const calls = [
    { type: "tool_start", tool_call_id: "call-a", name: "buscar_vuelos", args: {} },
    { type: "tool_end", tool_call_id: "call-a", name: "buscar_vuelos", blocks_version: 1, blocks: [flight] },
    { type: "tool_end", tool_call_id: "call-b", name: "buscar_vuelos", blocks_version: 1, blocks: [flight] },
    { type: "tool_end", tool_call_id: "future", name: "buscar_vuelos", blocks_version: 2, blocks: [flight] },
  ];
  const blocks = messageBlocks(calls);
  assert.equal(blocks.length, 1);
  assert.equal(blocks[0].type, "flight");
});

test("valida frames SSE y neutraliza bloques de una versión futura", () => {
  const event = parseAgentEvent({
    type: "tool_end",
    tool_call_id: "call-9",
    name: "navegar_web",
    result_preview: "Listo",
    artifacts: [],
    blocks_version: 2,
    blocks: [{ schema_version: 1, type: "link_preview", url: "https://example.com", title: "Ejemplo" }],
  });
  assert.equal(event?.type, "tool_end");
  if (event?.type === "tool_end") {
    assert.equal(event.tool_call_id, "call-9");
    assert.deepEqual(event.blocks, []);
  }
  assert.equal(parseAgentEvent({ type: "tool_start", name: "x", args: [] }), null);
  assert.equal(parseAgentEvent({ type: "made_up", payload: "ignored" }), null);
});

test("correlaciona llamadas homónimas por tool_call_id aunque terminen fuera de orden", () => {
  let timeline = [];
  timeline = reduceToolTimeline(timeline, {
    type: "tool_start",
    tool_call_id: "first",
    name: "buscar_web",
    args: { q: "uno" },
  });
  timeline = reduceToolTimeline(timeline, {
    type: "tool_start",
    tool_call_id: "second",
    name: "buscar_web",
    args: { q: "dos" },
  });
  timeline = reduceToolTimeline(timeline, {
    type: "tool_end",
    tool_call_id: "second",
    name: "buscar_web",
    result_preview: "segundo listo",
    artifacts: [],
    blocks_version: 1,
    blocks: [],
  });

  assert.equal(timeline[0].status, "running");
  assert.equal(timeline[1].status, "done");
  assert.equal(timeline[1].resultPreview, "segundo listo");
});

test("helpers de presentación producen destinos y etiquetas humanas", () => {
  assert.equal(chatScreenPath("travel"), "/app/viajes");
  assert.equal(chatScreenPath("assistant"), "/app");
  assert.equal(flightStopsLabel(0), "Directo");
  assert.equal(flightStopsLabel(1), "1 escala");
  assert.equal(flightStopsLabel(2), "2 escalas");
  assert.equal(sourceModeLabel("demo"), "Demostración");
  assert.equal(sourceModeLabel("live"), "Datos en vivo");
});
