/**
 * Bloques ricos del IDE (`edecan_schemas.ide_blocks`) del lado del cliente.
 *
 * El agente los acuña con `mostrar_tabla`/`mostrar_grafica` y viajan en el
 * canal `presentation` de un evento de sesión — nunca en el texto, y nunca
 * desde el resultado que una herramienta le devuelve al modelo. El servidor ya
 * los valida (`_sanitize_agent_events` en `edecan_api.routers.ide`); esto los
 * vuelve a validar acá por la misma razón por la que existe
 * `lib/chat-blocks.ts`: un bloque malformado no puede tumbar el turno, y lo
 * peor que puede pasar es que se lea el `text` del evento en vez de la
 * tarjeta.
 *
 * Los bloques se proyectan a `RichTable`/`TableChart` — los mismos tipos que
 * produce el Markdown del agente — para que los dibuje UN solo juego de
 * componentes. Dos renders de tabla en la misma pantalla terminarían diciendo
 * cosas distintas del mismo dato.
 */

import {
  MAX_CHART_SERIES,
  type ColumnAlign,
  type RichTable,
  type TableChart,
} from "./rich-text";

/** Espejo de los topes de `edecan_schemas.ide_blocks`. */
export const MAX_TABLE_COLUMNS = 8;
export const MAX_TABLE_ROWS = 30;
export const MAX_TABLE_CELL_CHARS = 120;
export const MAX_CHART_POINTS = 60;
export const MAX_BLOCKS_PER_EVENT = 3;

export type ChartKind = "bar" | "line";

export interface IdeTableColumn {
  key: string;
  title: string;
  align: ColumnAlign;
}

export interface IdeTableBlock {
  type: "table";
  fallback_text: string;
  title: string | null;
  columns: IdeTableColumn[];
  /** Celdas por CLAVE de columna: una celda ausente es "sin dato", no un cero. */
  rows: Record<string, string>[];
  note: string | null;
}

export interface IdeChartSeries {
  name: string;
  points: { label: string; value: number }[];
}

export interface IdeChartBlock {
  type: "chart";
  fallback_text: string;
  chart_kind: ChartKind;
  title: string;
  series: IdeChartSeries[];
  x_label: string | null;
  y_label: string | null;
  note: string | null;
}

export type IdeBlock = IdeTableBlock | IdeChartBlock;

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function text(value: unknown, maxLength: number): string | null {
  if (typeof value !== "string") return null;
  const clean = value.trim();
  return clean && clean.length <= maxLength ? clean : null;
}

function optionalText(value: unknown, maxLength: number): string | null {
  if (value === null || value === undefined || value === "") return null;
  return text(value, maxLength);
}

function align(value: unknown): ColumnAlign {
  return value === "center" || value === "right" ? value : "left";
}

/**
 * Recorta a `MAX_TABLE_CELL_CHARS` DEJANDO la marca del recorte.
 *
 * Espejo de `recortar_celda` en `edecan_schemas/ide_blocks.py`. Sin el "…",
 * una ruta o una URL cortada se lee como el valor completo: la celda no
 * revienta, se ve perfecta y dice otra cosa. El emisor ya recorta así, con lo
 * que esto solo actúa ante un companion de otra versión.
 */
function recortarCelda(value: string): string {
  return value.length <= MAX_TABLE_CELL_CHARS
    ? value
    : `${value.slice(0, MAX_TABLE_CELL_CHARS - 1)}…`;
}

function parseTable(raw: Record<string, unknown>): IdeTableBlock | null {
  const fallback = text(raw.fallback_text, 4000);
  if (!fallback || !Array.isArray(raw.columns) || !Array.isArray(raw.rows)) return null;

  const columns: IdeTableColumn[] = [];
  const keys = new Set<string>();
  for (const item of raw.columns.slice(0, MAX_TABLE_COLUMNS)) {
    const column = record(item);
    if (!column) continue;
    const key = text(column.key, 40);
    const title = text(column.title, 60);
    // Dos columnas con la misma clave harían que una tape a la otra al leer la fila.
    if (!key || !title || keys.has(key)) continue;
    keys.add(key);
    columns.push({ key, title, align: align(column.align) });
  }
  if (columns.length === 0) return null;

  const rows: Record<string, string>[] = [];
  for (const item of raw.rows.slice(0, MAX_TABLE_ROWS)) {
    const row = record(item);
    if (!row) continue;
    const clean: Record<string, string> = {};
    for (const column of columns) {
      const value = row[column.key];
      if (typeof value === "string" && value !== "") clean[column.key] = recortarCelda(value);
    }
    rows.push(clean);
  }
  // Una tabla sin ni una celda con datos no dice nada que el texto no diga.
  if (!rows.some((row) => Object.keys(row).length > 0)) return null;

  return {
    type: "table",
    fallback_text: fallback,
    title: optionalText(raw.title, 120),
    columns,
    rows,
    note: optionalText(raw.note, 300),
  };
}

function parseChart(raw: Record<string, unknown>): IdeChartBlock | null {
  const fallback = text(raw.fallback_text, 4000);
  const title = text(raw.title, 120);
  const kind: ChartKind = raw.chart_kind === "line" ? "line" : "bar";
  if (!fallback || !title || !Array.isArray(raw.series)) return null;

  const series: IdeChartSeries[] = [];
  const names = new Set<string>();
  for (const item of raw.series.slice(0, MAX_CHART_SERIES)) {
    const serie = record(item);
    if (!serie) continue;
    const name = text(serie.name, 60);
    if (!name || names.has(name) || !Array.isArray(serie.points)) continue;
    const points: { label: string; value: number }[] = [];
    const labels = new Set<string>();
    for (const rawPoint of serie.points.slice(0, MAX_CHART_POINTS)) {
      const point = record(rawPoint);
      if (!point) continue;
      const label = text(point.label, 40);
      const value = typeof point.value === "number" && Number.isFinite(point.value) ? point.value : null;
      // Dos puntos con la misma etiqueta son indistinguibles al mirarlos.
      if (!label || value === null || labels.has(label)) continue;
      labels.add(label);
      points.push({ label, value });
    }
    if (points.length < 2) continue;
    names.add(name);
    series.push({ name, points });
  }
  if (series.length === 0) return null;

  return {
    type: "chart",
    fallback_text: fallback,
    chart_kind: kind,
    title,
    series,
    x_label: optionalText(raw.x_label, 60),
    y_label: optionalText(raw.y_label, 60),
    note: optionalText(raw.note, 300),
  };
}

export function parseIdeBlock(value: unknown): IdeBlock | null {
  const raw = record(value);
  if (!raw) return null;
  // Una versión futura no se interpreta como v1 por accidente.
  if (raw.schema_version !== undefined && raw.schema_version !== 1) return null;
  if (raw.type === "table") return parseTable(raw);
  if (raw.type === "chart") return parseChart(raw);
  return null;
}

export function parseIdeBlocks(value: unknown): IdeBlock[] {
  if (!Array.isArray(value)) return [];
  return value.slice(0, MAX_BLOCKS_PER_EVENT).flatMap((item) => {
    const block = parseIdeBlock(item);
    return block ? [block] : [];
  });
}

/**
 * Proyecta la tabla tipada al mismo `RichTable` que produce el Markdown.
 *
 * Las filas vienen indexadas por clave y se ordenan según las columnas: una
 * celda ausente queda vacía en SU columna y el render pinta "—". Con filas
 * posicionales, una celda faltante correría todas las demás y cada valor
 * quedaría bajo la columna equivocada — un render que no revienta pero miente.
 */
export function tableFromIdeBlock(block: IdeTableBlock): RichTable {
  return {
    headers: block.columns.map((column) => column.title),
    aligns: block.columns.map((column) => column.align),
    rows: block.rows.map((row) => block.columns.map((column) => row[column.key] ?? "")),
    totalRows: block.rows.length,
  };
}

/**
 * Proyecta la gráfica tipada al mismo `TableChart` que produce el Markdown.
 *
 * Los valores se buscan POR ETIQUETA y no por posición: una serie a la que le
 * falte un punto se descarta entera en vez de dibujarse corrida (o rellenada
 * con ceros, que serían datos inventados).
 */
export function chartFromIdeBlock(block: IdeChartBlock): TableChart | null {
  const labels = block.series[0].points.map((point) => point.label);
  const series = block.series.flatMap((serie) => {
    const byLabel = new Map(serie.points.map((point) => [point.label, point.value]));
    const values = labels.map((label) => byLabel.get(label));
    if (values.some((value) => value === undefined)) return [];
    return [{ name: serie.name, values: values as number[] }];
  });
  if (series.length === 0) return null;
  return { labels, series, omittedSeries: block.series.length - series.length };
}
