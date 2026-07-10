"use client";

import { useEffect, useState } from "react";

import { Alert, Button, Card, CardBody, CardHeader, Field, Input, Select } from "@/components/ui";
import type { Empleado, EmpleadoCreateInput, EmpleadoUpdateInput } from "@/lib/api-rrhh";

const CAMPOS_VACIOS = {
  nombre: "",
  email: "",
  puesto: "",
  salarioMensual: "",
  moneda: "USD",
  fechaIngreso: "",
  status: "active" as "active" | "inactive",
};

/** Formulario único para crear Y editar (`editing` decide el modo) — mismo patrón que
 * `ProductoForm` de `/app/inventario`. A diferencia de la tool de chat `gestionar_empleado`
 * (que NO permite renombrar, ver `docs/rrhh.md`), este formulario web SÍ edita `nombre`
 * porque identifica al empleado por `id`, no por nombre. */
export function EmpleadoForm({
  editing,
  onCreate,
  onUpdate,
  onCancelEdit,
  submitting,
}: {
  editing: Empleado | null;
  onCreate: (input: EmpleadoCreateInput) => Promise<void>;
  onUpdate: (id: string, input: EmpleadoUpdateInput) => Promise<void>;
  onCancelEdit: () => void;
  submitting: boolean;
}) {
  const [campos, setCampos] = useState(CAMPOS_VACIOS);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (editing) {
      setCampos({
        nombre: editing.nombre,
        email: editing.email ?? "",
        puesto: editing.puesto,
        salarioMensual: editing.salario_mensual === null ? "" : String(editing.salario_mensual),
        moneda: editing.moneda,
        fechaIngreso: editing.fecha_ingreso ?? "",
        status: editing.status,
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

    if (!campos.nombre.trim()) {
      setError("El nombre es obligatorio.");
      return;
    }

    const base = {
      nombre: campos.nombre.trim(),
      email: campos.email.trim() || null,
      puesto: campos.puesto.trim(),
      salario_mensual: campos.salarioMensual.trim() === "" ? null : campos.salarioMensual,
      moneda: campos.moneda.trim() || "USD",
      fecha_ingreso: campos.fechaIngreso.trim() || null,
      status: campos.status,
    };

    try {
      if (editing) {
        await onUpdate(editing.id, base);
      } else {
        await onCreate(base);
        resetForm();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo guardar el empleado.");
    }
  }

  return (
    <Card>
      <CardHeader
        title={editing ? `Editar «${editing.nombre}»` : "Nuevo empleado"}
        description={
          editing
            ? "El salario mensual es opcional — sin él, este empleado no entra en las nóminas que generes."
            : "Nace 'active'. El salario mensual es opcional — puedes agregarlo después."
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

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="Nombre" htmlFor="rrhh-nombre">
              <Input
                id="rrhh-nombre"
                value={campos.nombre}
                onChange={(e) => update({ nombre: e.target.value })}
                placeholder="Nombre completo"
              />
            </Field>
            <Field label="Puesto (opcional)" htmlFor="rrhh-puesto">
              <Input
                id="rrhh-puesto"
                value={campos.puesto}
                onChange={(e) => update({ puesto: e.target.value })}
                placeholder="p. ej. Gerente de Ventas"
              />
            </Field>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="Email (opcional)" htmlFor="rrhh-email">
              <Input
                id="rrhh-email"
                type="email"
                value={campos.email}
                onChange={(e) => update({ email: e.target.value })}
                placeholder="nombre@empresa.com"
              />
            </Field>
            <Field label="Fecha de ingreso (opcional)" htmlFor="rrhh-fecha-ingreso">
              <Input
                id="rrhh-fecha-ingreso"
                type="date"
                value={campos.fechaIngreso}
                onChange={(e) => update({ fechaIngreso: e.target.value })}
              />
            </Field>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-4">
            <Field
              label="Salario mensual (opcional)"
              htmlFor="rrhh-salario"
              hint="Sin él, no entra en las nóminas."
              className="sm:col-span-2"
            >
              <Input
                id="rrhh-salario"
                type="number"
                min="0"
                step="0.01"
                value={campos.salarioMensual}
                onChange={(e) => update({ salarioMensual: e.target.value })}
              />
            </Field>
            <Field label="Moneda" htmlFor="rrhh-moneda">
              <Input
                id="rrhh-moneda"
                value={campos.moneda}
                maxLength={3}
                onChange={(e) => update({ moneda: e.target.value.toUpperCase() })}
              />
            </Field>
            <Field label="Estado" htmlFor="rrhh-status">
              <Select
                id="rrhh-status"
                value={campos.status}
                onChange={(e) => update({ status: e.target.value as "active" | "inactive" })}
              >
                <option value="active">Activo</option>
                <option value="inactive">Inactivo</option>
              </Select>
            </Field>
          </div>

          <div className="flex justify-end">
            <Button type="submit" loading={submitting}>
              {editing ? "Guardar cambios" : "Crear empleado"}
            </Button>
          </div>
        </form>
      </CardBody>
    </Card>
  );
}
