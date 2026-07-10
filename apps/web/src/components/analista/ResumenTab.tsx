"use client";

/**
 * Pestaña "Resumen" de `/app/analista`: tabla de estadística por columna + chips de outliers
 * (`POST /v1/analista/{file_id}/resumen`, sin `hoja` — un archivo XLSX con varias hojas usa
 * siempre la activa/primera desde esta pantalla; elegir otra hoja queda solo disponible por
 * chat, ver `docs/analista.md`).
 */

import { useEffect, useState } from "react";

import { Alert, Badge, Card, CardBody, CardHeader, FullPageSpinner } from "@/components/ui";
import { ApiError, obtenerResumen, type ResumenTabla, type StatsColumna } from "@/lib/api-analista";

function formatearNumero(n: number): string {
  return Number.isInteger(n) ? String(n) : n.toFixed(2);
}

function resumenDeStats(stats: StatsColumna): string {
  if (stats.tipo === "numerica") {
    return (
      `media ${formatearNumero(stats.media)} · mediana ${formatearNumero(stats.mediana)} · ` +
      `min ${formatearNumero(stats.min)} · max ${formatearNumero(stats.max)} · ` +
      `desv. estándar ${formatearNumero(stats.std)} · ${stats.nulos} nulo(s)`
    );
  }
  const top = stats.top.map((t) => `${t.valor} (${t.conteo})`).join(", ");
  return `${stats.nulos} nulo(s)${top ? ` · más frecuentes: ${top}` : ""}`;
}

export function ResumenTab({ fileId }: { fileId: string }) {
  const [resumen, setResumen] = useState<ResumenTabla | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelado = false;
    setLoading(true);
    setError(null);
    setResumen(null);
    obtenerResumen(fileId)
      .then((r) => {
        if (!cancelado) setResumen(r);
      })
      .catch((err) => {
        if (!cancelado) {
          setError(err instanceof ApiError ? err.message : "No se pudo analizar el archivo.");
        }
      })
      .finally(() => {
        if (!cancelado) setLoading(false);
      });
    return () => {
      cancelado = true;
    };
  }, [fileId]);

  if (loading) return <FullPageSpinner label="Analizando archivo…" />;
  if (error) return <Alert variant="error">{error}</Alert>;
  if (!resumen) return null;

  return (
    <div className="space-y-4">
      <p className="text-xs text-slate-500 dark:text-slate-400">
        {resumen.filas_leidas} fila(s) analizadas
        {resumen.total_filas_archivo > resumen.filas_leidas &&
          ` de ${resumen.total_filas_archivo} en total (recortado por límite de tamaño)`}
        {" · "}
        {resumen.columnas.length} columna(s)
      </p>

      <Card>
        <CardHeader
          title="Estadística por columna"
          description="Media, mediana, valores atípicos (IQR) y las categorías más frecuentes."
        />
        <CardBody className="overflow-x-auto p-0">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead className="border-b border-slate-100 text-xs uppercase tracking-wide text-slate-500 dark:border-slate-800 dark:text-slate-400">
              <tr>
                <th className="px-5 py-2 font-medium">Columna</th>
                <th className="px-5 py-2 font-medium">Tipo</th>
                <th className="px-5 py-2 font-medium">Estadística</th>
                <th className="px-5 py-2 font-medium">Outliers</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {resumen.columnas.map((col) => {
                const stats = resumen.stats[col.nombre];
                const outliers = resumen.outliers[col.nombre];
                return (
                  <tr key={col.nombre}>
                    <td className="px-5 py-3 font-medium text-slate-900 dark:text-slate-100">
                      {col.nombre}
                    </td>
                    <td className="px-5 py-3">
                      <Badge variant={col.tipo === "numerica" ? "brand" : "neutral"}>{col.tipo}</Badge>
                    </td>
                    <td className="px-5 py-3 text-slate-600 dark:text-slate-300">
                      {stats ? resumenDeStats(stats) : "—"}
                    </td>
                    <td className="px-5 py-3">
                      {outliers && outliers.conteo > 0 ? (
                        <Badge variant="warning">
                          {outliers.conteo} atípico{outliers.conteo === 1 ? "" : "s"}
                        </Badge>
                      ) : (
                        <span className="text-slate-400 dark:text-slate-600">—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </CardBody>
      </Card>
    </div>
  );
}
