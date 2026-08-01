"use client";

/**
 * Pestaña "Pronóstico" de `/app/analista`: elige columna de fecha/valor (autodetectadas del
 * resumen, igual criterio que el propio backend en `POST /v1/analista/{file_id}/forecast` —
 * ver su docstring) + horizonte (1-24), y muestra la tabla de proyección + la lista de
 * anomalías con severidad sobre la misma serie.
 */

import { useEffect, useState } from "react";

import { Alert, Badge, Button, Card, CardBody, CardHeader, Field, Select } from "@/components/ui";
import {
  ApiError,
  obtenerForecast,
  obtenerResumen,
  type AnomaliaOutlier,
  type ForecastResponse,
  type ResumenTabla,
} from "@/lib/api-analista";

const HORIZONTE_MIN = 1;
const HORIZONTE_MAX = 24;
const HORIZONTE_DEFECTO = 6;
const COLOR_BRAND = "#4f46e5"; // brand-600 (tailwind.config.ts) — accentColor del slider nativo

/** Heurística simple de severidad para triage visual: el `score` de `detectar_anomalias` está
 * en unidades de IQR (método "iqr") o desviaciones estándar (método "zscore") — no son
 * estrictamente comparables entre sí, pero para una lista corta de outliers ya filtrados por
 * el umbral del propio método, un corte fijo en |score| alcanza para distinguir "se sale un
 * poco" de "se sale mucho". No es un modelo de severidad estadísticamente riguroso. */
function severidad(score: number): "moderada" | "alta" {
  return Math.abs(score) >= 3 ? "alta" : "moderada";
}

function formatearNumero(n: number): string {
  return Number.isInteger(n) ? String(n) : n.toFixed(2);
}

export function PronosticoTab({ fileId }: { fileId: string }) {
  const [resumen, setResumen] = useState<ResumenTabla | null>(null);
  const [loadingResumen, setLoadingResumen] = useState(true);
  const [errorResumen, setErrorResumen] = useState<string | null>(null);

  const [columnaFecha, setColumnaFecha] = useState("");
  const [columnaValor, setColumnaValor] = useState("");
  const [horizonte, setHorizonte] = useState(HORIZONTE_DEFECTO);

  const [resultado, setResultado] = useState<ForecastResponse | null>(null);
  const [loadingForecast, setLoadingForecast] = useState(false);
  const [errorForecast, setErrorForecast] = useState<string | null>(null);

  useEffect(() => {
    let cancelado = false;
    setLoadingResumen(true);
    setErrorResumen(null);
    setResumen(null);
    setResultado(null);
    obtenerResumen(fileId)
      .then((r) => {
        if (cancelado) return;
        setResumen(r);
        // Autodetección igual criterio que el backend: primera columna numérica para "valor",
        // primera de texto para "fecha" (etiquetas) — el usuario puede cambiarlas antes de enviar.
        setColumnaValor(r.columnas.find((c) => c.tipo === "numerica")?.nombre ?? "");
        setColumnaFecha(r.columnas.find((c) => c.tipo === "texto")?.nombre ?? "");
      })
      .catch((err) => {
        if (!cancelado) {
          setErrorResumen(err instanceof ApiError ? err.message : "No se pudo leer el archivo.");
        }
      })
      .finally(() => {
        if (!cancelado) setLoadingResumen(false);
      });
    return () => {
      cancelado = true;
    };
  }, [fileId]);

  async function handleSubmit() {
    setLoadingForecast(true);
    setErrorForecast(null);
    try {
      const r = await obtenerForecast(fileId, {
        columna_fecha: columnaFecha || undefined,
        columna_valor: columnaValor || undefined,
        horizonte,
      });
      setResultado(r);
    } catch (err) {
      setErrorForecast(err instanceof ApiError ? err.message : "No se pudo calcular el pronóstico.");
    } finally {
      setLoadingForecast(false);
    }
  }

  if (loadingResumen) return <Alert variant="info">Leyendo columnas del archivo…</Alert>;
  if (errorResumen) return <Alert variant="error">{errorResumen}</Alert>;
  if (!resumen) return null;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title="Pronosticar una serie"
          description="Elige qué columna pronosticar. Es una proyección estadística informativa, no asesoría financiera ni garantía."
        />
        <CardBody className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Columna de valor (numérica, obligatoria)" htmlFor="pronostico-valor">
              <Select
                id="pronostico-valor"
                value={columnaValor}
                onChange={(e) => setColumnaValor(e.target.value)}
              >
                <option value="">Selecciona una columna…</option>
                {resumen.columnas.map((c) => (
                  <option key={c.nombre} value={c.nombre}>
                    {c.nombre} {c.tipo === "numerica" ? "" : "(no numérica)"}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Columna de fecha/etiqueta (opcional)" htmlFor="pronostico-fecha">
              <Select
                id="pronostico-fecha"
                value={columnaFecha}
                onChange={(e) => setColumnaFecha(e.target.value)}
              >
                <option value="">(ninguna — usa t+1, t+2…)</option>
                {resumen.columnas.map((c) => (
                  <option key={c.nombre} value={c.nombre}>
                    {c.nombre}
                  </option>
                ))}
              </Select>
            </Field>
          </div>

          <Field
            label={`Horizonte: ${horizonte} período(s)`}
            htmlFor="pronostico-horizonte"
            hint="Cuántos períodos futuros predecir (1 a 24)."
          >
            <input
              id="pronostico-horizonte"
              type="range"
              min={HORIZONTE_MIN}
              max={HORIZONTE_MAX}
              value={horizonte}
              onChange={(e) => setHorizonte(Number(e.target.value))}
              style={{ accentColor: COLOR_BRAND }}
              className="w-full"
            />
          </Field>

          {errorForecast && <Alert variant="error">{errorForecast}</Alert>}

          <Button onClick={handleSubmit} loading={loadingForecast} disabled={!columnaValor}>
            Calcular pronóstico
          </Button>
        </CardBody>
      </Card>

      {resultado && (
        <>
          <Card>
            <CardHeader
              title="Proyección"
              description={`Método elegido: ${resultado.forecast.metodo_legible} (error absoluto medio del backtest: ${formatearNumero(resultado.forecast.mae)}).`}
            />
            <CardBody className="overflow-x-auto p-0">
              <table className="w-full min-w-[420px] text-left text-sm">
                <thead className="border-b border-slate-100 text-xs uppercase tracking-wide text-slate-500 dark:border-slate-800 dark:text-slate-400">
                  <tr>
                    <th className="px-5 py-2 font-medium">Período</th>
                    <th className="px-5 py-2 font-medium">Predicción</th>
                    <th className="px-5 py-2 font-medium">Intervalo aproximado</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                  {resultado.forecast.predicciones.map((p, i) => (
                    <tr key={i}>
                      <td className="px-5 py-3 font-medium text-slate-900 dark:text-slate-100">
                        t+{i + 1}
                      </td>
                      <td className="px-5 py-3 text-slate-600 dark:text-slate-300">
                        {formatearNumero(p)}
                      </td>
                      <td className="px-5 py-3 text-slate-500 dark:text-slate-400">
                        [{formatearNumero(resultado.forecast.intervalo_inferior[i])},{" "}
                        {formatearNumero(resultado.forecast.intervalo_superior[i])}]
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </CardBody>
          </Card>

          {resultado.forecast.aviso && <Alert variant="info">{resultado.forecast.aviso}</Alert>}
          <p className="text-xs text-slate-400 dark:text-slate-500">
            Proyección estadística informativa basada solo en los datos dados; no es asesoría
            financiera ni garantía.
          </p>

          <Card>
            <CardHeader
              title="Anomalías"
              description="Rango intercuartílico sobre la misma serie — una heurística estadística, no un detector de fraude."
            />
            <CardBody>
              {resultado.anomalias_error && (
                <Alert variant="info">{resultado.anomalias_error}</Alert>
              )}
              {resultado.anomalias && resultado.anomalias.outliers.length === 0 && (
                <p className="text-sm text-slate-500 dark:text-slate-400">
                  No se detectaron valores atípicos en esta serie.
                </p>
              )}
              {resultado.anomalias && resultado.anomalias.outliers.length > 0 && (
                <ul className="space-y-2">
                  {resultado.anomalias.outliers.map((o: AnomaliaOutlier) => (
                    <li key={o.indice} className="flex items-center justify-between gap-2 text-sm">
                      <span className="text-slate-700 dark:text-slate-200">
                        {resultado.etiquetas?.[o.indice] || `posición ${o.indice + 1}`}: valor{" "}
                        {formatearNumero(o.valor)} (score {formatearNumero(o.score)})
                      </span>
                      <Badge variant={severidad(o.score) === "alta" ? "danger" : "warning"}>
                        {severidad(o.score)}
                      </Badge>
                    </li>
                  ))}
                </ul>
              )}
              {resultado.anomalias && resultado.anomalias.rachas.length > 0 && (
                <div className="mt-3 space-y-1 border-t border-slate-100 pt-3 text-xs text-slate-500 dark:border-slate-800 dark:text-slate-400">
                  {resultado.anomalias.rachas.map((r, i) => (
                    <p key={i}>
                      {r.longitud} valores seguidos {r.direccion === "por_encima" ? "por encima" : "por debajo"}{" "}
                      de la mediana (posible cambio de patrón).
                    </p>
                  ))}
                </div>
              )}
            </CardBody>
          </Card>
        </>
      )}
    </div>
  );
}
