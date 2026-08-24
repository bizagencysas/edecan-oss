import assert from "node:assert/strict";
import test from "node:test";

import {
  MAX_CHART_SERIES,
  MAX_TABLE_ROWS,
  columnsNotice,
  parseNumericCell,
  plainCell,
  rowsNotice,
  splitCells,
  splitRichText,
  tableChart,
} from "./src/components/ide/rich-text.ts";

/** Estas reglas están duplicadas en `TextoRicoIDE.swift`: si cambian acá, cambian allá. */

test("reconoce una tabla GFM y separa la prosa de alrededor", () => {
  const segments = splitRichText(
    ["Resultado:", "", "| Mes | Ventas |", "| --- | ---: |", "| Enero | 10 |", "| Febrero | 12 |", "", "Listo."].join("\n"),
  );
  assert.equal(segments.length, 3);
  assert.equal(segments[0].kind, "text");
  assert.equal(segments[2].kind, "text");
  assert.equal(segments[1].kind, "table");
  const table = segments[1].table;
  assert.deepEqual(table.headers, ["Mes", "Ventas"]);
  assert.deepEqual(table.aligns, ["left", "right"]);
  assert.deepEqual(table.rows, [["Enero", "10"], ["Febrero", "12"]]);
  assert.equal(table.totalRows, 2);
});

test("acepta tablas sin barras en los extremos y con alineación centrada", () => {
  const [segment] = splitRichText("a | b | c\n:-: | :-- | --:\n1 | 2 | 3");
  assert.equal(segment.kind, "table");
  assert.deepEqual(segment.table.aligns, ["center", "left", "right"]);
  assert.deepEqual(segment.table.rows, [["1", "2", "3"]]);
});

test("una tabla dentro de un bloque de código sigue siendo código", () => {
  const text = ["```", "| a | b |", "| --- | --- |", "| 1 | 2 |", "```"].join("\n");
  const segments = splitRichText(text);
  assert.equal(segments.length, 1);
  assert.equal(segments[0].kind, "text");
});

test("no convierte en tabla una frase con barras", () => {
  const segments = splitRichText("Corre npm run lint | npm test y avisa.\nOtra línea.");
  assert.equal(segments.length, 1);
  assert.equal(segments[0].kind, "text");
});

test("rellena celdas faltantes y descarta las que sobran, como GFM", () => {
  const [segment] = splitRichText("| a | b |\n| --- | --- |\n| 1 |\n| 1 | 2 | 3 |");
  assert.deepEqual(segment.table.rows, [["1", ""], ["1", "2"]]);
});

test("respeta las barras escapadas dentro de una celda", () => {
  assert.deepEqual(splitCells("| a \\| b | c |"), ["a | b", "c"]);
  const [segment] = splitRichText("| x | y |\n| --- | --- |\n| a \\| b | c |");
  assert.deepEqual(segment.table.rows, [["a | b", "c"]]);
});

test("las celdas quedan en texto plano para que iOS y web muestren lo mismo", () => {
  assert.equal(plainCell("**Total**"), "Total");
  assert.equal(plainCell("`uv run pytest`"), "uv run pytest");
  assert.equal(plainCell("[Edecán](https://edecan.example)"), "Edecán");
});

test("avisa cuando la tabla trae más filas de las que se pintan", () => {
  const filas = Array.from({ length: MAX_TABLE_ROWS + 5 }, (_, index) => `| f${index} | ${index} |`);
  const [segment] = splitRichText(["| a | b |", "| --- | --- |", ...filas].join("\n"));
  assert.equal(segment.table.rows.length, MAX_TABLE_ROWS);
  assert.equal(segment.table.totalRows, MAX_TABLE_ROWS + 5);
  assert.match(rowsNotice(segment.table), /primeras 200 filas de 205/);
});

test("avisa del desplazamiento solo cuando hay columnas de sobra", () => {
  const [angosta] = splitRichText("| a | b |\n| --- | --- |\n| 1 | 2 |");
  assert.equal(columnsNotice(angosta.table), null);
  const [ancha] = splitRichText("| a | b | c | d |\n| --- | --- | --- | --- |\n| 1 | 2 | 3 | 4 |");
  assert.match(columnsNotice(ancha.table), /4 columnas/);
});

test("lee números en los formatos que mezcla el modelo", () => {
  assert.equal(parseNumericCell("1.234,56"), 1234.56);
  assert.equal(parseNumericCell("1,234.56"), 1234.56);
  assert.equal(parseNumericCell("1,234"), 1234);
  assert.equal(parseNumericCell("1.234"), 1234);
  assert.equal(parseNumericCell("12,5"), 12.5);
  assert.equal(parseNumericCell("$ 1 200"), 1200);
  assert.equal(parseNumericCell("45%"), 45);
  assert.equal(parseNumericCell("(320)"), -320);
  assert.equal(parseNumericCell("-7.5"), -7.5);
  assert.equal(parseNumericCell("**42**"), 42);
});

test("una celda que no es solo un número no se grafica", () => {
  assert.equal(parseNumericCell("12 días"), null);
  assert.equal(parseNumericCell(""), null);
  assert.equal(parseNumericCell("—"), null);
  assert.equal(parseNumericCell("1.2.3.4"), null);
});

test("arma series solo con columnas enteramente numéricas", () => {
  const [segment] = splitRichText(
    ["| Canal | Ventas | Notas |", "| --- | ---: | --- |", "| Web | 1.200 | sube |", "| Tienda | 800 | baja |"].join("\n"),
  );
  const chart = tableChart(segment.table);
  assert.deepEqual(chart.labels, ["Web", "Tienda"]);
  assert.equal(chart.series.length, 1);
  assert.equal(chart.series[0].name, "Ventas");
  assert.deepEqual(chart.series[0].values, [1200, 800]);
  assert.equal(chart.omittedSeries, 0);
});

test("numera las etiquetas repetidas en vez de fundir dos filas en una barra", () => {
  const [segment] = splitRichText("| Mes | Ventas |\n| --- | --- |\n| Enero | 1 |\n| Enero | 2 |");
  assert.deepEqual(tableChart(segment.table).labels, ["Enero", "Enero (2)"]);
});

test("sin ninguna columna numérica no hay gráfica que ofrecer", () => {
  const [segment] = splitRichText("| a | b |\n| --- | --- |\n| uno | dos |\n| tres | cuatro |");
  assert.equal(tableChart(segment.table), null);
});

test("pasado el tope de series dice cuántas quedaron fuera", () => {
  const columnas = ["clave", "s1", "s2", "s3", "s4", "s5", "s6", "s7"];
  const fila = (nombre) => `| ${nombre} | 1 | 2 | 3 | 4 | 5 | 6 | 7 |`;
  const [segment] = splitRichText(
    [`| ${columnas.join(" | ")} |`, `| ${columnas.map(() => "---").join(" | ")} |`, fila("a"), fila("b")].join("\n"),
  );
  const chart = tableChart(segment.table);
  assert.equal(chart.series.length, MAX_CHART_SERIES);
  assert.equal(chart.omittedSeries, 7 - MAX_CHART_SERIES);
});
