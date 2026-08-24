"use client";

import { useEffect, useState } from "react";

import { TrashIcon } from "@/components/icons";
import {
  Alert,
  Badge,
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
import { createReminder, deleteReminder, listReminders, updateReminder } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { Reminder } from "@/lib/types";

const STATUS_VARIANT: Record<string, "brand" | "success" | "neutral"> = {
  pending: "brand",
  sent: "success",
  cancelled: "neutral",
};

function defaultDueAt(): string {
  const d = new Date(Date.now() + 60 * 60 * 1000);
  d.setSeconds(0, 0);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

export default function RecordatoriosPage() {
  const [items, setItems] = useState<Reminder[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [message, setMessage] = useState("");
  const [dueAt, setDueAt] = useState(defaultDueAt());
  const [saving, setSaving] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

  useEffect(() => {
    void load();
  }, []);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setItems(await listReminders());
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudieron cargar los recordatorios.");
    } finally {
      setLoading(false);
    }
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!message.trim() || !dueAt) return;
    setSaving(true);
    setError(null);
    try {
      await createReminder({ message: message.trim(), due_at: new Date(dueAt).toISOString(), channel: "web" });
      setMessage("");
      setDueAt(defaultDueAt());
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo crear el recordatorio.");
    } finally {
      setSaving(false);
    }
  }

  async function handleCancel(id: string) {
    setBusyId(id);
    setError(null);
    try {
      const updated = await updateReminder(id, { status: "cancelled" });
      setItems((prev) => prev.map((r) => (r.id === id ? updated : r)));
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo cancelar el recordatorio.");
    } finally {
      setBusyId(null);
    }
  }

  async function handleDelete(id: string) {
    setBusyId(id);
    setError(null);
    try {
      await deleteReminder(id);
      setItems((prev) => prev.filter((r) => r.id !== id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo borrar el recordatorio.");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div>
      <PageHeader title="Recordatorios" description="Se revisan y envían automáticamente cuando vencen." />
      {error && (
        <div className="mb-4">
          <Alert variant="error">{error}</Alert>
        </div>
      )}

      <Card className="mb-6">
        <CardHeader title="Nuevo recordatorio" />
        <CardBody>
          <form onSubmit={handleCreate} className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_220px_auto]">
            <Field label="Mensaje" htmlFor="message">
              <Input id="message" value={message} onChange={(e) => setMessage(e.target.value)} placeholder="Llamar al contador" />
            </Field>
            <Field label="Fecha y hora" htmlFor="due_at">
              <Input id="due_at" type="datetime-local" value={dueAt} onChange={(e) => setDueAt(e.target.value)} />
            </Field>
            <div className="flex items-end">
              <Button type="submit" loading={saving} className="w-full sm:w-auto">
                Crear
              </Button>
            </div>
          </form>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Tus recordatorios" />
        <CardBody>
          {loading ? (
            <div className="flex justify-center py-8">
              <Spinner className="h-5 w-5 text-slate-400" />
            </div>
          ) : items.length === 0 ? (
            <EmptyState title="Sin recordatorios" description="Crea uno arriba, o pídeselo a tu asistente en el chat." />
          ) : (
            <ul className="space-y-2">
              {items.map((r) => (
                <li
                  key={r.id}
                  className="flex items-center justify-between gap-3 rounded-lg border border-slate-100 px-3 py-2.5 dark:border-slate-800"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-slate-700 dark:text-slate-200">{r.message}</p>
                    <p className="text-xs text-slate-400">{formatDateTime(r.due_at)}{r.rrule ? ` · ${r.rrule}` : ""}</p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <Badge variant={STATUS_VARIANT[r.status] ?? "neutral"}>{r.status}</Badge>
                    {r.status === "pending" && (
                      <Button size="sm" variant="secondary" onClick={() => handleCancel(r.id)} loading={busyId === r.id}>
                        Cancelar
                      </Button>
                    )}
                    <button
                      onClick={() => handleDelete(r.id)}
                      disabled={busyId === r.id}
                      className="rounded-md p-1.5 text-slate-400 hover:bg-rose-50 hover:text-rose-600 dark:hover:bg-rose-950/40"
                      aria-label="Borrar"
                    >
                      <TrashIcon className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
