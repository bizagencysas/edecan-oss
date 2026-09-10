/**
 * `/app/workspaces` — espacios de trabajo de equipo (`GET /v1/workspaces`).
 * 404 del backend → "Próximamente". Lista vacía ≠ éxito fingido ni consola
 * inventada: se muestra el vacío real. Crear usa el POST del mismo contrato.
 */

"use client";

import { useEffect, useState } from "react";

import {
  Alert,
  Button,
  Card,
  CardBody,
  CardHeader,
  EmptyState,
  Field,
  Input,
  PageHeader,
  Spinner,
} from "@/components/ui";
import { formatDateTime } from "@/lib/format";
import {
  createWorkspace,
  isNotFound,
  listWorkspaces,
  type Workspace,
} from "@/lib/api-workspaces";

function agentCount(workspace: Workspace): number {
  return Array.isArray(workspace.agents) ? workspace.agents.length : 0;
}

export default function WorkspacesPage() {
  const [items, setItems] = useState<Workspace[]>([]);
  const [loading, setLoading] = useState(true);
  const [upcoming, setUpcoming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    void load();
  }, []);

  async function load(background = false) {
    if (!background) setLoading(true);
    setError(null);
    try {
      const next = await listWorkspaces();
      setUpcoming(false);
      setItems(next);
    } catch (err) {
      if (isNotFound(err)) {
        setUpcoming(true);
        setItems([]);
      } else {
        setError(err instanceof Error ? err.message : "No se pudieron cargar los workspaces.");
      }
    } finally {
      if (!background) setLoading(false);
    }
  }

  async function handleCreate(event: React.FormEvent) {
    event.preventDefault();
    if (!name.trim() || upcoming) return;
    setSaving(true);
    setError(null);
    try {
      await createWorkspace(name.trim());
      setName("");
      await load(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo crear el workspace.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="Workspaces"
        description="Espacios de trabajo del equipo: agrupan agentes y conocimiento del tenant."
      />
      {error && (
        <div className="mb-4">
          <Alert variant="error">{error}</Alert>
        </div>
      )}

      {loading ? (
        <div className="flex justify-center py-12">
          <Spinner className="h-5 w-5 text-slate-400" />
        </div>
      ) : upcoming ? (
        <Card>
          <CardBody>
            <p className="text-sm text-slate-400 dark:text-slate-500">Próximamente</p>
          </CardBody>
        </Card>
      ) : (
        <>
          <Card className="mb-6">
            <CardHeader title="Nuevo workspace" />
            <CardBody>
              <form onSubmit={(event) => void handleCreate(event)} className="flex flex-col gap-3 sm:flex-row">
                <Field label="Nombre" htmlFor="workspace-name" className="min-w-0 flex-1">
                  <Input
                    id="workspace-name"
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                    placeholder="Acme"
                  />
                </Field>
                <div className="flex items-end">
                  <Button type="submit" loading={saving} disabled={!name.trim()} className="w-full sm:w-auto">
                    Crear
                  </Button>
                </div>
              </form>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Tus workspaces" />
            <CardBody>
              {items.length === 0 ? (
                <EmptyState
                  title="Sin workspaces"
                  description="Crea uno arriba. Una lista vacía no significa que el backend esté ausente."
                />
              ) : (
                <ul className="space-y-2">
                  {items.map((workspace) => {
                    const agents = agentCount(workspace);
                    return (
                      <li
                        key={workspace.id}
                        className="rounded-lg border border-slate-100 px-3 py-2.5 dark:border-slate-800"
                      >
                        <p className="truncate text-sm font-medium text-slate-700 dark:text-slate-200">
                          {workspace.name}
                        </p>
                        {workspace.description?.trim() ? (
                          <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                            {workspace.description}
                          </p>
                        ) : null}
                        <p className="mt-1 text-[11px] text-slate-400 dark:text-slate-500">
                          {agents} {agents === 1 ? "agente" : "agentes"}
                          {workspace.created_at ? ` · ${formatDateTime(workspace.created_at)}` : ""}
                        </p>
                      </li>
                    );
                  })}
                </ul>
              )}
            </CardBody>
          </Card>
        </>
      )}
    </div>
  );
}
