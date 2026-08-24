import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

/**
 * Encargo "cuando se cierra el panel de Ejecuciones, hay que adivinar cómo
 * volver a abrirlo": al cerrar con la X, `page.tsx` dejaba de montar TODO el
 * bloque (`{estado.activityOpen && (...)}`), sin dejar rastro visible -- y la
 * entrada "Ejecuciones" de la barra lateral no se leía como un interruptor.
 *
 * `PanelRedimensionable.tsx` ya resolvía exactamente este problema para el
 * caso "colapsado arrastrando el divisor" con una pestaña fina en el borde
 * (`pestana`/`reabrir`) -- el arreglo extrae ese patrón a un componente
 * exportado (`PanelTab`) y lo reutiliza en `page.tsx` para el caso "cerrado
 * del todo con la X", en vez de inventar una pestaña aparte.
 *
 * Igual que el resto de `*.test.mjs` de este paquete (ver
 * `ide-workspace-visibility.test.mjs`): JSX con componentes de React no se
 * importa como módulo acá, se lee el código fuente y se comprueba el patrón.
 */
const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

const panelRedimensionable = read("./src/components/ide/PanelRedimensionable.tsx");
const page = read('./src/app/(app)/app/ide/page.tsx');
const agentActivityCenter = read("./src/components/ide/AgentActivityCenter.tsx");

test("PanelTab se exporta desde PanelRedimensionable.tsx para que otros archivos reutilicen la misma pestaña", () => {
  assert.match(panelRedimensionable, /export function PanelTab/);
  // El colapso-por-arrastre (`pestana`) debe seguir usando ese mismo componente,
  // no una implementación paralela.
  assert.match(panelRedimensionable, /const pestana = colapsado && <PanelTab/);
});

test("page.tsx reutiliza PanelTab -- no inventa una pestaña de reapertura distinta", () => {
  assert.match(page, /import\s*\{\s*PanelRedimensionable,\s*PanelTab\s*\}\s*from ["']@\/components\/ide\/PanelRedimensionable["']/);
  assert.match(page, /<PanelTab/);
});

test("cerrar el panel ya no desmonta todo: hay una rama visible cuando activityOpen es falso", () => {
  const bloque = page.split('En pantallas anchas la torre de control')[1] ?? "";
  assert.match(bloque, /\{estado\.activityOpen \? \(/, "debe ser un if/else, no un `&&` que desaparece del todo");
  assert.match(bloque, /onClick=\{\(\) => estado\.setActivityOpen\(true\)\}/, "la pestaña debe reabrir el panel");
});

test("la pestaña de reapertura no depende de `lg:` -- es la única forma de reabrir en pantallas chicas", () => {
  const bloque = page.split('En pantallas anchas la torre de control')[1] ?? "";
  const ramaCerrada = bloque.split(") : (")[1]?.split("<PanelTab")[0] ?? "";
  assert.doesNotMatch(ramaCerrada, /\blg:hidden\b/);
  assert.doesNotMatch(ramaCerrada, /\bhidden lg:/);
});

test("la pestaña avisa si hay agentes trabajando o esperando aprobación (encargo: esa información no se puede perder al cerrar)", () => {
  assert.match(page, /waitingRunCount/);
  assert.match(page, /tonoDe\(run\) === "esperando"/);
  // El indicador debe distinguir "te espera" (número, más urgente) de solo "corriendo" (punto).
  assert.match(page, /indicador=\{/);
});

test("la entrada «Ejecuciones» de la barra lateral refleja abierto/cerrado -- ya no es un botón mudo", () => {
  const boton = page.split("Encargo: \"no ser un botón mudo\"")[1]?.split("</button>")[0] ?? "";
  assert.notEqual(boton, "", "debe existir el bloque comentado del botón de Ejecuciones");
  assert.match(boton, /aria-pressed=\{estado\.activityOpen\}/);
  // El fondo del botón debe depender de si el panel está abierto, no ser una clase fija.
  assert.match(boton, /estado\.activityOpen\s*\n?\s*\?\s*"bg-forja-superficie-elevada/);
});

test("tonoDe (AgentActivityCenter) sigue siendo la única fuente de qué cuenta como \"esperando\"", () => {
  assert.match(agentActivityCenter, /export function tonoDe/);
  assert.match(page, /import\s*\{\s*AgentActivityCenter,\s*tonoDe\s*\}/);
});

test("cero literales de color [#rrggbb] en PanelRedimensionable.tsx y page.tsx (regla dura del encargo)", () => {
  for (const source of [panelRedimensionable, page]) {
    assert.doesNotMatch(source, /\[#[0-9a-fA-F]{3,8}\]/);
  }
});
