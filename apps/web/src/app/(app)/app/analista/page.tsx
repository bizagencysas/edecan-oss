"use client";

/**
 * `/app/analista` — pantalla Analista: estadística, pronóstico/anomalías y gráficos SVG de
 * `edecan_docanalysis`, 100% determinista (nunca llama al LLM — esa vía sigue siendo el chat,
 * ver `docs/analista.md`). Layout de dos paneles: selector de archivo (panel 1, columna
 * angosta) + pestañas Resumen / Pronóstico / Gráfico (panel 2, mismo patrón tab-bar que
 * `/app/rrhh` — sin un componente `Tabs` compartido en `components/ui.tsx`, cada página arma
 * el suyo con botones + `border-b-2`).
 */

import { useEffect, useState } from "react";

import { Alert, PageHeader } from "@/components/ui";
import { ArchivoSelector } from "@/components/analista/ArchivoSelector";
import { GraficoTab } from "@/components/analista/GraficoTab";
import { PronosticoTab } from "@/components/analista/PronosticoTab";
import { ResumenTab } from "@/components/analista/ResumenTab";
import { ApiError, listArchivosAnalista, type ArchivoAnalista } from "@/lib/api-analista";

type Tab = "resumen" | "pronostico" | "grafico";

const TABS: { key: Tab; label: string }[] = [
  { key: "resumen", label: "Resumen" },
  { key: "pronostico", label: "Pronóstico" },
  { key: "grafico", label: "Gráfico" },
];

export default function AnalistaPage() {
  const [archivos, setArchivos] = useState<ArchivoAnalista[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("resumen");

  useEffect(() => {
    let cancelado = false;
    setLoading(true);
    setError(null);
    listArchivosAnalista()
      .then((lista) => {
        if (cancelado) return;
        setArchivos(lista);
        // Auto-selecciona el primer archivo (principio de "pocos clicks",
        // DIRECCION_ACTUAL.md) — el usuario puede cambiarlo en el dropdown.
        setSelectedId((actual) => actual ?? lista[0]?.id ?? null);
      })
      .catch((err) => {
        if (!cancelado) {
          setError(err instanceof ApiError ? err.message : "No se pudo cargar la lista de archivos.");
        }
      })
      .finally(() => {
        if (!cancelado) setLoading(false);
      });
    return () => {
      cancelado = true;
    };
  }, []);

  return (
    <div>
      <PageHeader
        title="Analista"
        description="Estadística, pronóstico de series y gráficos sobre tus archivos CSV/XLSX — 100% determinista, sin usar el modelo de lenguaje."
      />

      {error && (
        <div className="mb-4">
          <Alert variant="error">{error}</Alert>
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[280px_1fr]">
        <div>
          <ArchivoSelector
            archivos={archivos}
            loading={loading}
            selectedId={selectedId}
            onSelect={setSelectedId}
          />
        </div>

        <div>
          {selectedId ? (
            <>
              <div className="mb-6 flex gap-1 border-b border-slate-200 dark:border-slate-800">
                {TABS.map((t) => (
                  <button
                    key={t.key}
                    type="button"
                    onClick={() => setTab(t.key)}
                    className={`border-b-2 px-3 py-2 text-sm font-medium transition-colors ${
                      tab === t.key
                        ? "border-brand-600 text-brand-700 dark:text-brand-400"
                        : "border-transparent text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200"
                    }`}
                  >
                    {t.label}
                  </button>
                ))}
              </div>

              {tab === "resumen" && <ResumenTab key={`resumen-${selectedId}`} fileId={selectedId} />}
              {tab === "pronostico" && (
                <PronosticoTab key={`pronostico-${selectedId}`} fileId={selectedId} />
              )}
              {tab === "grafico" && <GraficoTab key={`grafico-${selectedId}`} fileId={selectedId} />}
            </>
          ) : (
            // Sin archivo seleccionado: el panel 1 ya explica el motivo (EmptyState si no hay
            // ninguno; auto-selección si sí hay) — este aviso solo cubre el caso borde de que
            // haya archivos pero, por algún motivo, ninguno quedara seleccionado.
            !loading &&
            archivos.length > 0 && <Alert variant="info">Selecciona un archivo para analizarlo.</Alert>
          )}
        </div>
      </div>
    </div>
  );
}
