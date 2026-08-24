"use client";

import type { CanalVenta } from "@/lib/api-negocios";
import { formatNumber } from "@/lib/format";

/**
 * Dona SVG a mano (stroke-dasharray sobre círculos apilados) — `ROADMAP_V2.md` §7.10:
 * "gráficos = SVG a mano", cero dependencias npm nuevas. Cada canal es un `<circle>`
 * separado, todos centrados y rotados -90° para empezar en las 12, con
 * `stroke-dashoffset` acumulado para que cada segmento arranque donde terminó el anterior.
 *
 * Los totales que llegan aquí (`kpis.por_canal`) suman `transactions.monto` SIN agrupar
 * por `moneda` (mismo criterio que `finanzas/page.tsx` con `formatNumber` en vez de
 * `formatMoney`, ver el docstring de `lib/format.ts`): un tenant que registra ingresos en
 * más de una moneda vería un símbolo de moneda engañoso, así que esta dona muestra números
 * sin símbolo, no un monto con "USD" hardcodeado.
 */

const COLORS = ["#6366f1", "#0ea5e9", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#64748b"];

const SIZE = 168;
const STROKE = 22;
const RADIUS = (SIZE - STROKE) / 2;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

export function DonutChart({ data }: { data: CanalVenta[] }) {
  const total = data.reduce((acc, d) => acc + Math.max(0, d.total), 0);

  if (data.length === 0 || total <= 0) {
    return <p className="py-8 text-center text-sm text-slate-400">Sin ventas este mes.</p>;
  }

  let acumulado = 0;
  const segments = data.map((d, i) => {
    const valor = Math.max(0, d.total);
    const fraction = valor / total;
    const dash = fraction * CIRCUMFERENCE;
    const offset = -acumulado * CIRCUMFERENCE;
    acumulado += fraction;
    return { ...d, color: COLORS[i % COLORS.length], dash, offset, pct: fraction * 100 };
  });

  return (
    <div className="flex flex-col items-center gap-5 sm:flex-row sm:justify-center">
      <svg
        viewBox={`0 0 ${SIZE} ${SIZE}`}
        width={SIZE}
        height={SIZE}
        className="shrink-0"
        role="img"
        aria-label="Ventas por canal"
      >
        <circle
          cx={SIZE / 2}
          cy={SIZE / 2}
          r={RADIUS}
          fill="none"
          stroke="currentColor"
          strokeWidth={STROKE}
          className="text-slate-100 dark:text-slate-800"
        />
        {segments.map((s) => (
          <circle
            key={s.canal}
            cx={SIZE / 2}
            cy={SIZE / 2}
            r={RADIUS}
            fill="none"
            stroke={s.color}
            strokeWidth={STROKE}
            strokeDasharray={`${s.dash} ${CIRCUMFERENCE - s.dash}`}
            strokeDashoffset={s.offset}
            strokeLinecap="butt"
            transform={`rotate(-90 ${SIZE / 2} ${SIZE / 2})`}
          >
            <title>{`${s.canal}: ${formatNumber(s.total)} (${s.pct.toFixed(1)}%)`}</title>
          </circle>
        ))}
        <text
          x={SIZE / 2}
          y={SIZE / 2}
          textAnchor="middle"
          dominantBaseline="middle"
          className="fill-slate-700 text-sm font-semibold dark:fill-slate-100"
        >
          {formatNumber(total)}
        </text>
      </svg>
      <ul className="flex min-w-0 flex-col gap-1.5 text-sm">
        {segments.map((s) => (
          <li
            key={s.canal}
            className="flex items-center gap-2"
            title={`${formatNumber(s.total)} (${s.pct.toFixed(1)}%)`}
          >
            <span
              className="h-2.5 w-2.5 shrink-0 rounded-full"
              style={{ backgroundColor: s.color }}
            />
            <span className="truncate text-slate-600 dark:text-slate-300">{s.canal}</span>
            <span className="ml-auto shrink-0 font-medium text-slate-800 dark:text-slate-100">
              {s.pct.toFixed(0)}%
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
