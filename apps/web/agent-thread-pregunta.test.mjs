import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

import ts from "typescript";

/**
 * Espejo de `agent-thread-plan.test.mjs` para la capacidad nueva del
 * encargo ("que la IA me hable"): `agent_question`, el evento de sesión que
 * el companion produce cuando opencode pausa un turno a preguntar algo
 * (`question.v2.asked` -> `ide_opencode_eventos.py::traducir_pregunta`).
 * Mismo motivo para extraer la función pura en vez de importar el módulo
 * JSX directo -- ver el docstring de `agent-thread-plan.test.mjs`.
 */
const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");
const fuente = read("./src/components/ide/AgentThread.tsx");

const sourceFile = ts.createSourceFile(
  "AgentThread.tsx",
  fuente,
  ts.ScriptTarget.ES2020,
  true,
  ts.ScriptKind.TSX,
);

function extraerFuncion(nombre) {
  let encontrada;
  sourceFile.forEachChild((node) => {
    if (encontrada) return;
    if (ts.isFunctionDeclaration(node) && node.name?.text === nombre) encontrada = node;
  });
  assert.ok(encontrada, `no se encontró "function ${nombre}" en AgentThread.tsx`);
  return fuente.slice(encontrada.getStart(sourceFile), encontrada.getEnd());
}

const codigoTs = ["extraerPreguntaPendiente"].map(extraerFuncion).join("\n\n");

const { outputText } = ts.transpileModule(codigoTs, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
});

new vm.Script(outputText, { filename: "agent-thread-pregunta-funcion-pura.js" }).runInThisContext();

const { extraerPreguntaPendiente } = globalThis;
assert.equal(typeof extraerPreguntaPendiente, "function", "extraerPreguntaPendiente no se extrajo como función");

function evento(cursor, type, text) {
  return { cursor, type, text, stream: null, timestamp: "2026-07-30T10:00:00Z" };
}

/** Mismo payload exacto que produce `ide_opencode_eventos.py::traducir_pregunta` en el companion. */
function agentQuestionJson(overrides = {}) {
  return JSON.stringify({
    request_id: "req-1",
    session_id: "sess-1",
    questions: [
      {
        question: "¿En qué puerto corro el servidor de pruebas?",
        header: "Puerto",
        options: [
          { label: "3000", description: "El de siempre" },
          { label: "8080", description: "Si 3000 ya está ocupado" },
        ],
        multiple: false,
        custom: true,
      },
    ],
    ...overrides,
  });
}

test("un turno que acaba en agent_question se reconoce como pregunta pendiente", () => {
  const events = [
    evento(1, "user", "corre el servidor de pruebas"),
    evento(2, "status", "Agente de Workers AI iniciado."),
    evento(3, "agent_question", agentQuestionJson()),
  ];
  const pregunta = extraerPreguntaPendiente(events);
  assert.ok(pregunta, "la pregunta pendiente debía reconocerse");
  assert.equal(pregunta.cursor, 3);
  assert.equal(pregunta.pregunta.request_id, "req-1");
  assert.equal(pregunta.pregunta.session_id, "sess-1");
  assert.equal(pregunta.pregunta.questions.length, 1);
  assert.equal(pregunta.pregunta.questions[0].question, "¿En qué puerto corro el servidor de pruebas?");
  assert.deepEqual(
    pregunta.pregunta.questions[0].options.map((opcion) => opcion.label),
    ["3000", "8080"],
  );
});

test("un turno en curso no reclama pregunta -- ese cálculo ni se hace en la pantalla real (`!live` en TurnRow)", () => {
  // La función en sí no mira `live` (esa decisión vive en `TurnRow`); esto
  // documenta que, aun con el evento presente, la función sigue devolviendo
  // la pregunta -- es la pantalla real la que no la consulta mientras el
  // turno sigue vivo.
  const events = [evento(1, "user", "corre el servidor"), evento(2, "agent_question", agentQuestionJson())];
  assert.ok(extraerPreguntaPendiente(events));
});

test("una pregunta con varias questions conserva el orden de todas", () => {
  const events = [
    evento(1, "user", "prepara el despliegue"),
    evento(
      2,
      "agent_question",
      JSON.stringify({
        request_id: "req-2",
        session_id: "sess-1",
        questions: [
          { question: "¿Ambiente?", header: "Ambiente", options: [], multiple: false, custom: true },
          {
            question: "¿Notificar al equipo?",
            header: "Notificación",
            options: [
              { label: "Sí", description: "" },
              { label: "No", description: "" },
            ],
            multiple: false,
            custom: false,
          },
        ],
      }),
    ),
  ];
  const pregunta = extraerPreguntaPendiente(events);
  assert.ok(pregunta);
  assert.equal(pregunta.pregunta.questions.length, 2);
  assert.equal(pregunta.pregunta.questions[0].question, "¿Ambiente?");
  assert.equal(pregunta.pregunta.questions[1].question, "¿Notificar al equipo?");
});

test("un agent_question con JSON dañado no revienta: se ignora y se sigue buscando", () => {
  const events = [evento(1, "user", "corre el servidor"), evento(2, "agent_question", "{no es json")];
  assert.equal(extraerPreguntaPendiente(events), null);
});

test("un agent_question sin los campos esperados tampoco se pinta a medias", () => {
  const events = [
    evento(1, "user", "corre el servidor"),
    evento(2, "agent_question", JSON.stringify({ request_id: "req-1" })),
  ];
  assert.equal(extraerPreguntaPendiente(events), null);
});

test("un agent_question con questions vacío tampoco se pinta a medias", () => {
  const events = [
    evento(1, "user", "corre el servidor"),
    evento(
      2,
      "agent_question",
      JSON.stringify({ request_id: "req-1", session_id: "sess-1", questions: [] }),
    ),
  ];
  assert.equal(extraerPreguntaPendiente(events), null);
});

test("sin ningún agent_question, no hay nada que reconocer", () => {
  const events = [evento(1, "user", "arregla el bug"), evento(2, "tool", "editando archivo.py")];
  assert.equal(extraerPreguntaPendiente(events), null);
});

// --- La pantalla real: que el bloque de primera clase y sus acciones estén cableados ---

test("la pregunta pendiente se pinta como bloque propio, con responder/rechazar conectados", () => {
  assert.match(fuente, /preguntaPendiente \? \(/);
  assert.match(
    fuente,
    /<AgentQuestionCard sessionId=\{sessionId\} pregunta=\{preguntaPendiente\.pregunta\} \/>/,
  );
  assert.match(fuente, /answerIdeAgentQuestion\(/);
  assert.match(fuente, /rejectIdeAgentQuestion\(/);
  // El plan pendiente sigue ganando si por alguna razón coinciden con una
  // pregunta en el mismo turno, y el mensaje genérico queda como último
  // recurso, después de las dos.
  const ordenPlan = fuente.indexOf("planPendiente ? (");
  const ordenPregunta = fuente.indexOf("preguntaPendiente ? (");
  const ordenGenerico = fuente.indexOf("Este turno no dejó una respuesta de texto.", ordenPregunta);
  assert.ok(ordenPlan > 0, "no se encontró la rama de planPendiente");
  assert.ok(ordenPregunta > ordenPlan, "preguntaPendiente debe evaluarse después de planPendiente");
  assert.ok(ordenGenerico > ordenPregunta, "el mensaje genérico debe quedar después de preguntaPendiente");
});

const apiIde = read("./src/lib/api-ide.ts");

// Este test fijaba `jsonBody({ answers })` -- una lista plana. Pero el
// companion (`ide_agent_question_answer`, ide_runtime.py) valida que
// «respuestas» sea una lista DE LISTAS, una por pregunta, porque opencode
// admite preguntas de opción múltiple. El test verde estaba clavando un
// contrato que el backend habría rechazado en la primera respuesta real: el
// endpoint REST no existía todavía, así que nadie lo descubrió. Ahora fija el
// contrato de verdad.
test("los dos clientes de pregunta siguen la forma real de los endpoints (mismo patrón que plan/mcp)", () => {
  assert.match(
    apiIde,
    /\/question\/\$\{encodeURIComponent\(requestId\)\}\/reply`,\s*\{ method: "POST", \.\.\.jsonBody\(\{ respuestas: answers\.map\(\(respuesta\) => \[respuesta\]\) \}\) \}/,
  );
  assert.match(apiIde, /\/question\/\$\{encodeURIComponent\(requestId\)\}\/reject`,\s*\{ method: "POST" \}/);
});

// --- Permisos: el freno de Manual / Aceptar ediciones, con su salida --------

test("existe el cliente para conceder o denegar un permiso pendiente", () => {
  assert.match(apiIde, /export async function answerIdeAgentPermission/);
  assert.match(apiIde, /\/permission\/\$\{encodeURIComponent\(requestId\)\}\/answer`/);
  assert.match(apiIde, /export async function listIdeAgentPermissions/);
});

test("la tarjeta de permiso se pinta y compite por el mismo sitio que plan y pregunta", () => {
  const hilo = read("./src/components/ide/AgentThread.tsx");
  assert.match(hilo, /function AgentPermissionCard/);
  assert.match(hilo, /<AgentPermissionCard sessionId=\{sessionId\} permiso=\{permisoPendiente\.permiso\}/);
  // Sin esto el modo Manual sería una trampa: el turno para y no hay con qué
  // dejarlo seguir.
  assert.match(hilo, /answerIdeAgentPermission\(sessionId, permiso\.request_id/);
});

test("recordar un permiso solo se ofrece si opencode declara que esa solicitud lo admite", () => {
  const hilo = read("./src/components/ide/AgentThread.tsx");
  assert.match(hilo, /permiso\.puede_recordar === true/);
  // Y aunque la casilla esté marcada, no se manda si la solicitud no lo admite.
  assert.match(hilo, /recordar: conceder && recordar && permiso\.puede_recordar === true/);
});
