"use client";

/**
 * Campañas + métricas del período (`GET /v1/ads/resumen`) — lectura en vivo
 * del proveedor del tenant (Meta real, o `StubAdsProvider` offline si nadie
 * conectó una cuenta todavía). Solo lectura: no hay ningún botón acá que
 * cree o cambie nada en Meta — eso vive exclusivamente en `BorradoresAds`
 * (`docs/ads.md`, guardrail de dinero).
 */

import { useEffect, useState } from "react";

import { Alert, Badge, Card, CardBody, CardHeader, EmptyState, Select, Spinner } from "@/components/ui";
import { ApiError, getAdsResumen, type AdsResumen } from "@/lib/api-ads";
import { formatMoney, formatNumber } from "@/lib/format";

const PERIODOS: { value: string; label: string }[] = [
  { value: "today", label: "Hoy" },
  { value: "yesterday", label: "Ayer" },
  { value: "last_7d", label: "Últimos 7 días" },
  { value: "last_30d", label: "Últimos 30 días" },
  { value: "this_month", label: "Este mes" },
  { value: "last_month", label: "Mes pasado" },
];

function mensajeError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "No se pudo cargar el resumen.";
}

function metrica(valor: string | number | undefined): string {
  if (valor === undefined || valor === null) return "—";
  return formatNumber(valor);
}

/** `spend`/`cpc` son valores monetarios (a diferencia de `impressions`/`clicks`,
 * conteos puros) — se formatean con la moneda real de la cuenta cuando se
 * conoce (`AdsStatus.moneda`, solo disponible con una cuenta de Meta
 * conectada); sin cuenta conectada (datos de ejemplo del stub) se muestran
 * como número simple, sin inventar una moneda. */
function metricaMonetaria(valor: string | number | undefined, moneda: string | null | undefined): string {
  if (valor === undefined || valor === null) return "—";
  return moneda ? formatMoney(valor, moneda) : formatNumber(valor);
}

export function ResumenCampanas({ conectado, moneda }: { conectado: boolean; moneda: string | null | undefined }) {
  const [periodo, setPeriodo] = useState("last_30d");
  const [resumen, setResumen] = useState<AdsResumen | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelado = false;
    setLoading(true);
    setError(null);
    getAdsResumen(periodo)
      .then((r) => {
        if (!cancelado) setResumen(r);
      })
      .catch((err) => {
        if (!cancelado) setError(mensajeError(err));
      })
      .finally(() => {
        if (!cancelado) setLoading(false);
      });
    return () => {
      cancelado = true;
    };
  }, [periodo]);

  const metricas = resumen?.metricas;
  const campanas = resumen?.campanas ?? [];

  return (
    <Card>
      <CardHeader
        title="Campañas y métricas"
        description="Lectura en vivo de tu cuenta de Meta — nunca queda en caché."
        actions={
          <Select value={periodo} onChange={(e) => setPeriodo(e.target.value)} className="w-auto" disabled={loading}>
            {PERIODOS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </Select>
        }
      />
      <CardBody className="space-y-4">
        {!conectado && (
          <Alert variant="info">
            Datos de ejemplo (modo offline) — conecta tu cuenta de Meta arriba para ver campañas y
            métricas reales.
          </Alert>
        )}
        {error && <Alert variant="error">{error}</Alert>}

        {loading ? (
          <div className="flex justify-center py-8">
            <Spinner className="h-5 w-5 text-slate-400" />
          </div>
        ) : (
          <>
            {metricas && (
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
                <Metrica label="Gasto" value={metricaMonetaria(metricas.spend, moneda)} />
                <Metrica label="Impresiones" value={metrica(metricas.impressions)} />
                <Metrica label="Clics" value={metrica(metricas.clicks)} />
                <Metrica label="CPC" value={metricaMonetaria(metricas.cpc, moneda)} />
                <Metrica label="CTR" value={metrica(metricas.ctr)} />
              </div>
            )}

            {campanas.length === 0 ? (
              <EmptyState
                title="Sin campañas"
                description="Todavía no hay campañas en esta cuenta de anuncios."
              />
            ) : (
              <ul className="space-y-2">
                {campanas.map((c, i) => (
                  <li
                    key={c.id ?? i}
                    className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-slate-50 px-3 py-2 text-sm dark:bg-slate-950/40"
                  >
                    <span className="text-slate-700 dark:text-slate-200">{c.name ?? "(sin nombre)"}</span>
                    <span className="flex items-center gap-2">
                      {c.objective && <Badge variant="neutral">{String(c.objective)}</Badge>}
                      <Badge variant={c.status === "PAUSED" ? "warning" : "success"}>
                        {c.status ? String(c.status) : "—"}
                      </Badge>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </CardBody>
    </Card>
  );
}

function Metrica({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-slate-100 px-3 py-2 dark:border-slate-800">
      <p className="text-xs font-medium uppercase tracking-wide text-slate-400">{label}</p>
      <p className="mt-0.5 text-sm font-semibold text-slate-800 dark:text-slate-100">{value}</p>
    </div>
  );
}
