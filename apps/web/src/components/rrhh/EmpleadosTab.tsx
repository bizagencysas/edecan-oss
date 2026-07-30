"use client";

import { useEffect, useState } from "react";

import { Alert, Card, CardBody, CardHeader, Input, Select } from "@/components/ui";
import {
  createEmpleado,
  listEmpleados,
  setEmpleadoStatus,
  updateEmpleado,
  type Empleado,
  type EmpleadoCreateInput,
  type EmpleadoStatus,
  type EmpleadoUpdateInput,
} from "@/lib/api-rrhh";

import { EmpleadoForm } from "./EmpleadoForm";
import { EmpleadosTable } from "./EmpleadosTable";

type FiltroEstado = "todos" | EmpleadoStatus;

export function EmpleadosTab() {
  const [empleados, setEmpleados] = useState<Empleado[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [filtroEstado, setFiltroEstado] = useState<FiltroEstado>("active");
  const [editing, setEditing] = useState<Empleado | null>(null);

  // Debounce manual (sin dependencias npm nuevas), mismo patrón que `/app/inventario`.
  useEffect(() => {
    const handle = setTimeout(() => setQ(qInput), 300);
    return () => clearTimeout(handle);
  }, [qInput]);

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, filtroEstado]);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const status = filtroEstado === "todos" ? undefined : filtroEstado;
      const empleadosRes = await listEmpleados({ status, q: q.trim() || undefined });
      setEmpleados(empleadosRes);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo cargar la lista de empleados.");
    } finally {
      setLoading(false);
    }
  }

  async function handleCreate(input: EmpleadoCreateInput) {
    setSaving(true);
    setError(null);
    try {
      await createEmpleado(input);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo crear el empleado.");
      throw err; // deja que EmpleadoForm mantenga los datos capturados para reintentar
    } finally {
      setSaving(false);
    }
  }

  async function handleUpdate(id: string, input: EmpleadoUpdateInput) {
    setSaving(true);
    setError(null);
    try {
      await updateEmpleado(id, input);
      setEditing(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo actualizar el empleado.");
      throw err;
    } finally {
      setSaving(false);
    }
  }

  async function handleToggleStatus(empleado: Empleado) {
    setError(null);
    try {
      await setEmpleadoStatus(empleado.id, empleado.status === "active" ? "inactive" : "active");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo cambiar el estado del empleado.");
    }
  }

  return (
    <div>
      {error && (
        <div className="mb-4">
          <Alert variant="error">{error}</Alert>
        </div>
      )}

      <div className="mb-6">
        <EmpleadoForm
          editing={editing}
          onCreate={handleCreate}
          onUpdate={handleUpdate}
          onCancelEdit={() => setEditing(null)}
          submitting={saving}
        />
      </div>

      <Card>
        <CardHeader
          title="Empleados"
          description="Edita o activa/desactiva un empleado. Sin salario mensual, no entra en las nóminas."
          actions={
            <div className="flex flex-wrap items-center gap-2">
              <Input
                value={qInput}
                onChange={(e) => setQInput(e.target.value)}
                placeholder="Buscar por nombre, email o puesto…"
                className="w-56"
                aria-label="Buscar empleados"
              />
              <Select
                value={filtroEstado}
                onChange={(e) => setFiltroEstado(e.target.value as FiltroEstado)}
                className="w-auto"
                aria-label="Filtrar por estado"
              >
                <option value="active">Activos</option>
                <option value="inactive">Inactivos</option>
                <option value="todos">Todos</option>
              </Select>
            </div>
          }
        />
        <CardBody>
          <EmpleadosTable
            empleados={empleados}
            loading={loading}
            onEdit={setEditing}
            onToggleStatus={handleToggleStatus}
          />
        </CardBody>
      </Card>
    </div>
  );
}
