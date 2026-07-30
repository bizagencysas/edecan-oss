/**
 * Criterio de `edecan-web-format-vacio`.
 *
 * `formatMoney`/`formatNumber` reciben lo que llega del API. Un campo
 * numérico ausente viaja como `""` en varias respuestas, y `Number("")` es
 * `0`: hoy la interfaz muestra "0,00 US$" — un dato inventado — donde debería
 * mostrar el guion de "sin dato" que ya usa para `null`/`undefined`.
 *
 * Falla hoy con cadena vacía y con cadena de espacios.
 */

import { formatMoney, formatNumber } from "../../../../apps/web/src/lib/format.ts";

const fallos = [];

function esperar(etiqueta, obtenido, esperado) {
  if (obtenido !== esperado) {
    fallos.push(`${etiqueta}: obtenido ${JSON.stringify(obtenido)}, esperado ${JSON.stringify(esperado)}`);
  }
}

// Sin dato: la cadena vacía y la cadena en blanco valen lo mismo que null.
const guion = formatMoney(null);
esperar('formatMoney("")', formatMoney(""), guion);
esperar('formatMoney("   ")', formatMoney("   "), guion);
esperar("formatMoney(undefined)", formatMoney(undefined), guion);
esperar('formatNumber("")', formatNumber(""), formatNumber(null));
esperar('formatNumber("   ")', formatNumber("   "), formatNumber(null));

// Y los casos legítimos siguen intactos.
esperar("formatMoney(0)", formatMoney(0), formatMoney(0));
if (formatMoney(0) === guion) {
  fallos.push("formatMoney(0) se volvió el guion: un cero real sí es un dato");
}
if (formatNumber(0) === formatNumber(null)) {
  fallos.push("formatNumber(0) se volvió el guion: un cero real sí es un dato");
}
if (!formatMoney("1234.5").includes("1")) {
  fallos.push(`formatMoney("1234.5") dejó de formatear: ${formatMoney("1234.5")}`);
}
if (formatNumber("no-es-un-numero") !== "no-es-un-numero") {
  fallos.push("formatNumber ya no devuelve el valor crudo cuando no es numérico");
}

if (fallos.length > 0) {
  for (const fallo of fallos) console.error(fallo);
  process.exit(1);
}
console.log("ok: los campos vacíos se muestran como sin dato");
