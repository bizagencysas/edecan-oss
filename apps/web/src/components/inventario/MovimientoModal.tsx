"use client";

import { useState } from "react";

import { Alert, Button, Field, Input, Select, Textarea } from "@/components/ui";
import {
  registrarMovimiento,
  type MovimientoMotivo,
  type MovimientoResultado,
  type Producto,
} from "@/lib/api-inventario";
import { formatNumber } from "@/lib/format";

type Tipo = "entrada" | "salida" | "ajuste";

const MOTIVOS_ENTRADA: { value: MovimientoMotivo; label: string }[] = [
  { value: "compra", label: "Compra" },
  { value: "devolucion", label: "Devolución" },
];
const MOTIVOS_SALIDA: { value: MovimientoMotivo; label: string }[] = [
  { value: "venta", label: "Venta" },
  { value: "merma", label: "Merma" },
];

/** Modal "Registrar movimiento" (entrada/salida/ajuste) — mismo patrón de overlay que
 * `ConfirmModal` de `/app/ordenes` (sin un `Modal` compartido en `components/ui.tsx`, cada
 * página arma el suyo con `fixed inset-0` + `role="dialog"`).
 *
 * "Entrada"/"Salida" piden una CANTIDAD siempre positiva (la UI arma el signo del `delta`
 * por vos); "Ajuste" es la única vía para escribir un `delta` con signo explícito a mano —
 * mismo vocabulario que `edecan_business.inventory.registrar_movimiento`: solo `motivo=
 * 'ajuste'` puede dejar el stock en negativo. */
export function MovimientoModal({
  producto,
  onClose,
  onRegistrado,
}: {
  producto: Producto;
  onClose: () => void;
  onRegistrado: (resultado: MovimientoResultado) => void;
}) {
  const [tipo, setTipo] = useState<Tipo>("entrada");
  const [motivo, setMotivo] = useState<MovimientoMotivo>("compra");
  const [cantidad, setCantidad] = useState("");
  const [nota, setNota] = useState("");
  const [ref, setRef] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function handleTipoChange(nuevo: Tipo) {
    setTipo(nuevo);
    if (nuevo === "entrada") setMotivo("compra");
    else if (nuevo === "salida") setMotivo("venta");
    else setMotivo("ajuste");
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    const magnitud = Number(cantidad);
    if (!cantidad.trim() || Number.isNaN(magnitud) || magnitud === 0) {
      setError("Ingresa una cantidad válida y distinta de cero.");
      return;
    }
    if (tipo !== "ajuste" && magnitud < 0) {
      setError("La cantidad debe ser positiva — el tipo de movimiento ya define el signo.");
      return;
    }

    const delta = tipo === "entrada" ? Math.abs(magnitud) : tipo === "salida" ? -Math.abs(magnitud) : magnitud;

    setLoading(true);
    try {
      const resultado = await registrarMovimiento(producto.id, {
        delta,
        motivo,
        nota: nota.trim(),
        ref: ref.trim() || null,
      });
      onRegistrado(resultado);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo registrar el movimiento.");
    } finally {
      setLoading(false);
    }
  }

  const opcionesMotivo = tipo === "entrada" ? MOTIVOS_ENTRADA : tipo === "salida" ? MOTIVOS_SALIDA : null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="movimiento-titulo"
    >
      <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-5 shadow-xl dark:border-slate-800 dark:bg-slate-900">
        <h3 id="movimiento-titulo" className="text-sm font-semibold text-slate-900 dark:text-slate-100">
          Registrar movimiento — {producto.sku}
        </h3>
        <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
          Stock actual: {formatNumber(producto.stock)} {producto.unidad} (mínimo{" "}
          {formatNumber(producto.stock_minimo)})
        </p>

        <form onSubmit={handleSubmit} className="mt-4 space-y-3">
          {error && <Alert variant="error">{error}</Alert>}

          <Field label="Tipo de movimiento" htmlFor="tipo">
            <Select
              id="tipo"
              value={tipo}
              onChange={(e) => handleTipoChange(e.target.value as Tipo)}
            >
              <option value="entrada">Entrada (suma stock)</option>
              <option value="salida">Salida (resta stock)</option>
              <option value="ajuste">Ajuste (corrección manual, puede quedar en negativo)</option>
            </Select>
          </Field>

          {opcionesMotivo && (
            <Field label="Motivo" htmlFor="motivo">
              <Select
                id="motivo"
                value={motivo}
                onChange={(e) => setMotivo(e.target.value as MovimientoMotivo)}
              >
                {opcionesMotivo.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </Select>
            </Field>
          )}

          <Field
            label={tipo === "ajuste" ? "Cantidad a ajustar (+/-)" : "Cantidad"}
            htmlFor="cantidad"
            hint={
              tipo === "ajuste"
                ? "Positiva para sumar, negativa para restar."
                : "Siempre positiva — el tipo ya define si suma o resta."
            }
          >
            <Input
              id="cantidad"
              type="number"
              step="0.001"
              value={cantidad}
              onChange={(e) => setCantidad(e.target.value)}
              placeholder={tipo === "ajuste" ? "p. ej. -2 o 5" : "p. ej. 5"}
            />
          </Field>

          <Field label="Nota (opcional)" htmlFor="nota">
            <Textarea id="nota" rows={1} value={nota} onChange={(e) => setNota(e.target.value)} />
          </Field>

          <Field label="Referencia (opcional)" htmlFor="ref" hint="p. ej. número de factura u orden.">
            <Input id="ref" value={ref} onChange={(e) => setRef(e.target.value)} />
          </Field>

          <div className="mt-5 flex justify-end gap-2">
            <Button type="button" variant="secondary" onClick={onClose} disabled={loading}>
              Cancelar
            </Button>
            <Button type="submit" loading={loading}>
              Registrar
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
