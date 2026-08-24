import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

import ts from "typescript";

/**
 * `AgentThread.tsx` es JSX: el "type stripping" nativo de Node (usado por
 * `test-register.mjs` para los demás `.test.mjs`, que solo resuelven `.ts`
 * sin JSX) no lo puede importar directo. En vez de conformarse con asserts
 * de texto sobre el archivo (el patrón de `ide-cola.test.mjs` para los
 * componentes vecinos), este test extrae las funciones puras que decían
 * QUÉ mostrar cuando un turno cierra sin `assistant_final` -- ninguna usa
 * JSX -- y las corre de verdad: se les quitan los tipos con el compilador
 * de TypeScript (ya es una dependencia del repo, solo transpila, no
 * resuelve módulos) y se evalúan en un contexto aparte. Así el test
 * ejercita el código real del componente, no una reimplementación que
 * podría desincronizarse en silencio.
 */
const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");
const fuente = read("./src/components/ide/AgentThread.tsx");

// Un contador de llaves a mano se rompe apenas una firma trae un tipo de
// retorno con forma de objeto (`): { cursor: number; texto: string } | null {`
// -- la PRIMERA "{" que encuentra es la del TIPO, no la del cuerpo, y todo
// lo de después queda cortado. El propio compilador de TypeScript ya sabe
// distinguir eso: se parsea el archivo una vez (como `.tsx`, aunque estas
// funciones puntuales no usen JSX) y se recorta por los límites reales del
// nodo `FunctionDeclaration`.
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

// Orden de dependencia: `pickFinalEvent` llama a `looksLikeTechnicalText`.
const codigoTs = [
  "looksLikeTechnicalText",
  "pickFinalEvent",
  "planYaResuelto",
  "extraerPlanPendiente",
  "cierreSinRespuesta",
  "textoDeCierreLegible",
]
  .map(extraerFuncion)
  .join("\n\n");

const { outputText } = ts.transpileModule(codigoTs, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
});

// `runInThisContext` (no `createContext`) a propósito: comparar un array
// construido en un sandbox de otro "realm" contra un literal de este test
// falla `assert.deepEqual` aunque el contenido sea idéntico (`Array` de un
// realm no es el mismo constructor que el de otro). Evaluar en ESTE
// contexto es más simple y sigue sin tocar nada del módulo real -- solo
// declara las funciones extraídas como globals de este proceso de test.
new vm.Script(outputText, { filename: "agent-thread-funciones-puras.js" }).runInThisContext();

const {
  pickFinalEvent,
  planYaResuelto,
  extraerPlanPendiente,
  cierreSinRespuesta,
  textoDeCierreLegible,
} = globalThis;

for (const [nombre, fn] of Object.entries({
  pickFinalEvent,
  planYaResuelto,
  extraerPlanPendiente,
  cierreSinRespuesta,
  textoDeCierreLegible,
})) {
  assert.equal(typeof fn, "function", `${nombre} no se extrajo como función`);
}

/** Un evento mínimo, con los campos que estas funciones de verdad miran. */
function evento(cursor, type, text) {
  return { cursor, type, text, stream: null, timestamp: "2026-07-30T10:00:00Z" };
}

function planProposedJson(overrides = {}) {
  return JSON.stringify({
    plan: {
      id: "plan-1",
      goal: "Reestructurar el README",
      steps: [{ description: "Dividir en secciones" }, { description: "Actualizar el índice" }],
      ...overrides,
    },
  });
}

// --- El fallo real: un turno en plan_proposed no puede leerse como "sin respuesta" ---

test("un turno que acaba en plan_proposed no produce el mensaje de sin respuesta de texto", () => {
  const events = [
    evento(1, "user", "reestructura el README"),
    evento(2, "status", "Agente de Workers AI iniciado."),
    evento(3, "plan_proposed", planProposedJson()),
  ];
  // El primer eslabón del bug real: `pickFinalEvent` no puede inventar una
  // respuesta que no existe.
  assert.equal(pickFinalEvent(events, false), undefined);
  // Pero el turno SÍ tiene algo que mostrar antes de caer al mensaje
  // genérico: el plan sigue pendiente de una decisión.
  const plan = extraerPlanPendiente(events);
  assert.ok(plan, "el plan pendiente debía reconocerse");
  assert.equal(plan.meta, "Reestructurar el README");
  assert.deepEqual(
    plan.pasos.map((paso) => paso.descripcion),
    ["Dividir en secciones", "Actualizar el índice"],
  );
  assert.equal(plan.cursor, 3);
  // Y como hay plan pendiente, el cierre "sin respuesta" ni se busca en la
  // pantalla real (ver el `&& !planPendiente` en `TurnRow`) -- acá basta con
  // que este turno no tenga ninguna de las dos marcas de cierre por decisión.
  assert.equal(cierreSinRespuesta(events), null);
});

test("un turno en curso (todavía viable) no reclama un plan ni un cierre", () => {
  const events = [evento(1, "user", "reestructura el README"), evento(2, "status", "Agente iniciado.")];
  // Con el turno vivo, ni `pickFinalEvent` ni el resto deciden nada: gana
  // `live` en la pantalla real (`ThinkingRow`), esto solo confirma que no hay
  // una respuesta prematura que mostrar.
  assert.equal(pickFinalEvent(events, true), undefined);
});

// --- El plan se resuelve DENTRO del mismo turno (mismo `events[]`, sin nuevo `user`) ---

test("un plan aprobado dentro del turno deja de pintarse como tarjeta pendiente", () => {
  const events = [
    evento(1, "user", "reestructura el README"),
    evento(2, "status", "Agente de Workers AI iniciado."),
    evento(3, "plan_proposed", planProposedJson()),
    evento(4, "status", "Plan aprobado: «Reestructurar el README» (2 paso(s))."),
    evento(5, "plan_step", "[Dividir en secciones] hecho: ok"),
    evento(6, "assistant_final", "Listo: el README quedó dividido en secciones."),
  ];
  assert.equal(extraerPlanPendiente(events), null);
  const final = pickFinalEvent(events, false);
  assert.equal(final?.text, "Listo: el README quedó dividido en secciones.");
});

test("un plan rechazado dentro del turno se lee como cierre por decisión, no como plan pendiente", () => {
  const events = [
    evento(1, "user", "reestructura el README"),
    evento(2, "status", "Agente de Workers AI iniciado."),
    evento(3, "plan_proposed", planProposedJson()),
    evento(4, "status", "Plan rechazado por la persona; no se ejecutó ningún paso."),
  ];
  assert.equal(extraerPlanPendiente(events), null);
  const cierre = cierreSinRespuesta(events);
  assert.ok(cierre);
  assert.equal(cierre.texto, "Plan rechazado por la persona; no se ejecutó ningún paso.");
  assert.equal(
    textoDeCierreLegible(cierre.texto),
    "Rechazaste el plan propuesto: no se ejecutó ningún paso.",
  );
});

// --- Otros cierres sin `assistant_final` que tampoco son "no pasó nada" ------

test("cancelar el turno se distingue del mensaje genérico de sin respuesta", () => {
  const events = [
    evento(1, "user", "corre los tests"),
    evento(2, "command", "npm test"),
    evento(3, "status", "Sesión cancelada."),
  ];
  assert.equal(pickFinalEvent(events, false), undefined);
  assert.equal(extraerPlanPendiente(events), null);
  const cierre = cierreSinRespuesta(events);
  assert.ok(cierre);
  assert.equal(textoDeCierreLegible(cierre.texto), "Detuviste este turno antes de que terminara.");
});

test("sin ninguna marca de cierre, el mensaje genérico sigue siendo honesto", () => {
  // El caso de una sesión "interrupted" (companion reiniciado a media
  // corrida, `ide_sessions.py::_load`): no queda NINGÚN evento que lo cuente,
  // así que esto es lo único que le queda a la pantalla -- y acá sí aplica.
  const events = [evento(1, "user", "arregla el bug"), evento(2, "tool", "editando archivo.py")];
  assert.equal(pickFinalEvent(events, false), undefined);
  assert.equal(extraerPlanPendiente(events), null);
  assert.equal(cierreSinRespuesta(events), null);
});

test("un plan_proposed con JSON dañado no revienta: se ignora y se sigue buscando", () => {
  const events = [
    evento(1, "user", "reestructura el README"),
    evento(2, "plan_proposed", "{no es json"),
  ];
  assert.equal(extraerPlanPendiente(events), null);
});

test("un plan_proposed sin los campos esperados tampoco se pinta a medias", () => {
  const events = [
    evento(1, "user", "reestructura el README"),
    evento(2, "plan_proposed", JSON.stringify({ plan: { goal: "sin id ni steps" } })),
  ];
  assert.equal(extraerPlanPendiente(events), null);
});

// --- La pantalla real: que el bloque de primera clase y sus acciones estén cableados ---

test("el plan pendiente se pinta como bloque propio, con las tres acciones reales conectadas", () => {
  assert.match(fuente, /planPendiente \? \(/);
  assert.match(fuente, /<PlanPropuestoCard sessionId=\{sessionId\} plan=\{planPendiente\} \/>/);
  assert.match(fuente, /approveIdeAgentPlan\(/);
  assert.match(fuente, /editIdeAgentPlan\(/);
  assert.match(fuente, /rejectIdeAgentPlan\(/);
  // El mensaje genérico queda como el ÚLTIMO recurso de la cadena, después
  // del plan pendiente y del cierre por decisión -- si algún día alguien lo
  // sube de orden, este test lo nota. Se busca la ocurrencia del JSX real
  // (después de `ordenCierre`), no la mención del mismo texto en el
  // docstring del módulo, que aparece antes.
  const ordenPlan = fuente.indexOf("planPendiente ? (");
  const ordenCierre = fuente.indexOf("cierre ? (", ordenPlan);
  const ordenGenerico = fuente.indexOf("Este turno no dejó una respuesta de texto.", ordenCierre);
  assert.ok(ordenPlan > 0, "no se encontró la rama de planPendiente");
  assert.ok(ordenCierre > ordenPlan, "no se encontró la rama de cierre después de planPendiente");
  assert.ok(ordenGenerico > ordenCierre, "el mensaje genérico debe quedar después de las otras dos ramas");
});

test("aprobar el plan pide confirmación antes de mandar la aprobación real", () => {
  // Punto 2 del encargo: aprobar reparte el trabajo entre subagentes que
  // escriben archivos, así que no puede ser un solo clic.
  assert.match(fuente, /confirmando === "aprobar"/);
  assert.match(fuente, /Esto reparte los pasos entre subagentes que van a escribir archivos/);
});

const apiIde = read("./src/lib/api-ide.ts");

test("los tres clientes de plan siguen la forma real de los endpoints del backend", () => {
  assert.match(apiIde, /\/plan\/\$\{encodeURIComponent\(planId\)\}\/approve`,\s*\{ method: "POST" \}/);
  assert.match(apiIde, /\/plan\/\$\{encodeURIComponent\(planId\)\}\/edit`/);
  assert.match(apiIde, /\.\.\.jsonBody\(\{ steps \}\)/);
  assert.match(apiIde, /\/plan\/\$\{encodeURIComponent\(planId\)\}\/reject`/);
  assert.match(apiIde, /\.\.\.jsonBody\(\{ reason \}\)/);
});
