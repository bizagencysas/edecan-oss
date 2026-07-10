"use client";

import { useEffect, useState } from "react";

import { MovimientoModal } from "@/components/inventario/MovimientoModal";
import { ProductoForm } from "@/components/inventario/ProductoForm";
import { ProductosTable } from "@/components/inventario/ProductosTable";
import { ResumenInventarioCard } from "@/components/inventario/ResumenInventarioCard";
import { Alert, Card, CardBody, CardHeader, Input, PageHeader, Select } from "@/components/ui";
import {
  createProducto,
  desactivarProducto,
  getResumenInventario,
  listProductos,
  updateProducto,
  type MovimientoResultado,
  type Producto,
  type ProductoCreateInput,
  type ProductoUpdateInput,
  type ResumenInventario,
} from "@/lib/api-inventario";

type FiltroActivo = "todos" | "activos" | "inactivos";

export default function InventarioPage() {
  const [productos, setProductos] = useState<Producto[]>([]);
  const [resumen, setResumen] = useState<ResumenInventario | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [filtroActivo, setFiltroActivo] = useState<FiltroActivo>("activos");

  const [editing, setEditing] = useState<Producto | null>(null);
  const [movimientoProducto, setMovimientoProducto] = useState<Producto | null>(null);

  // Debounce manual (sin dependencias npm nuevas): evita disparar una request por cada
  // tecla al buscar por sku/nombre.
  useEffect(() => {
    const handle = setTimeout(() => setQ(qInput), 300);
    return () => clearTimeout(handle);
  }, [qInput]);

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, filtroActivo]);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const activo = filtroActivo === "todos" ? undefined : filtroActivo === "activos";
      const [prods, res] = await Promise.all([
        listProductos({ activo, q: q.trim() || undefined }),
        getResumenInventario(),
      ]);
      setProductos(prods);
      setResumen(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo cargar el inventario.");
    } finally {
      setLoading(false);
    }
  }

  async function handleCreate(input: ProductoCreateInput) {
    setSaving(true);
    setError(null);
    try {
      await createProducto(input);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo crear el producto.");
      throw err; // deja que ProductoForm mantenga los datos capturados para reintentar
    } finally {
      setSaving(false);
    }
  }

  async function handleUpdate(id: string, input: ProductoUpdateInput) {
    setSaving(true);
    setError(null);
    try {
      await updateProducto(id, input);
      setEditing(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo actualizar el producto.");
      throw err;
    } finally {
      setSaving(false);
    }
  }

  async function handleToggleActivo(producto: Producto) {
    setError(null);
    try {
      if (producto.activo) {
        await desactivarProducto(producto.id);
      } else {
        await updateProducto(producto.id, { activo: true });
      }
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo cambiar el estado del producto.");
    }
  }

  function handleMovimientoRegistrado(_resultado: MovimientoResultado) {
    setMovimientoProducto(null);
    void load();
  }

  return (
    <div>
      <PageHeader
        title="Inventario"
        description="ERP básico: productos, stock y alertas de mínimos. No es contabilidad de costos avanzada."
      />

      {error && (
        <div className="mb-4">
          <Alert variant="error">{error}</Alert>
        </div>
      )}

      <div className="mb-6">
        <ResumenInventarioCard resumen={resumen} />
      </div>

      <div className="mb-6">
        <ProductoForm
          editing={editing}
          onCreate={handleCreate}
          onUpdate={handleUpdate}
          onCancelEdit={() => setEditing(null)}
          submitting={saving}
        />
      </div>

      <Card>
        <CardHeader
          title="Productos"
          description="Edita, registra un movimiento de stock o desactiva un producto."
          actions={
            <div className="flex flex-wrap items-center gap-2">
              <Input
                value={qInput}
                onChange={(e) => setQInput(e.target.value)}
                placeholder="Buscar por sku o nombre…"
                className="w-56"
                aria-label="Buscar productos"
              />
              <Select
                value={filtroActivo}
                onChange={(e) => setFiltroActivo(e.target.value as FiltroActivo)}
                className="w-auto"
                aria-label="Filtrar por estado"
              >
                <option value="activos">Activos</option>
                <option value="inactivos">Inactivos</option>
                <option value="todos">Todos</option>
              </Select>
            </div>
          }
        />
        <CardBody>
          <ProductosTable
            productos={productos}
            loading={loading}
            onEdit={setEditing}
            onMovimiento={setMovimientoProducto}
            onToggleActivo={handleToggleActivo}
          />
        </CardBody>
      </Card>

      {movimientoProducto && (
        <MovimientoModal
          producto={movimientoProducto}
          onClose={() => setMovimientoProducto(null)}
          onRegistrado={handleMovimientoRegistrado}
        />
      )}
    </div>
  );
}
