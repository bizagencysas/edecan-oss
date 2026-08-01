/**
 * Criterio de `edecan-test-web-format`.
 *
 * `apps/web/src/lib/format.ts` es el módulo que decide cómo ve el usuario cada
 * fecha, cada monto y cada cifra de la aplicación, y no tiene ninguna prueba.
 * Este criterio no se conforma con que exista el archivo: rompe cada helper
 * exportado, uno por uno, y exige que la suite nueva se ponga roja. Un archivo
 * con un solo `assert.ok(true)` no pasa de aquí.
 *
 * Falla hoy: `apps/web/format.test.mjs` no existe.
 *
 * El módulo original se restaura siempre, incluso si Node revienta.
 */

import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const RAIZ = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../..");
const MODULO = path.join(RAIZ, "apps/web/src/lib/format.ts");
const PRUEBAS = path.join(RAIZ, "apps/web/format.test.mjs");

const MUTANTES = [
  ["formatDateTime", '"MUTANTE"'],
  ["formatDate", '"MUTANTE"'],
  ["formatMoney", '"MUTANTE"'],
  ["formatNumber", '"MUTANTE"'],
  ["currentMonth", '"MUTANTE"'],
  ["limitLabel", '"MUTANTE"'],
  ["bytesToMb", "-1"],
];

function correrPruebas() {
  return spawnSync(process.execPath, ["--test", path.relative(RAIZ, PRUEBAS)], {
    cwd: RAIZ,
    encoding: "utf8",
  });
}

function mutar(original, nombre, valor) {
  const firma = `export function ${nombre}(`;
  if (!original.includes(firma)) {
    throw new Error(`format.ts ya no exporta ${nombre}: el criterio quedó desactualizado`);
  }
  const renombrado = original.replace(firma, `function __original_${nombre}(`);
  return `${renombrado}\nexport function ${nombre}() {\n  return ${valor};\n}\n`;
}

function fallar(mensaje) {
  console.error(mensaje);
  process.exit(1);
}

if (!existsSync(PRUEBAS)) fallar("falta apps/web/format.test.mjs");

const limpio = correrPruebas();
if (limpio.status !== 0) {
  fallar(`las pruebas nuevas no pasan sobre el módulo intacto:\n${limpio.stdout || limpio.stderr}`);
}

const original = readFileSync(MODULO, "utf8");
let sobrevivio = null;
try {
  for (const [nombre, valor] of MUTANTES) {
    writeFileSync(MODULO, mutar(original, nombre, valor), "utf8");
    if (correrPruebas().status === 0) {
      sobrevivio = nombre;
      break;
    }
  }
} finally {
  writeFileSync(MODULO, original, "utf8");
}

if (sobrevivio) fallar(`la suite nueva NO detecta que ${sobrevivio}() devuelva basura`);
console.log(`ok: las pruebas de format.ts pasan y matan los ${MUTANTES.length} mutantes`);
