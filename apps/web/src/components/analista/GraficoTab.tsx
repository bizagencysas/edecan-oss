"use client";

/**
 * Pestaña "Gráfico" de `/app/analista`: elige tipo + columnas (autodetectadas del resumen,
 * igual criterio que `POST /v1/analista/{file_id}/grafico`) y renderiza el SVG que devuelve
 * el backend, con un botón para copiar el SVG crudo.
 */

import { useEffect, useState } from "react";

import { Alert, Button, Card, CardBody, CardHeader, Field, Select } from "@/components/ui";
import {
  ApiError,
  generarGraficoAnalista,
  obtenerResumen,
  type GraficoResponse,
  type ResumenTabla,
  type TipoGrafico,
} from "@/lib/api-analista";

const TIPOS: { value: TipoGrafico; label: string }[] = [
  { value: "barras", label: "Barras" },
  { value: "lineas", label: "Líneas" },
  { value: "dona", label: "Dona" },
];

export function GraficoTab({ fileId }: { fileId: string }) {
  const [resumen, setResumen] = useState<ResumenTabla | null>(null);
  const [loadingResumen, setLoadingResumen] = useState(true);
  const [errorResumen, setErrorResumen] = useState<string | null>(null);

  const [tipo, setTipo] = useState<TipoGrafico>("barras");
  const [columnaX, setColumnaX] = useState("");
  const [columnaY, setColumnaY] = useState("");

  const [resultado, setResultado] = useState<GraficoResponse | null>(null);
  const [loadingGrafico, setLoadingGrafico] = useState(false);
  const [errorGrafico, setErrorGrafico] = useState<string | null>(null);
  const [copiado, setCopiado] = useState(false);

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
        const primeraNumerica = r.columnas.find((c) => c.tipo === "numerica")?.nombre ?? "";
        const primeraTexto = r.columnas.find(
          (c) => c.tipo === "texto" && c.nombre !== primeraNumerica,
        )?.nombre;
        setColumnaY(primeraNumerica);
        setColumnaX(primeraTexto ?? r.columnas.find((c) => c.nombre !== primeraNumerica)?.nombre ?? "");
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
    setLoadingGrafico(true);
    setErrorGrafico(null);
    setCopiado(false);
    try {
      const r = await generarGraficoAnalista(fileId, {
        tipo,
        columna_x: columnaX || undefined,
        columna_y: columnaY || undefined,
      });
      setResultado(r);
    } catch (err) {
      setErrorGrafico(err instanceof ApiError ? err.message : "No se pudo generar el gráfico.");
    } finally {
      setLoadingGrafico(false);
    }
  }

  async function handleCopiar() {
    if (!resultado) return;
    try {
      await navigator.clipboard.writeText(resultado.svg);
      setCopiado(true);
      setTimeout(() => setCopiado(false), 2000);
    } catch {
      setErrorGrafico("No pude copiar el SVG al portapapeles.");
    }
  }

  if (loadingResumen) return <Alert variant="info">Leyendo columnas del archivo…</Alert>;
  if (errorResumen) return <Alert variant="error">{errorResumen}</Alert>;
  if (!resumen) return null;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title="Generar gráfico"
          description="Barras, líneas o dona a partir de dos columnas de este archivo."
        />
        <CardBody className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Tipo" htmlFor="grafico-tipo">
              <Select
                id="grafico-tipo"
                value={tipo}
                onChange={(e) => setTipo(e.target.value as TipoGrafico)}
              >
                {TIPOS.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Columna X (etiquetas)" htmlFor="grafico-x">
              <Select id="grafico-x" value={columnaX} onChange={(e) => setColumnaX(e.target.value)}>
                <option value="">Selecciona una columna…</option>
                {resumen.columnas.map((c) => (
                  <option key={c.nombre} value={c.nombre}>
                    {c.nombre}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Columna Y (valores numéricos)" htmlFor="grafico-y">
              <Select id="grafico-y" value={columnaY} onChange={(e) => setColumnaY(e.target.value)}>
                <option value="">Selecciona una columna…</option>
                {resumen.columnas.map((c) => (
                  <option key={c.nombre} value={c.nombre}>
                    {c.nombre} {c.tipo === "numerica" ? "" : "(no numérica)"}
                  </option>
                ))}
              </Select>
            </Field>
          </div>

          {errorGrafico && <Alert variant="error">{errorGrafico}</Alert>}

          <Button onClick={handleSubmit} loading={loadingGrafico} disabled={!columnaX || !columnaY}>
            Generar gráfico
          </Button>
        </CardBody>
      </Card>

      {resultado && (
        <Card>
          <CardHeader
            title="Resultado"
            description={
              resultado.truncado
                ? `Se graficaron las primeras ${resultado.puntos_graficados} de ${resultado.puntos_totales} filas.`
                : `${resultado.puntos_graficados} punto(s) graficados.`
            }
            actions={
              <Button variant="secondary" size="sm" onClick={handleCopiar}>
                {copiado ? "¡Copiado!" : "Copiar SVG"}
              </Button>
            }
          />
          <CardBody>
            {/*
              dangerouslySetInnerHTML SOLO porque este SVG lo genera nuestro propio backend
              determinista (`edecan_docanalysis.graficos.generar_svg`, construido a mano en
              Python puro, sin plantillas de usuario ni datos ejecutables — nunca HTML/JS de
              terceros ni entrada arbitraria del usuario insertada tal cual). El endpoint
              `POST /v1/analista/{file_id}/grafico` es de solo lectura y el propio archivo del
              usuario nunca aparece como marcado sin escapar: `generar_svg` escapa etiquetas/
              títulos con `xml.sax.saxutils.escape`/`quoteattr` antes de interpolarlos, ver
              `packages/docanalysis/edecan_docanalysis/graficos.py`.
            */}
            <div
              className="overflow-x-auto rounded-lg bg-white p-2 dark:bg-slate-950"
              dangerouslySetInnerHTML={{ __html: resultado.svg }}
            />
          </CardBody>
        </Card>
      )}
    </div>
  );
}
