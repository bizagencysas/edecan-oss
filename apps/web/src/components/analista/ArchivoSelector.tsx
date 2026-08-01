"use client";

/**
 * Panel 1 de `/app/analista` (layout de dos paneles, ver `docs/analista.md`): dropdown de
 * archivos tabulares del tenant (`GET /v1/analista/archivos`). Cuando no hay ninguno, un
 * `EmptyState` con link directo a `/app/archivos` (donde el usuario ya puede subir un
 * CSV/XLSX, `apps/api/edecan_api/routers/files.py`) — nunca un formulario de subida propio
 * acá, para no duplicar esa pantalla.
 */

import Link from "next/link";

import { EmptyState, Field, Select } from "@/components/ui";
import type { ArchivoAnalista } from "@/lib/api-analista";

function formatearTamano(bytes: number | null): string {
  if (bytes === null) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function ArchivoSelector({
  archivos,
  loading,
  selectedId,
  onSelect,
}: {
  archivos: ArchivoAnalista[];
  loading: boolean;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  if (!loading && archivos.length === 0) {
    return (
      <EmptyState
        title="Todavía no tienes archivos analizables"
        description="Sube un CSV o XLSX en Archivos para empezar a ver estadística, pronósticos y gráficos aquí."
        action={
          <Link
            href="/app/archivos"
            className="text-sm font-medium text-brand-600 hover:underline dark:text-brand-400"
          >
            Ir a Archivos
          </Link>
        }
      />
    );
  }

  const seleccionado = archivos.find((a) => a.id === selectedId) ?? null;

  return (
    <div className="space-y-3">
      <Field label="Archivo" htmlFor="analista-archivo">
        <Select
          id="analista-archivo"
          value={selectedId ?? ""}
          disabled={loading}
          onChange={(e) => onSelect(e.target.value)}
        >
          {!selectedId && <option value="">{loading ? "Cargando…" : "Selecciona un archivo"}</option>}
          {archivos.map((a) => (
            <option key={a.id} value={a.id}>
              {a.filename ?? a.id}
            </option>
          ))}
        </Select>
      </Field>
      {seleccionado && (
        <p className="text-xs text-slate-500 dark:text-slate-400">
          {seleccionado.mime ?? "tipo desconocido"}
          {seleccionado.size_bytes !== null && ` · ${formatearTamano(seleccionado.size_bytes)}`}
        </p>
      )}
      <p className="text-xs text-slate-400 dark:text-slate-500">
        ¿Falta un archivo?{" "}
        <Link href="/app/archivos" className="font-medium text-brand-600 hover:underline dark:text-brand-400">
          Súbelo en Archivos
        </Link>
        .
      </p>
    </div>
  );
}
