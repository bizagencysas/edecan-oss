import assert from "node:assert/strict";
import test from "node:test";

import {
  chartFromIdeBlock,
  parseIdeBlock,
  parseIdeBlocks,
  tableFromIdeBlock,
} from "./src/components/ide/ide-blocks.ts";

/** Espejo de `apps/mobile/ios/EdecanKit/Tests/EdecanKitTests/IDEBlocksTests.swift`. */

const TABLA = {
  schema_version: 1,
  type: "table",
  fallback_text: "Ruta | ms\n/a | 120",
  title: "Rutas más lentas",
  columns: [
    { key: "ruta", title: "Ruta", align: "left" },
    { key: "ms", title: "Latencia", align: "right" },
  ],
  rows: [{ ruta: "/a", ms: "120" }, { ruta: "/b", ms: "340" }],
  note: "Se muestran 2 de 18 rutas.",
};

const GRAFICA = {
  schema_version: 1,
  type: "chart",
  chart_kind: "line",
  fallback_text: "ene 10, feb 20, mar 15",
  title: "Latencia por mes",
  series: [
    {
      name: "p95",
      points: [
        { label: "ene", value: 10 },
        { label: "feb", value: 20 },
        { label: "mar", value: 15 },
      ],
    },
  ],
  x_label: "mes",
  y_label: "ms",
  note: null,
};

test("lee una tabla tipada con sus columnas y notas", () => {
  const block = parseIdeBlock(TABLA);
  assert.equal(block.type, "table");
  assert.equal(block.title, "Rutas más lentas");
  assert.equal(block.note, "Se muestran 2 de 18 rutas.");
  assert.deepEqual(block.columns.map((column) => column.align), ["left", "right"]);
});

test("ordena las celdas por CLAVE y deja vacía la que falta", () => {
  const block = parseIdeBlock({ ...TABLA, rows: [{ ms: "340" }, { ruta: "/c", ms: "10" }] });
  // Sin dato en "ruta": queda vacío en SU columna. Posicionalmente, "340" habría
  // caído bajo "Ruta" y la fila entera diría otra cosa sin que se note.
  assert.deepEqual(tableFromIdeBlock(block).rows, [["", "340"], ["/c", "10"]]);
});

test("descarta claves que no corresponden a ninguna columna", () => {
  const block = parseIdeBlock({ ...TABLA, rows: [{ ruta: "/a", ms: "1", secreto: "x" }] });
  assert.deepEqual(block.rows, [{ ruta: "/a", ms: "1" }]);
});

test("una tabla sin ninguna celda con datos no se dibuja", () => {
  assert.equal(parseIdeBlock({ ...TABLA, rows: [{}, {}] }), null);
});

test("una versión futura del esquema no se interpreta como v1", () => {
  assert.equal(parseIdeBlock({ ...TABLA, schema_version: 2 }), null);
});

test("un bloque de tipo desconocido se descarta sin tumbar el resto", () => {
  const blocks = parseIdeBlocks([{ schema_version: 1, type: "mapa" }, TABLA]);
  assert.equal(blocks.length, 1);
  assert.equal(blocks[0].type, "table");
});

test("no dibuja más de tres bloques por evento", () => {
  assert.equal(parseIdeBlocks([TABLA, TABLA, TABLA, TABLA]).length, 3);
});

test("lee una gráfica de líneas con sus ejes", () => {
  const block = parseIdeBlock(GRAFICA);
  assert.equal(block.chart_kind, "line");
  assert.equal(block.x_label, "mes");
  const chart = chartFromIdeBlock(block);
  assert.deepEqual(chart.labels, ["ene", "feb", "mar"]);
  assert.deepEqual(chart.series[0].values, [10, 20, 15]);
  assert.equal(chart.omittedSeries, 0);
});

test("un tipo de gráfica desconocido cae a barras en vez de no dibujarse", () => {
  assert.equal(parseIdeBlock({ ...GRAFICA, chart_kind: "burbujas" }).chart_kind, "bar");
});

test("una serie con menos de dos puntos no es una serie", () => {
  const block = parseIdeBlock({
    ...GRAFICA,
    series: [{ name: "p95", points: [{ label: "ene", value: 1 }] }],
  });
  assert.equal(block, null);
});

test("un valor que no es número finito descarta ese punto", () => {
  const block = parseIdeBlock({
    ...GRAFICA,
    series: [
      {
        name: "p95",
        points: [
          { label: "ene", value: 1 },
          { label: "feb", value: "20" },
          { label: "mar", value: 3 },
        ],
      },
    ],
  });
  assert.deepEqual(block.series[0].points.map((point) => point.label), ["ene", "mar"]);
});

test("una serie a la que le falta una etiqueta se descarta entera, no se rellena con ceros", () => {
  const block = parseIdeBlock({
    ...GRAFICA,
    series: [
      GRAFICA.series[0],
      { name: "p50", points: [{ label: "ene", value: 5 }, { label: "feb", value: 6 }] },
    ],
  });
  const chart = chartFromIdeBlock(block);
  assert.equal(chart.series.length, 1);
  assert.equal(chart.series[0].name, "p95");
  assert.equal(chart.omittedSeries, 1);
});
