/**
 * Parte la respuesta del agente del IDE en prosa y TABLAS de Markdown (GFM).
 *
 * Por qué existe: el agente contesta con tablas en Markdown, y ninguna de las
 * dos superficies las sabía pintar — en web `MarkdownText` no las contempla y
 * en iOS `AttributedString(markdown:)` tampoco. El resultado era una tabla
 * mostrada como barras y guiones, que es exactamente el caso en el que un
 * render pobre hace que el dato PAREZCA decir otra cosa.
 *
 * La misma gramática está implementada dos veces: aquí y en
 * `apps/mobile/ios/EdecanApp/Componentes/TextoRicoIDE.swift`. Se duplica a
 * propósito (el iPhone no puede importar TypeScript), pero es un contrato
 * compartido: cambiar una regla en un solo lado hace que el teléfono y el
 * navegador digan cosas distintas del mismo texto, que es el bug silencioso
 * que esto viene a evitar.
 */

export type ColumnAlign = "left" | "center" | "right";

export interface RichTable {
  headers: string[];
  aligns: ColumnAlign[];
  /** Filas visibles, ya acotadas a `MAX_TABLE_ROWS`. */
  rows: string[][];
  /** Filas que traía el texto. Si supera a `rows.length`, la UI DEBE avisarlo. */
  totalRows: number;
}

export type RichSegment =
  | { kind: "text"; text: string }
  | { kind: "table"; table: RichTable };

/** Tope de filas pintadas. Recortar en silencio no: la UI avisa cuántas faltan. */
export const MAX_TABLE_ROWS = 200;

/** Tope de series en la gráfica. Es el mismo de `edecan_schemas.ide_blocks`
 * (`MAX_CHART_SERIES`), para que una gráfica que el servidor acepta no pierda
 * series al dibujarse. */
export const MAX_CHART_SERIES = 6;

const SEPARATOR_CELL = /^:?-+:?$/;
const FENCE = /^\s*(```+|~~~+)/;

/** Parte una fila por `|` respetando `\|` escapado y las barras de los extremos. */
export function splitCells(line: string): string[] {
  let work = line.trim();
  if (work.startsWith("|")) work = work.slice(1);
  if (work.endsWith("|") && !work.endsWith("\\|")) work = work.slice(0, -1);
  const cells: string[] = [];
  let current = "";
  for (let index = 0; index < work.length; index += 1) {
    const char = work[index];
    if (char === "\\" && work[index + 1] === "|") {
      current += "|";
      index += 1;
      continue;
    }
    if (char === "|") {
      cells.push(current.trim());
      current = "";
      continue;
    }
    current += char;
  }
  cells.push(current.trim());
  return cells;
}

/** La línea `| --- | ---: |` que confirma que la anterior era un encabezado. */
function separatorAligns(line: string, columns: number): ColumnAlign[] | null {
  if (!line.includes("-") || !line.includes("|")) return null;
  const cells = splitCells(line);
  if (cells.length !== columns) return null;
  const aligns: ColumnAlign[] = [];
  for (const cell of cells) {
    const clean = cell.replace(/\s+/g, "");
    if (!SEPARATOR_CELL.test(clean)) return null;
    const left = clean.startsWith(":");
    const right = clean.endsWith(":");
    aligns.push(left && right ? "center" : right ? "right" : "left");
  }
  return aligns;
}

/**
 * Deja el contenido de una celda en texto plano.
 *
 * Una celda con `**Total**` tiene que verse igual en las dos superficies. El
 * camino barato para lograrlo es quitar los marcadores en ambas, en vez de
 * meter un render inline distinto dentro de cada celda de cada plataforma.
 */
export function plainCell(raw: string): string {
  return raw
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/(\*\*|__|~~)(.*?)\1/g, "$2")
    .replace(/`([^`]*)`/g, "$1")
    .trim();
}

export function splitRichText(text: string): RichSegment[] {
  const lines = text.split("\n");
  const segments: RichSegment[] = [];
  let prose: string[] = [];
  let fence: string | null = null;

  function flushProse() {
    if (prose.length === 0) return;
    const joined = prose.join("\n");
    if (joined.trim()) segments.push({ kind: "text", text: joined });
    prose = [];
  }

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index].replace(/\r$/, "");

    // Una tabla dentro de un bloque de código es código, no una tabla.
    const fenceMatch = FENCE.exec(line);
    if (fenceMatch) {
      const marker = fenceMatch[1][0];
      if (fence === null) fence = marker;
      else if (marker === fence) fence = null;
      prose.push(line);
      continue;
    }
    if (fence !== null) {
      prose.push(line);
      continue;
    }

    if (line.includes("|") && index + 1 < lines.length) {
      const headers = splitCells(line);
      // Con una sola columna, `a | b` dentro de una frase se volvería tabla.
      const aligns = headers.length >= 2 ? separatorAligns(lines[index + 1], headers.length) : null;
      if (aligns) {
        const rows: string[][] = [];
        let cursor = index + 2;
        while (cursor < lines.length) {
          const rowLine = lines[cursor].replace(/\r$/, "");
          if (!rowLine.trim() || !rowLine.includes("|") || FENCE.test(rowLine)) break;
          const cells = splitCells(rowLine);
          // GFM: las celdas de más se descartan y las que faltan quedan vacías.
          rows.push(headers.map((_, column) => plainCell(cells[column] ?? "")));
          cursor += 1;
        }
        flushProse();
        segments.push({
          kind: "table",
          table: {
            headers: headers.map(plainCell),
            aligns,
            rows: rows.slice(0, MAX_TABLE_ROWS),
            totalRows: rows.length,
          },
        });
        index = cursor - 1;
        continue;
      }
    }

    prose.push(line);
  }

  flushProse();
  return segments;
}

/**
 * Convierte una celda a número aceptando los formatos que el modelo mezcla:
 * `1.234,56` (es), `1,234.56` (en), `$1 200`, `45%`, `(320)` de contabilidad.
 * Devuelve `null` en cuanto la celda no sea SOLO un número: una columna con
 * "12 días" no es una serie graficable y forzarla mentiría sobre el dato.
 */
export function parseNumericCell(raw: string): number | null {
  const trimmed = plainCell(raw);
  if (!trimmed) return null;
  let clean = trimmed
    // `\s` en JS ya cubre el espacio duro y el fino de los formatos es-419.
    .replace(/\s/g, "")
    .replace(/[$€£¥%]/g, "")
    .replace(/^\+/, "");
  if (/^\(.+\)$/.test(clean)) clean = `-${clean.slice(1, -1)}`;
  if (!/^-?[\d.,]+$/.test(clean)) return null;

  const hasDot = clean.includes(".");
  const hasComma = clean.includes(",");
  if (hasDot && hasComma) {
    // El separador que aparece más a la derecha es el decimal.
    const decimal = clean.lastIndexOf(".") > clean.lastIndexOf(",") ? "." : ",";
    const thousands = decimal === "." ? "," : ".";
    clean = clean.split(thousands).join("");
    if (decimal === ",") clean = clean.replace(",", ".");
  } else if (hasComma) {
    clean = /^-?\d{1,3}(,\d{3})+$/.test(clean) ? clean.split(",").join("") : clean.replace(",", ".");
  } else if (hasDot && /^-?\d{1,3}(\.\d{3})+$/.test(clean)) {
    clean = clean.split(".").join("");
  }

  if (!/^-?\d+(\.\d+)?$/.test(clean)) return null;
  const value = Number(clean);
  return Number.isFinite(value) ? value : null;
}

export interface TableSeries {
  name: string;
  values: number[];
}

export interface TableChart {
  labels: string[];
  series: TableSeries[];
  /** Series numéricas que no caben en la gráfica; la UI DEBE decirlo. */
  omittedSeries: number;
}

/**
 * Series graficables de una tabla: primera columna = etiquetas, y cada columna
 * siguiente cuyas celdas sean TODAS números es una serie. Si ninguna lo es, no
 * hay gráfica que ofrecer y la tabla se queda sola.
 */
export function tableChart(table: RichTable): TableChart | null {
  if (table.headers.length < 2 || table.rows.length < 2) return null;
  // Dos filas con la MISMA etiqueta se funden en una sola barra en Swift Charts
  // (la categoría es la clave del eje). Numerarlas es feo pero visible; fundirlas
  // sería un dato distinto al de la tabla sin que nadie lo note.
  const seen = new Map<string, number>();
  const labels = table.rows.map((row, index) => {
    const base = row[0]?.trim() || `Fila ${index + 1}`;
    const repeats = (seen.get(base) ?? 0) + 1;
    seen.set(base, repeats);
    return repeats === 1 ? base : `${base} (${repeats})`;
  });
  const series: TableSeries[] = [];
  for (let column = 1; column < table.headers.length; column += 1) {
    const values = table.rows.map((row) => parseNumericCell(row[column] ?? ""));
    if (values.some((value) => value === null)) continue;
    series.push({
      name: table.headers[column]?.trim() || `Columna ${column + 1}`,
      values: values as number[],
    });
  }
  if (series.length === 0) return null;
  return {
    labels,
    series: series.slice(0, MAX_CHART_SERIES),
    omittedSeries: Math.max(0, series.length - MAX_CHART_SERIES),
  };
}

/** Paleta compartida con iOS (`TextoRicoIDE.swift`) para que la misma serie tenga
 * el mismo color en las dos superficies. Seis, como el tope de series. */
export const CHART_COLORS = [
  "#4f46e5",
  "#0ea5e9",
  "#10b981",
  "#f59e0b",
  "#ec4899",
  "#14b8a6",
] as const;

/** Etiqueta del aviso de filas recortadas, o `null` si se muestran todas. */
export function rowsNotice(table: RichTable): string | null {
  if (table.totalRows <= table.rows.length) return null;
  return `Se muestran las primeras ${table.rows.length} filas de ${table.totalRows}.`;
}

/** Aviso de desplazamiento horizontal; con pocas columnas no hace falta. */
export function columnsNotice(table: RichTable): string | null {
  if (table.headers.length <= 3) return null;
  return `Desliza para ver las ${table.headers.length} columnas.`;
}
