import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  IDE_EVENTO_MENSAJE_ENCOLADO,
  IDE_EVENTO_MENSAJE_ENTREGADO,
  IDE_EVENTO_MENSAJE_NO_ENTREGADO,
  conEventos,
  conFallo,
  conRespuestaDeEnvio,
  contarEnEspera,
  mensajesDeConversacion,
  normalizarRespuestaDeEnvio,
  nuevoMensajeLocal,
  sinFichasDeSesionesTerminadas,
  sinResueltosViejos,
} from "./src/lib/ide-cola.ts";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

function nuevo(overrides = {}) {
  return nuevoMensajeLocal({
    localId: "l1",
    conversationId: "c1",
    texto: "revisa también el login",
    creadoEn: 1_000,
    cursorAlEncolar: 10,
    ...overrides,
  });
}

/** Igual a lo que responde `routers/ide.py::post_agent` cuando el mensaje se encoló. */
function respuestaDelMotor(position) {
  return {
    id: "s1",
    queued: {
      id: "q1",
      position,
      pending: position,
      max_pending: 5,
      queued_at: "2026-07-29T10:00:00Z",
    },
  };
}

function encolado(mensaje, position = 1) {
  return conRespuestaDeEnvio(
    [mensaje],
    mensaje.localId,
    normalizarRespuestaDeEnvio(respuestaDelMotor(position)),
  );
}

// --- La respuesta del envío -------------------------------------------------

test("un mensaje que arranca de una no deja ficha: ya se ve como un turno del hilo", () => {
  // Con el agente libre el motor responde la sesión sola, sin `queued`.
  const resultado = conRespuestaDeEnvio(
    [nuevo()],
    "l1",
    normalizarRespuestaDeEnvio({ id: "s1", kind: "agent" }),
  );
  assert.equal(resultado.length, 0);
});

test("la posición del motor es 1-based y la ficha cuenta los que van por delante", () => {
  assert.deepEqual(normalizarRespuestaDeEnvio(respuestaDelMotor(1)), {
    sessionId: "s1",
    encolado: true,
    posicion: 0,
  });
  assert.equal(normalizarRespuestaDeEnvio(respuestaDelMotor(3)).posicion, 2);
  // Tolerancia por si el router alguna vez responde el booleano pelado.
  assert.deepEqual(normalizarRespuestaDeEnvio({ id: "s1", queued: true }), {
    sessionId: "s1",
    encolado: true,
    posicion: null,
  });
  assert.equal(normalizarRespuestaDeEnvio(null).encolado, false);
});

test("con el agente ocupado, la ficha queda en cola contra esa sesión", () => {
  const cola = encolado(nuevo(), 2);
  assert.equal(cola[0].estado, "encolado");
  assert.equal(cola[0].sessionId, "s1");
  assert.equal(cola[0].posicion, 1);
});

// --- Los eventos del motor --------------------------------------------------

test("`user_delivered` pasa la ficha a entregado", () => {
  const resultado = conEventos(
    encolado(nuevo()),
    "s1",
    [{ cursor: 42, type: IDE_EVENTO_MENSAJE_ENTREGADO, text: "revisa también el login" }],
    5_000,
  );
  assert.equal(resultado[0].estado, "entregado");
  assert.equal(resultado[0].resueltoEn, 5_000);
});

test("`user_queued` solo confirma la recepción: no lo da por leído", () => {
  const resultado = conEventos(
    encolado(nuevo()),
    "s1",
    [{ cursor: 41, type: IDE_EVENTO_MENSAJE_ENCOLADO, text: "revisa también el login" }],
    5_000,
  );
  assert.equal(resultado[0].estado, "encolado");
});

test("`user_undelivered` no se traga el mensaje: queda en rojo y recuperable", () => {
  const resultado = conEventos(
    encolado(nuevo()),
    "s1",
    [{ cursor: 60, type: IDE_EVENTO_MENSAJE_NO_ENTREGADO, text: "revisa también el login" }],
    6_000,
  );
  assert.equal(resultado[0].estado, "fallido");
  assert.match(resultado[0].error, /vuelve a mandarlo/i);
  assert.equal(resultado[0].texto, "revisa también el login");
  // Un fallido no se va solo: necesita que la persona haga algo.
  assert.equal(sinResueltosViejos(resultado, 999_000).length, 1);
});

test("el turno que termina y promueve lo encolado también resuelve la ficha", () => {
  // `_entregar_pendientes_al_cerrar` une los pendientes con una línea en
  // blanco y los manda como UN evento `user`: sin esto, dos fichas se
  // quedarían pegadas en "en cola" para siempre.
  const cola = [
    ...encolado(nuevo({ localId: "a", texto: "usa la API nueva" })),
    ...encolado(nuevo({ localId: "b", texto: "y no toques el schema" })),
  ];
  const resultado = conEventos(
    cola,
    "s1",
    [{ cursor: 70, type: "user", text: "usa la API nueva\n\ny no toques el schema" }],
    7_000,
  );
  assert.deepEqual(
    resultado.map((mensaje) => mensaje.estado),
    ["entregado", "entregado"],
  );
});

test("un mensaje más largo que el recorte del motor igual se reconoce", () => {
  // `MAX_EVENT_TEXT_CHARS` (8000) recorta el texto del evento; comparar por
  // igualdad exacta dejaría la ficha pegada.
  const largo = `arregla ${"x".repeat(9_000)}`;
  const resultado = conEventos(
    encolado(nuevo({ texto: largo })),
    "s1",
    [{ cursor: 80, type: IDE_EVENTO_MENSAJE_ENTREGADO, text: largo.slice(0, 8_000) }],
    8_000,
  );
  assert.equal(resultado[0].estado, "entregado");
});

test("el eco del turno que YA estaba corriendo no marca entregado al mensaje encolado", () => {
  const resultado = conEventos(
    encolado(nuevo({ cursorAlEncolar: 20 })),
    "s1",
    [{ cursor: 5, type: "user", text: "revisa también el login" }],
    7_000,
  );
  assert.equal(resultado[0].estado, "encolado");
});

test("los eventos de otra sesión no tocan la cola de esta", () => {
  const cola = encolado(nuevo());
  const resultado = conEventos(
    cola,
    "otra-sesion",
    [{ cursor: 99, type: IDE_EVENTO_MENSAJE_ENTREGADO, text: "revisa también el login" }],
    7_000,
  );
  assert.equal(resultado[0].estado, "encolado");
  assert.equal(resultado, cola, "sin cambios reales debe devolver la misma lista");
});

test("tres mensajes seguidos: tres fichas, y cada una se resuelve sola", () => {
  // Lo que hace el dueño: escribe tres ideas mientras el agente trabaja.
  let cola = [];
  const textos = ["arregla el test", "usa la API nueva", "no toques el schema"];
  textos.forEach((texto, indice) => {
    cola = [...cola, nuevo({ localId: `l${indice}`, texto, cursorAlEncolar: 10 })];
    cola = conRespuestaDeEnvio(
      cola,
      `l${indice}`,
      normalizarRespuestaDeEnvio(respuestaDelMotor(indice + 1)),
    );
  });
  assert.equal(contarEnEspera(cola, "c1"), 3);
  assert.deepEqual(
    cola.map((mensaje) => mensaje.posicion),
    [0, 1, 2],
  );
  // El agente cierra su vuelta y se los lleva todos, en orden.
  cola = conEventos(
    cola,
    "s1",
    textos.map((texto, indice) => ({
      cursor: 20 + indice,
      type: IDE_EVENTO_MENSAJE_ENTREGADO,
      text: texto,
    })),
    9_000,
  );
  assert.equal(contarEnEspera(cola, "c1"), 0);
  assert.equal(sinResueltosViejos(cola, 20_000).length, 0);
});

// --- Fallos y limpieza ------------------------------------------------------

test("un envío fallido conserva el texto para poder recuperarlo", () => {
  const resultado = conFallo([nuevo()], "l1", "no hay Mac conectado");
  assert.equal(resultado[0].estado, "fallido");
  assert.equal(resultado[0].error, "no hay Mac conectado");
  assert.equal(resultado[0].texto, "revisa también el login");
});

test("la ficha de una sesión que ya terminó no se queda diciendo «en cola»", () => {
  // Caso de la torre de control: se dirige a otra conversación, de la que no
  // se leen eventos. Cuando esa sesión deja de estar viva, la ficha sobra.
  const cola = encolado(nuevo());
  assert.equal(sinFichasDeSesionesTerminadas(cola, new Set(["s1"]), new Set(["s1"])), cola);
  assert.equal(sinFichasDeSesionesTerminadas(cola, new Set(), new Set(["s1"])).length, 0);
  // Una sesión que todavía no aparece en la lista se deja quieta.
  assert.equal(sinFichasDeSesionesTerminadas(cola, new Set(), new Set()), cola);
  // Y la conversación abierta se resuelve por sus eventos, no por acá: soltarla
  // le ganaría la carrera al `user_undelivered` que la pinta en rojo.
  assert.equal(sinFichasDeSesionesTerminadas(cola, new Set(), new Set(["s1"]), "c1"), cola);
});

test("las fichas entregadas se van solas; las que esperan turno se quedan", () => {
  const entregada = { ...nuevo({ localId: "l1" }), estado: "entregado", resueltoEn: 1_000 };
  const enCola = { ...nuevo({ localId: "l3" }), estado: "encolado" };
  const resultado = sinResueltosViejos([entregada, enCola], 9_000, 5_000);
  assert.deepEqual(
    resultado.map((mensaje) => mensaje.localId),
    ["l3"],
  );
  const intacta = [entregada, enCola];
  assert.equal(sinResueltosViejos(intacta, 2_000, 5_000), intacta);
});

test("la cuenta de espera es por conversación y no cuenta lo ya resuelto", () => {
  const cola = [
    { ...nuevo({ localId: "a", conversationId: "c1" }), estado: "encolado" },
    { ...nuevo({ localId: "b", conversationId: "c1" }), estado: "enviando" },
    { ...nuevo({ localId: "c", conversationId: "c1" }), estado: "entregado" },
    { ...nuevo({ localId: "d", conversationId: "c2" }), estado: "encolado" },
  ];
  assert.equal(contarEnEspera(cola, "c1"), 2);
  assert.equal(contarEnEspera(cola, "c2"), 1);
  assert.equal(contarEnEspera(cola), 3);
  assert.equal(mensajesDeConversacion(cola, "c2").length, 1);
  assert.equal(mensajesDeConversacion(cola, null).length, 0);
});

// --- La pantalla: lo que el dueño pidió, verificado sobre el archivo ---------

const page = read("./src/app/(app)/app/ide/page.tsx");
const torre = read("./src/components/ide/AgentActivityCenter.tsx");
const hilo = read("./src/components/ide/AgentThread.tsx");

test("el compositor ya no se bloquea mientras el agente trabaja", () => {
  // El botón de enviar solo depende de que haya texto: ni `busy` ni el estado
  // del agente lo apagan, que era lo que hacía perder el mensaje.
  assert.match(page, /disabled=\{!prompt\.trim\(\) \|\| commandBusy\}/);
  assert.doesNotMatch(page, /disabled=\{!prompt\.trim\(\) \|\| busy \|\| commandBusy\}/);
  assert.doesNotMatch(page, /if \(!text \|\| !workspace \|\| busy\) return;/);
});

test("detener y enviar conviven en el compositor", () => {
  assert.match(page, /aria-label="Detener trabajo"/);
  assert.match(page, /Mandar a la cola del agente/);
});

test("la pantalla sigue el ciclo completo del mensaje", () => {
  for (const token of [
    "MessageQueue",
    "conRespuestaDeEnvio",
    "conEventos",
    "sinFichasDeSesionesTerminadas",
    "sinResueltosViejos",
  ]) {
    assert.match(page, new RegExp(token));
  }
  // El motor no sabe retirar un mensaje encolado: no se ofrece un botón que
  // no tiene ruta detrás (ver el docstring de `ide_sessions.py`).
  assert.doesNotMatch(page, /cancelIdeQueuedMessage/);
});

test("el mensaje dirigido se ve en el hilo, no solo en la ficha que se desvanece", () => {
  for (const tipo of ["user_queued", "user_delivered", "user_undelivered"]) {
    assert.match(hilo, new RegExp(tipo));
  }
  assert.match(hilo, /TIPOS_DIRIGIDOS\.has\(event\.type\)/);
});

test("la torre de control mira todas las carpetas, no solo la abierta", () => {
  // `getIdeAgents()` sin argumento es la lista de TODOS los workspaces.
  assert.match(page, /const rows = await getIdeAgents\(\);/);
  assert.match(page, /AgentActivityCenter/);
  assert.match(page, /activityRuns/);
});

test("desde la torre se puede saltar, detener y dirigir sin salir de ella", () => {
  assert.match(page, /onGo=\{\(run\) => void goToRun\(run\)\}/);
  assert.match(page, /onStop=\{\(run\) => void stopRun\(run\)\}/);
  assert.match(page, /onDirect=\{\(run, texto\) => void directRun\(run, texto\)\}/);
  // Dirigir conserva la continuidad del hilo de ESA conversación.
  assert.match(page, /conversation_id: conversationId,/);
});

test("la torre ordena por urgencia y no es una tabla ni un desplegable", () => {
  assert.match(torre, /PESO_DE_TONO/);
  assert.match(torre, /esperando: 0/);
  assert.doesNotMatch(torre, /<table|<select/);
});
