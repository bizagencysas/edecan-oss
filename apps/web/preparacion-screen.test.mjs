import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

/**
 * `PantallaPreparacion.tsx` tiene JSX -- igual que el resto de `*.test.mjs`
 * de este paquete (ver `ide-terminal-ansi.test.mjs`) no se importa como
 * módulo (el "type stripping" nativo de Node no compila JSX ni TSX): se lee
 * su código fuente y se comprueban los contratos del encargo por patrón.
 *
 * Los contratos "de verdad" (detección con dobles, rechazo de un `id` fuera
 * del manifiesto, lista vacía fuera de Windows) viven del lado companion/API
 * -- ver `apps/companion/tests/test_preparacion.py` y
 * `apps/api/tests/test_preparacion_router.py`. Este archivo cubre la mitad
 * que sí vive acá: que la pantalla respeta el comportamiento pedido
 * literalmente por el dueño (un solo botón ▶, admin marcado antes de pulsar
 * play, fallo visible con reintentar, xterm.js reusado, cero hex sueltos).
 */
const SOURCE = readFileSync(
  new URL("./src/components/preparacion/PantallaPreparacion.tsx", import.meta.url),
  "utf8",
);
const LAYOUT_SOURCE = readFileSync(new URL("./src/app/(app)/layout.tsx", import.meta.url), "utf8");
const API_SOURCE = readFileSync(new URL("./src/lib/api-preparacion.ts", import.meta.url), "utf8");

test("un solo botón ▶ arranca la cola completa, no uno por fila", () => {
  const ocurrencias = SOURCE.match(/<PlayIcon\b/g) ?? [];
  assert.equal(ocurrencias.length, 1, "PlayIcon debe aparecer una sola vez (el botón global)");
  assert.match(SOURCE, /onClick=\{\(\)\s*=>\s*void ejecutarTodo\(\)\}/);
});

test("una fila que necesita administrador nunca se intenta sola", () => {
  // La condición vive en una función propia (`puedeIntentarseAhora`) para no
  // repetirse -- y se usa para decidir el estado INICIAL de la fila
  // (`sin_admin`), antes de que exista ningún intento fallido.
  assert.match(SOURCE, /function puedeIntentarseAhora/);
  assert.match(
    SOURCE,
    /function filaInicial[\s\S]{0,200}?puedeIntentarseAhora\(requisito, elevado\)/,
    "el estado inicial de la fila debe decidirse con los datos de la PRIMERA lectura (filaInicial), no después de un intento fallido",
  );
  assert.match(SOURCE, /"sin_admin"/);
});

test("un fallo se queda visible con reintentar; continuar de todas formas solo si no es obligatorio", () => {
  assert.match(SOURCE, /Reintentar/);
  assert.match(SOURCE, /Continuar de todas formas/);
  assert.match(SOURCE, /const puedeContinuarSinResolver = !fila\.obligatorio;/);
  // El botón de continuar sin resolver debe estar condicionado a esa constante.
  assert.match(SOURCE, /\{puedeContinuarSinResolver &&/);
});

test("una fila completada se desvanece y se quita de la lista antes de avisar que se puede seguir", () => {
  assert.match(SOURCE, /estadoFila: "saliendo"/);
  assert.match(SOURCE, /quitarFila\(id\)/);
  // La pantalla completa solo se desvanece cuando la lista quedó vacía.
  assert.match(SOURCE, /filas\.length > 0 \|\| saliendoPantalla\) return;/);
  assert.match(SOURCE, /onListo/);
});

test("la salida en vivo reusa xterm.js (nunca limpia ANSI) y solo se monta mientras la fila corre", () => {
  assert.match(SOURCE, /import\("@xterm\/xterm"\)/);
  assert.match(SOURCE, /import\("@xterm\/addon-fit"\)/);
  assert.doesNotMatch(SOURCE, /cleanTerminalOutput/);
  assert.match(
    SOURCE,
    /fila\.estadoFila === "ejecutando" && <SalidaInstalacion/,
    "el mini-terminal solo debe montarse para la fila que está corriendo ahora",
  );
});

test("un fallo al CONSULTAR el estado no bloquea la entrada a la app (fail-open)", () => {
  assert.match(SOURCE, /catch \(err\) \{[\s\S]{0,200}?console\.error[\s\S]{0,200}?onListo\(\);/);
});

test("cero literales de color [#rrggbb] en clases de Tailwind (el hex de xterm no cuenta: ese vive en un objeto ITheme, fuera de la cascada)", () => {
  // Mismo criterio que la regla del encargo para components/ide/: se
  // prohíben literales `[#rrggbb]` DENTRO de una clase de Tailwind
  // (`className="...bg-[#fff]..."`), no el hex que exige la API de xterm.js
  // (`ITheme`, ver `TEMA_SALIDA`), que no puede expresarse con tokens de
  // Tailwind porque xterm pinta fuera de esa cascada.
  const clasesTailwind = SOURCE.match(/className=\{?[`"][^`"]*[`"]/g) ?? [];
  for (const clase of clasesTailwind) {
    assert.doesNotMatch(clase, /\[#[0-9a-fA-F]{3,8}\]/, `literal de color en: ${clase}`);
  }
});

test("api-preparacion.ts es el único camino hacia /v1/preparacion (nada de fetch ad hoc en el componente)", () => {
  assert.doesNotMatch(SOURCE, /\bfetch\(/);
  assert.match(SOURCE, /from "@\/lib\/api-preparacion"/);
  assert.match(API_SOURCE, /"\/v1\/preparacion"/);
  assert.match(API_SOURCE, /\/instalar`/);
});

test("(app)/layout.tsx solo muestra la pantalla en la app de escritorio, después de resolver autenticación", () => {
  assert.match(LAYOUT_SOURCE, /isLocalDesktop/);
  assert.match(LAYOUT_SOURCE, /<PantallaPreparacion onListo=/);
  // El gate de preparación tiene que venir DESPUÉS del de auth, no antes --
  // si no, alguien sin sesión vería esta pantalla.
  const posAuthGate = LAYOUT_SOURCE.indexOf("loading || !isAuthenticated");
  const posPreparacionGate = LAYOUT_SOURCE.indexOf("isLocalDesktop && !preparacionLista");
  assert.ok(posAuthGate >= 0 && posPreparacionGate >= 0);
  assert.ok(posAuthGate < posPreparacionGate);
});
