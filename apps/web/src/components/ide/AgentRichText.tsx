"use client";

/**
 * Tablas y gráficas del agente del IDE, en el navegador.
 *
 * Dibuja las dos cosas con UN solo juego de componentes:
 *
 * 1. Los bloques tipados (`edecan_schemas.ide_blocks`) que el agente acuña con
 *    `mostrar_tabla`/`mostrar_grafica` y llegan por el canal `presentation`.
 * 2. Las tablas en Markdown que el agente escribe en su respuesta, que antes
 *    pasaban por `MarkdownText` — que no conoce tablas — y llegaban como barras
 *    y guiones.
 *
 * Los dos caminos se proyectan a `RichTable`/`TableChart` y se pintan igual, a
 * propósito: dos renders de tabla en la misma pantalla acabarían diciendo cosas
 * distintas del mismo dato.
 *
 * La gráfica es SVG a mano y no una dependencia nueva: mismo criterio que en
 * iOS, donde se usa Swift Charts porque YA está en el proyecto
 * (`NegociosView.swift`). Las dos superficies comparten paleta y reglas
 * (`rich-text.ts` ↔ `TextoRicoMarkdown.swift`) para que la misma respuesta se
 * vea igual en el navegador y en el teléfono.
 */

import { useMemo, useState } from "react";

import { MarkdownText } from "@/components/chat/markdown";

import {
  chartFromIdeBlock,
  tableFromIdeBlock,
  type ChartKind,
  type IdeBlock,
} from "./ide-blocks";
import {
  CHART_COLORS,
  columnsNotice,
  rowsNotice,
  splitRichText,
  tableChart,
  type RichTable,
  type TableChart,
} from "./rich-text";

const ALIGN_CLASS = {
  left: "text-left",
  center: "text-center",
  right: "text-right",
} as const;

const numberFormat = new Intl.NumberFormat("es", { maximumFractionDigits: 2 });
const compactFormat = new Intl.NumberFormat("es", { notation: "compact", maximumFractionDigits: 1 });

function axisLabel(value: number): string {
  return Math.abs(value) >= 10_000 ? compactFormat.format(value) : numberFormat.format(value);
}

/**
 * Barras agrupadas o líneas, según lo que pidió quien acuñó la gráfica.
 *
 * Se desplaza dentro de su caja en vez de comprimir los grupos: veinte barras
 * aplastadas en 300px no son un dato, son una mancha.
 */
function SeriesChart({
  chart,
  kind,
  caption,
}: {
  chart: TableChart;
  kind: ChartKind;
  caption: string;
}) {
  const padLeft = 46;
  const padRight = 14;
  const padTop = 12;
  const plotHeight = 190;
  const labelHeight = 30;
  const barWidth = 16;
  const barGap = 4;
  const groupPadding = 16;
  const groupWidth =
    kind === "bar"
      ? chart.series.length * barWidth + (chart.series.length - 1) * barGap + groupPadding
      : 56;
  const width = Math.max(320, padLeft + chart.labels.length * groupWidth + padRight);
  const height = padTop + plotHeight + labelHeight;

  const values = chart.series.flatMap((serie) => serie.values);
  // Una BARRA se mide desde cero: si no arranca en cero, su largo exagera la
  // diferencia. Una LÍNEA no: forzarle el cero aplasta justo la forma que la
  // gráfica viene a mostrar (de 420 ms a 510 ms se ve plano contra un eje que
  // empieza en 0). Swift Charts hace exactamente esto en el teléfono — barras
  // contra cero, líneas ajustadas al dato — y las dos pantallas tienen que
  // dibujar la misma curva.
  const dataMax = Math.max(...values);
  const dataMin = Math.min(...values);
  const top = kind === "bar" ? Math.max(0, dataMax) : dataMax;
  const bottom = kind === "bar" ? Math.min(0, dataMin) : dataMin;
  // Todo el mismo valor: sin margen quedaría pegado a un borde, que se lee como
  // si valiera cero. Se centra abriendo el dominio.
  const plano = top === bottom;
  const rawMax = plano ? top + 1 : top;
  const rawMin = plano ? bottom - 1 : bottom;
  const span = rawMax - rawMin;
  const scale = (value: number) => padTop + plotHeight - ((value - rawMin) / span) * plotHeight;
  const zeroY = scale(0);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((ratio) => rawMin + ratio * span);
  const centerX = (index: number) => padLeft + index * groupWidth + groupWidth / 2;

  return (
    <div className="overflow-x-auto">
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={caption}
        className="block"
      >
        {/* La clave va por posición y no por valor: una tabla de puros ceros
            genera cinco marcas idénticas y React se quejaría de claves repetidas. */}
        {ticks.map((tick, tickIndex) => {
          const y = scale(tick);
          return (
            <g key={tickIndex}>
              <line
                x1={padLeft}
                x2={width - padRight}
                y1={y}
                y2={y}
                className="stroke-slate-200 dark:stroke-slate-700"
                strokeWidth={1}
              />
              <text x={padLeft - 6} y={y + 3} textAnchor="end" className="fill-slate-400 text-[10px]">
                {axisLabel(tick)}
              </text>
            </g>
          );
        })}

        {kind === "line" &&
          chart.series.map((serie, serieIndex) => (
            <polyline
              key={serie.name}
              fill="none"
              stroke={CHART_COLORS[serieIndex % CHART_COLORS.length]}
              strokeWidth={2}
              strokeLinejoin="round"
              strokeLinecap="round"
              points={serie.values.map((value, index) => `${centerX(index)},${scale(value)}`).join(" ")}
            />
          ))}

        {chart.labels.map((label, groupIndex) => (
          <g key={`${label}-${groupIndex}`}>
            {kind === "bar"
              ? chart.series.map((serie, serieIndex) => {
                  const value = serie.values[groupIndex] ?? 0;
                  const y = scale(value);
                  return (
                    <rect
                      key={serie.name}
                      x={padLeft + groupIndex * groupWidth + groupPadding / 2 + serieIndex * (barWidth + barGap)}
                      y={Math.min(y, zeroY)}
                      width={barWidth}
                      height={Math.max(1, Math.abs(zeroY - y))}
                      rx={3}
                      fill={CHART_COLORS[serieIndex % CHART_COLORS.length]}
                    >
                      <title>{`${label} · ${serie.name}: ${numberFormat.format(value)}`}</title>
                    </rect>
                  );
                })
              : chart.series.map((serie, serieIndex) => (
                  <circle
                    key={serie.name}
                    cx={centerX(groupIndex)}
                    cy={scale(serie.values[groupIndex] ?? 0)}
                    r={3}
                    fill={CHART_COLORS[serieIndex % CHART_COLORS.length]}
                  >
                    <title>
                      {`${label} · ${serie.name}: ${numberFormat.format(serie.values[groupIndex] ?? 0)}`}
                    </title>
                  </circle>
                ))}
            <text
              x={centerX(groupIndex)}
              y={padTop + plotHeight + 16}
              textAnchor="middle"
              className="fill-slate-500 text-[10px] dark:fill-slate-400"
            >
              {label.length > 12 ? `${label.slice(0, 11)}…` : label}
            </text>
          </g>
        ))}
      </svg>
    </div>
  );
}

function ChartLegend({ chart }: { chart: TableChart }) {
  if (chart.series.length < 2) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
      {chart.series.map((serie, index) => (
        <span key={serie.name} className="flex items-center gap-1.5 text-[11px] text-slate-500 dark:text-slate-400">
          <span
            className="h-2 w-2 rounded-sm"
            style={{ backgroundColor: CHART_COLORS[index % CHART_COLORS.length] }}
            aria-hidden="true"
          />
          {serie.name}
        </span>
      ))}
    </div>
  );
}

function BlockCaption({ children }: { children: string }) {
  return <p className="mt-2 text-[11px] text-slate-500 dark:text-slate-400">{children}</p>;
}

function CardShell({ title, children }: { title?: string | null; children: React.ReactNode }) {
  return (
    <figure className="my-3 overflow-hidden rounded-xl border border-slate-200 dark:border-slate-700">
      {title && (
        <figcaption className="border-b border-slate-100 px-3 py-2 text-[13px] font-semibold text-slate-700 dark:border-slate-800 dark:text-slate-200">
          {title}
        </figcaption>
      )}
      {children}
    </figure>
  );
}

function ViewToggle({
  view,
  onChange,
}: {
  view: "tabla" | "grafica";
  onChange: (next: "tabla" | "grafica") => void;
}) {
  return (
    <div className="flex gap-0.5 rounded-lg bg-slate-100 p-0.5 dark:bg-slate-800">
      {(["tabla", "grafica"] as const).map((option) => (
        <button
          key={option}
          type="button"
          onClick={() => onChange(option)}
          aria-pressed={view === option}
          className={`rounded-md px-2 py-1 text-[11px] font-medium transition ${
            view === option
              ? "bg-white text-slate-900 shadow-sm dark:bg-slate-950 dark:text-white"
              : "text-slate-500 hover:text-slate-700 dark:text-slate-400"
          }`}
        >
          {option === "tabla" ? "Tabla" : "Gráfica"}
        </button>
      ))}
    </div>
  );
}

function DataGrid({ table }: { table: RichTable }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-left text-[13px]">
        <thead>
          <tr className="bg-slate-50 dark:bg-slate-900/60">
            {table.headers.map((header, index) => (
              <th
                key={`${header}-${index}`}
                scope="col"
                className={`whitespace-nowrap px-3 py-2 font-semibold text-slate-600 dark:text-slate-300 ${ALIGN_CLASS[table.aligns[index] ?? "left"]}`}
              >
                {header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row, rowIndex) => (
            <tr key={rowIndex} className="border-t border-slate-100 align-top dark:border-slate-800">
              {row.map((cell, cellIndex) => (
                <td
                  key={cellIndex}
                  className={`px-3 py-1.5 text-slate-700 dark:text-slate-200 ${ALIGN_CLASS[table.aligns[cellIndex] ?? "left"]}`}
                >
                  {/* Una celda sin dato se marca; nunca un cero, que sería inventado. */}
                  {cell === "" ? <span className="text-slate-300 dark:text-slate-600">—</span> : cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function AgentDataTable({
  table,
  title,
  note,
}: {
  table: RichTable;
  title?: string | null;
  note?: string | null;
}) {
  const chart = useMemo(() => tableChart(table), [table]);
  const [view, setView] = useState<"tabla" | "grafica">("tabla");
  const filas = rowsNotice(table);
  const columnas = columnsNotice(table);
  const showChart = view === "grafica" && chart !== null;

  return (
    <CardShell title={title}>
      {chart && (
        <div className="flex items-center justify-between gap-2 border-b border-slate-100 px-2 py-1.5 dark:border-slate-800">
          <span className="pl-1 text-[11px] text-slate-400">
            {table.rows.length} {table.rows.length === 1 ? "fila" : "filas"} · {table.headers.length} columnas
          </span>
          <ViewToggle view={view} onChange={setView} />
        </div>
      )}

      {showChart ? (
        <div className="p-3">
          <SeriesChart
            chart={chart}
            kind="bar"
            caption={`Gráfica de ${chart.series.map((serie) => serie.name).join(", ")} por ${table.headers[0] || "categoría"}`}
          />
          <ChartLegend chart={chart} />
          {chart.omittedSeries > 0 && (
            <p className="mt-2 text-[11px] text-amber-600 dark:text-amber-400">
              La gráfica muestra {chart.series.length} de {chart.series.length + chart.omittedSeries} columnas
              numéricas. Cambia a Tabla para verlas todas.
            </p>
          )}
        </div>
      ) : (
        <DataGrid table={table} />
      )}

      {(filas || note || (columnas && !showChart)) && (
        <div className="border-t border-slate-100 px-3 py-1.5 text-[11px] text-slate-400 dark:border-slate-800">
          {[filas, showChart ? null : columnas, note].filter(Boolean).join(" ")}
        </div>
      )}
    </CardShell>
  );
}

function IdeChartCard({ block }: { block: Extract<IdeBlock, { type: "chart" }> }) {
  const chart = useMemo(() => chartFromIdeBlock(block), [block]);
  if (!chart) {
    // Una gráfica que no se puede dibujar cae al texto equivalente, nunca a nada.
    return <p className="my-2 text-sm text-slate-500 dark:text-slate-400">{block.fallback_text}</p>;
  }
  const ejes = [block.x_label && `X: ${block.x_label}`, block.y_label && `Y: ${block.y_label}`]
    .filter(Boolean)
    .join(" · ");
  return (
    <CardShell title={block.title}>
      <div className="p-3">
        <SeriesChart chart={chart} kind={block.chart_kind} caption={block.fallback_text} />
        <ChartLegend chart={chart} />
        {ejes && <BlockCaption>{ejes}</BlockCaption>}
        {chart.omittedSeries > 0 && (
          <p className="mt-2 text-[11px] text-amber-600 dark:text-amber-400">
            {/* Mismo texto que iOS (`GraficaIDEView`), singular incluido: la
                versión anterior decía "1 serie llegó incompleta y no se dibujaron". */}
            {chart.omittedSeries === 1
              ? "1 serie llegó incompleta y no se dibujó."
              : `${chart.omittedSeries} series llegaron incompletas y no se dibujaron.`}
          </p>
        )}
        {block.note && <BlockCaption>{block.note}</BlockCaption>}
      </div>
    </CardShell>
  );
}

/** Tarjetas de los bloques tipados que trajo un turno del IDE. */
export function IdeBlockCards({ blocks }: { blocks: IdeBlock[] }) {
  if (blocks.length === 0) return null;
  return (
    <>
      {blocks.map((block, index) =>
        block.type === "table" ? (
          <AgentDataTable
            key={`ide-table-${index}`}
            table={tableFromIdeBlock(block)}
            title={block.title}
            note={block.note}
          />
        ) : (
          <IdeChartCard key={`ide-chart-${index}`} block={block} />
        ),
      )}
    </>
  );
}

export default function AgentRichText({ text }: { text: string }) {
  const segments = useMemo(() => splitRichText(text), [text]);
  return (
    <>
      {segments.map((segment, index) =>
        segment.kind === "table" ? (
          <AgentDataTable key={`table-${index}`} table={segment.table} />
        ) : (
          <MarkdownText key={`text-${index}`} text={segment.text} />
        ),
      )}
    </>
  );
}
