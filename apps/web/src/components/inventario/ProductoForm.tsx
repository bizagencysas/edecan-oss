"use client";

import { useEffect, useState } from "react";

import { Alert, Button, Card, CardBody, CardHeader, Field, Input, Textarea } from "@/components/ui";
import type { Producto, ProductoCreateInput, ProductoUpdateInput } from "@/lib/api-inventario";

const CAMPOS_VACIOS = {
  sku: "",
  nombre: "",
  descripcion: "",
  unidad: "unidad",
  precio: "",
  costo: "",
  stockMinimo: "0",
};

/** Formulario único para crear Y editar (`editing` decide el modo) — `sku` es la identidad
 * del producto (`edecan_business.inventory`: no editable), así que en modo edición el campo
 * queda deshabilitado en vez de desaparecer, para que quede claro CUÁL producto se está
 * tocando. */
export function ProductoForm({
  editing,
  onCreate,
  onUpdate,
  onCancelEdit,
  submitting,
}: {
  editing: Producto | null;
  onCreate: (input: ProductoCreateInput) => Promise<void>;
  onUpdate: (id: string, input: ProductoUpdateInput) => Promise<void>;
  onCancelEdit: () => void;
  submitting: boolean;
}) {
  const [campos, setCampos] = useState(CAMPOS_VACIOS);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (editing) {
      setCampos({
        sku: editing.sku,
        nombre: editing.nombre,
        descripcion: editing.descripcion,
        unidad: editing.unidad,
        precio: editing.precio === null ? "" : String(editing.precio),
        costo: editing.costo === null ? "" : String(editing.costo),
        stockMinimo: String(editing.stock_minimo),
      });
      setError(null);
    } else {
      setCampos(CAMPOS_VACIOS);
    }
  }, [editing]);

  function update(patch: Partial<typeof campos>) {
    setCampos((prev) => ({ ...prev, ...patch }));
  }

  function resetForm() {
    setCampos(CAMPOS_VACIOS);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    if (!editing && !campos.sku.trim()) {
      setError("El SKU es obligatorio.");
      return;
    }
    if (!campos.nombre.trim()) {
      setError("El nombre es obligatorio.");
      return;
    }

    const base = {
      nombre: campos.nombre.trim(),
      descripcion: campos.descripcion.trim(),
      unidad: campos.unidad.trim() || "unidad",
      precio: campos.precio.trim() === "" ? null : campos.precio,
      costo: campos.costo.trim() === "" ? null : campos.costo,
      stock_minimo: campos.stockMinimo.trim() === "" ? 0 : campos.stockMinimo,
    };

    try {
      if (editing) {
        await onUpdate(editing.id, base);
      } else {
        await onCreate({ sku: campos.sku.trim(), ...base });
        resetForm();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo guardar el producto.");
    }
  }

  return (
    <Card>
      <CardHeader
        title={editing ? `Editar «${editing.sku}»` : "Nuevo producto"}
        description={
          editing
            ? "El SKU no se puede cambiar — es la identidad del producto."
            : "El stock nace en 0: usa 'Movimiento' para dejar existencias iniciales."
        }
        actions={
          editing && (
            <Button type="button" variant="ghost" size="sm" onClick={onCancelEdit}>
              Cancelar edición
            </Button>
          )
        }
      />
      <CardBody>
        <form onSubmit={handleSubmit} className="space-y-4">
          {error && <Alert variant="error">{error}</Alert>}

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <Field label="SKU" htmlFor="sku">
              <Input
                id="sku"
                value={campos.sku}
                disabled={!!editing}
                onChange={(e) => update({ sku: e.target.value })}
                placeholder="p. ej. ABC-001"
              />
            </Field>
            <Field label="Nombre" htmlFor="nombre" className="sm:col-span-2">
              <Input
                id="nombre"
                value={campos.nombre}
                onChange={(e) => update({ nombre: e.target.value })}
                placeholder="Nombre del producto"
              />
            </Field>
          </div>

          <Field label="Descripción (opcional)" htmlFor="descripcion">
            <Textarea
              id="descripcion"
              rows={2}
              value={campos.descripcion}
              onChange={(e) => update({ descripcion: e.target.value })}
            />
          </Field>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-4">
            <Field label="Unidad" htmlFor="unidad">
              <Input
                id="unidad"
                value={campos.unidad}
                onChange={(e) => update({ unidad: e.target.value })}
                placeholder="unidad, kg, caja…"
              />
            </Field>
            <Field label="Precio de venta (opcional)" htmlFor="precio">
              <Input
                id="precio"
                type="number"
                min="0"
                step="0.01"
                value={campos.precio}
                onChange={(e) => update({ precio: e.target.value })}
              />
            </Field>
            <Field label="Costo (opcional)" htmlFor="costo">
              <Input
                id="costo"
                type="number"
                min="0"
                step="0.01"
                value={campos.costo}
                onChange={(e) => update({ costo: e.target.value })}
              />
            </Field>
            <Field label="Stock mínimo" htmlFor="stock_minimo">
              <Input
                id="stock_minimo"
                type="number"
                min="0"
                step="0.001"
                value={campos.stockMinimo}
                onChange={(e) => update({ stockMinimo: e.target.value })}
              />
            </Field>
          </div>

          <div className="flex justify-end">
            <Button type="submit" loading={submitting}>
              {editing ? "Guardar cambios" : "Crear producto"}
            </Button>
          </div>
        </form>
      </CardBody>
    </Card>
  );
}
