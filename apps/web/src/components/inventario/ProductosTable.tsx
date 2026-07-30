"use client";

import { useState } from "react";

import { Badge, Button, EmptyState, Spinner } from "@/components/ui";
import type { Producto } from "@/lib/api-inventario";
import { formatMoney, formatNumber } from "@/lib/format";

export function ProductosTable({
  productos,
  loading,
  onEdit,
  onMovimiento,
  onToggleActivo,
}: {
  productos: Producto[];
  loading: boolean;
  onEdit: (producto: Producto) => void;
  onMovimiento: (producto: Producto) => void;
  onToggleActivo: (producto: Producto) => Promise<void>;
}) {
  const [busyId, setBusyId] = useState<string | null>(null);

  async function handleToggle(producto: Producto) {
    setBusyId(producto.id);
    try {
      await onToggleActivo(producto);
    } finally {
      setBusyId(null);
    }
  }

  if (loading) {
    return (
      <div className="flex justify-center py-8">
        <Spinner className="h-5 w-5 text-slate-400" />
      </div>
    );
  }

  if (productos.length === 0) {
    return (
      <EmptyState
        title="Sin productos todavía"
        description="Crea el primero con el formulario de arriba."
      />
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-slate-100 text-xs uppercase tracking-wide text-slate-400 dark:border-slate-800">
            <th className="py-2 pr-3 font-medium">SKU</th>
            <th className="py-2 pr-3 font-medium">Nombre</th>
            <th className="py-2 pr-3 text-right font-medium">Stock</th>
            <th className="py-2 pr-3 text-right font-medium">Precio</th>
            <th className="py-2 pr-3 font-medium">Estado</th>
            <th className="py-2 pr-3 font-medium">Acciones</th>
          </tr>
        </thead>
        <tbody>
          {productos.map((p) => {
            const stockBajo = p.stock <= p.stock_minimo;
            return (
              <tr key={p.id} className="border-b border-slate-50 dark:border-slate-800/60">
                <td className="py-2 pr-3 whitespace-nowrap font-medium text-slate-700 dark:text-slate-200">
                  {p.sku}
                </td>
                <td className="py-2 pr-3 text-slate-700 dark:text-slate-200">
                  <div>{p.nombre}</div>
                  {p.descripcion && (
                    <div className="text-xs text-slate-400">{p.descripcion}</div>
                  )}
                </td>
                <td className="py-2 pr-3 whitespace-nowrap text-right">
                  <Badge variant={stockBajo ? "danger" : "neutral"}>
                    {formatNumber(p.stock)} {p.unidad}
                  </Badge>
                  {stockBajo && (
                    <div className="mt-0.5 text-[11px] text-rose-500">
                      mínimo {formatNumber(p.stock_minimo)}
                    </div>
                  )}
                </td>
                <td className="py-2 pr-3 whitespace-nowrap text-right text-slate-700 dark:text-slate-200">
                  {p.precio === null ? "—" : formatMoney(p.precio)}
                </td>
                <td className="py-2 pr-3">
                  <Badge variant={p.activo ? "success" : "neutral"}>
                    {p.activo ? "Activo" : "Inactivo"}
                  </Badge>
                </td>
                <td className="py-2 pr-3">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Button size="sm" variant="secondary" onClick={() => onEdit(p)}>
                      Editar
                    </Button>
                    {/* Sin `disabled={!p.activo}` a propósito: el backend
                        (`edecan_business.inventory.registrar_movimiento`) no restringe
                        movimientos por `activo` — un producto desactivado puede necesitar un
                        último ajuste (p. ej. liquidar stock remanente) antes de retirarlo del
                        todo. Deshabilitarlo acá sin esa misma regla en la API solo generaría
                        una UI más restrictiva que lo que el backend en verdad permite. */}
                    <Button size="sm" variant="secondary" onClick={() => onMovimiento(p)}>
                      Movimiento
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={busyId === p.id}
                      onClick={() => handleToggle(p)}
                    >
                      {p.activo ? "Desactivar" : "Reactivar"}
                    </Button>
                    {busyId === p.id && <Spinner className="h-3.5 w-3.5 text-slate-400" />}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
