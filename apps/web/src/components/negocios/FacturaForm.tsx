"use client";

import { useMemo, useState } from "react";

import { PlusIcon, TrashIcon } from "@/components/icons";
import { Alert, Button, Card, CardBody, CardHeader, Field, Input, Textarea } from "@/components/ui";
import type { InvoiceCreateInput, InvoiceItemInput } from "@/lib/api-negocios";
import { formatMoney } from "@/lib/format";

const EMPTY_ITEM: InvoiceItemInput = { descripcion: "", cantidad: "1", precio_unitario: "" };

function round2(n: number): number {
  return Math.round((n + Number.EPSILON) * 100) / 100;
}

/** Formulario de creación con items dinámicos y vista previa de totales en vivo (el mismo
 * cálculo que `edecan_business.invoices.compute_totals` hace en el backend — redondeando
 * cada línea antes de sumar — para que la vista previa nunca "salte" un centavo respecto a
 * la factura que realmente se crea). */
export function FacturaForm({
  onSubmit,
  submitting,
}: {
  onSubmit: (input: InvoiceCreateInput) => Promise<void>;
  submitting: boolean;
}) {
  const [clienteNombre, setClienteNombre] = useState("");
  const [clienteEmail, setClienteEmail] = useState("");
  const [dueDate, setDueDate] = useState("");
  const [impuestosPct, setImpuestosPct] = useState("0");
  const [notas, setNotas] = useState("");
  const [items, setItems] = useState<InvoiceItemInput[]>([{ ...EMPTY_ITEM }]);
  const [error, setError] = useState<string | null>(null);

  const totals = useMemo(() => {
    const subtotal = items.reduce((acc, it) => {
      const cantidad = Number(it.cantidad) || 0;
      const precio = Number(it.precio_unitario) || 0;
      return acc + round2(cantidad * precio);
    }, 0);
    const pct = Number(impuestosPct) || 0;
    const impuestos = round2((subtotal * pct) / 100);
    const total = round2(subtotal + impuestos);
    return { subtotal, impuestos, total };
  }, [items, impuestosPct]);

  function updateItem(index: number, patch: Partial<InvoiceItemInput>) {
    setItems((prev) => prev.map((it, i) => (i === index ? { ...it, ...patch } : it)));
  }

  function addItem() {
    setItems((prev) => [...prev, { ...EMPTY_ITEM }]);
  }

  function removeItem(index: number) {
    setItems((prev) => (prev.length > 1 ? prev.filter((_, i) => i !== index) : prev));
  }

  function resetForm() {
    setClienteNombre("");
    setClienteEmail("");
    setDueDate("");
    setImpuestosPct("0");
    setNotas("");
    setItems([{ ...EMPTY_ITEM }]);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    if (!clienteNombre.trim()) {
      setError("El nombre del cliente es obligatorio.");
      return;
    }
    const itemsValidos = items.filter(
      (it) => it.descripcion.trim() && Number(it.precio_unitario) >= 0 && Number(it.cantidad) >= 0,
    );
    if (itemsValidos.length === 0) {
      setError("Agrega al menos un item con descripción, cantidad y precio.");
      return;
    }

    try {
      await onSubmit({
        cliente_nombre: clienteNombre.trim(),
        cliente_email: clienteEmail.trim() || null,
        due_date: dueDate || null,
        impuestos_pct: impuestosPct || 0,
        notas: notas.trim(),
        items: itemsValidos,
      });
      resetForm();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo crear la factura.");
    }
  }

  return (
    <Card>
      <CardHeader
        title="Nueva factura"
        description="Nace como borrador (draft): no se envía ni se cobra sola."
      />
      <CardBody>
        <form onSubmit={handleSubmit} className="space-y-4">
          {error && <Alert variant="error">{error}</Alert>}

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <Field label="Cliente" htmlFor="cliente_nombre">
              <Input
                id="cliente_nombre"
                value={clienteNombre}
                onChange={(e) => setClienteNombre(e.target.value)}
                placeholder="Nombre del cliente"
              />
            </Field>
            <Field label="Email del cliente (opcional)" htmlFor="cliente_email">
              <Input
                id="cliente_email"
                type="email"
                value={clienteEmail}
                onChange={(e) => setClienteEmail(e.target.value)}
              />
            </Field>
            <Field label="Vence (opcional)" htmlFor="due_date">
              <Input
                id="due_date"
                type="date"
                value={dueDate}
                onChange={(e) => setDueDate(e.target.value)}
              />
            </Field>
          </div>

          <div>
            <div className="mb-2 flex items-center justify-between">
              <p className="text-sm font-medium text-slate-700 dark:text-slate-200">Items</p>
              <Button type="button" variant="secondary" size="sm" onClick={addItem}>
                <PlusIcon className="h-3.5 w-3.5" /> Agregar item
              </Button>
            </div>
            <div className="space-y-2">
              {items.map((item, index) => (
                <div key={index} className="grid grid-cols-12 items-end gap-2">
                  <div className="col-span-12 sm:col-span-6">
                    <Field label={index === 0 ? "Descripción" : undefined}>
                      <Input
                        value={item.descripcion}
                        onChange={(e) => updateItem(index, { descripcion: e.target.value })}
                        placeholder="Descripción del producto/servicio"
                      />
                    </Field>
                  </div>
                  <div className="col-span-4 sm:col-span-2">
                    <Field label={index === 0 ? "Cantidad" : undefined}>
                      <Input
                        type="number"
                        min="0"
                        step="0.01"
                        value={item.cantidad}
                        onChange={(e) => updateItem(index, { cantidad: e.target.value })}
                      />
                    </Field>
                  </div>
                  <div className="col-span-6 sm:col-span-3">
                    <Field label={index === 0 ? "Precio unitario" : undefined}>
                      <Input
                        type="number"
                        min="0"
                        step="0.01"
                        value={item.precio_unitario}
                        onChange={(e) => updateItem(index, { precio_unitario: e.target.value })}
                      />
                    </Field>
                  </div>
                  <div className="col-span-2 sm:col-span-1 flex justify-end pb-2">
                    <button
                      type="button"
                      onClick={() => removeItem(index)}
                      disabled={items.length === 1}
                      className="rounded-md p-1.5 text-slate-400 hover:bg-rose-50 hover:text-rose-600 disabled:opacity-30 dark:hover:bg-rose-950/40"
                      aria-label="Quitar item"
                    >
                      <TrashIcon className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <Field label="Impuestos (%)" htmlFor="impuestos_pct">
              <Input
                id="impuestos_pct"
                type="number"
                min="0"
                step="0.01"
                value={impuestosPct}
                onChange={(e) => setImpuestosPct(e.target.value)}
              />
            </Field>
            <Field label="Notas (opcional)" htmlFor="notas" className="sm:col-span-2">
              <Textarea id="notas" rows={1} value={notas} onChange={(e) => setNotas(e.target.value)} />
            </Field>
          </div>

          <div className="flex flex-col items-end gap-1 border-t border-slate-100 pt-3 text-sm dark:border-slate-800">
            <div className="flex w-full max-w-xs justify-between text-slate-500">
              <span>Subtotal</span>
              <span>{formatMoney(totals.subtotal)}</span>
            </div>
            <div className="flex w-full max-w-xs justify-between text-slate-500">
              <span>Impuestos</span>
              <span>{formatMoney(totals.impuestos)}</span>
            </div>
            <div className="flex w-full max-w-xs justify-between text-base font-semibold text-slate-800 dark:text-slate-100">
              <span>Total</span>
              <span>{formatMoney(totals.total)}</span>
            </div>
          </div>

          <div className="flex justify-end">
            <Button type="submit" loading={submitting}>
              Crear factura
            </Button>
          </div>
        </form>
      </CardBody>
    </Card>
  );
}
