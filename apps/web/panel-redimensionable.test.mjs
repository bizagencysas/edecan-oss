import assert from "node:assert/strict";
import test from "node:test";

import {
  analizarEstadoPanel,
  anchoConDelta,
  anchoEnVivo,
  clamp,
  claveAlmacenamiento,
  limitesPanel,
  resolverAnchoArrastre,
  serializarEstadoPanel,
  siguienteAnchoTeclado,
} from "./src/components/ide/panel-redimensionable-logica.ts";

test("clamp confina al rango y nunca cruza min/max", () => {
  assert.equal(clamp(50, 200, 500), 200);
  assert.equal(clamp(9999, 200, 500), 500);
  assert.equal(clamp(300, 200, 500), 300);
  // Ventana angosta donde max < min por redondeo: gana el mínimo.
  assert.equal(clamp(150, 200, 120), 200);
});

test("limitesPanel calcula el 40% de la ventana y nunca deja el máximo por debajo del mínimo", () => {
  assert.deepEqual(limitesPanel(1600, 200, 0.4), { min: 200, max: 640 });
  // Ventana angosta: 40% de 300 = 120, por debajo del mínimo de 200 -- el máximo cede.
  assert.deepEqual(limitesPanel(300, 200, 0.4), { min: 200, max: 200 });
});

test("resolverAnchoArrastre colapsa por debajo del mínimo y clampea dentro del rango", () => {
  const limites = { min: 200, max: 600 };
  assert.deepEqual(resolverAnchoArrastre(199, limites), { ancho: 200, colapsado: true });
  assert.deepEqual(resolverAnchoArrastre(-40, limites), { ancho: 200, colapsado: true });
  assert.deepEqual(resolverAnchoArrastre(200, limites), { ancho: 200, colapsado: false });
  assert.deepEqual(resolverAnchoArrastre(350, limites), { ancho: 350, colapsado: false });
  assert.deepEqual(resolverAnchoArrastre(900, limites), { ancho: 600, colapsado: false });
});

test("anchoEnVivo da resistencia en el mínimo durante el arrastre (no colapsa a medio gesto)", () => {
  const limites = { min: 200, max: 600 };
  assert.equal(anchoEnVivo(120, limites), 200);
  assert.equal(anchoEnVivo(900, limites), 600);
  assert.equal(anchoEnVivo(300, limites), 300);
});

test("anchoConDelta crece hacia la derecha en el panel izquierdo y hacia la izquierda en el derecho", () => {
  assert.equal(anchoConDelta(true, 300, 40), 340);
  assert.equal(anchoConDelta(true, 300, -40), 260);
  assert.equal(anchoConDelta(false, 300, 40), 260);
  assert.equal(anchoConDelta(false, 300, -40), 340);
});

test("siguienteAnchoTeclado mueve 16px por flecha, clampeado, sin colapsar nunca", () => {
  const limites = { min: 200, max: 600 };
  assert.equal(siguienteAnchoTeclado(true, 300, "ArrowRight", limites), 316);
  assert.equal(siguienteAnchoTeclado(true, 300, "ArrowLeft", limites), 284);
  assert.equal(siguienteAnchoTeclado(false, 300, "ArrowRight", limites), 284);
  assert.equal(siguienteAnchoTeclado(false, 300, "ArrowLeft", limites), 316);
  // En el borde, clampea en vez de cruzar -- nunca colapsa por teclado.
  assert.equal(siguienteAnchoTeclado(true, 205, "ArrowLeft", limites), 200);
  assert.equal(siguienteAnchoTeclado(true, 590, "ArrowRight", limites), 600);
});

test("claveAlmacenamiento usa el prefijo forge.panel. que pide el encargo", () => {
  assert.equal(claveAlmacenamiento("proyectos"), "forge.panel.proyectos");
  assert.equal(claveAlmacenamiento("ejecuciones"), "forge.panel.ejecuciones");
});

test("serializar/analizar hacen ida y vuelta sin perder datos", () => {
  const estado = { ancho: 420, colapsado: false };
  assert.deepEqual(analizarEstadoPanel(serializarEstadoPanel(estado), 336), estado);

  const colapsado = { ancho: 200, colapsado: true };
  assert.deepEqual(analizarEstadoPanel(serializarEstadoPanel(colapsado), 336), colapsado);
});

test("analizarEstadoPanel cae al default ante localStorage vacío, corrupto o con datos inválidos", () => {
  assert.deepEqual(analizarEstadoPanel(null, 336), { ancho: 336, colapsado: false });
  assert.deepEqual(analizarEstadoPanel("", 336), { ancho: 336, colapsado: false });
  assert.deepEqual(analizarEstadoPanel("{no es json", 336), { ancho: 336, colapsado: false });
  // Ancho negativo, NaN o de tipo equivocado -- ninguno pasa la validación.
  assert.deepEqual(analizarEstadoPanel(JSON.stringify({ ancho: -50, colapsado: false }), 336), {
    ancho: 336,
    colapsado: false,
  });
  assert.deepEqual(analizarEstadoPanel(JSON.stringify({ ancho: "420", colapsado: false }), 336), {
    ancho: 336,
    colapsado: false,
  });
  assert.deepEqual(analizarEstadoPanel(JSON.stringify({ colapsado: true }), 336), {
    ancho: 336,
    colapsado: true,
  });
  // `colapsado` con un valor truthy pero no booleano no cuela: solo `true` literal.
  assert.deepEqual(analizarEstadoPanel(JSON.stringify({ ancho: 400, colapsado: "si" }), 336), {
    ancho: 400,
    colapsado: false,
  });
});
