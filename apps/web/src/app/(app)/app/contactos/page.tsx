"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { SearchIcon, TrashIcon } from "@/components/icons";
import { ICloudConnectionCard } from "@/components/configuracion/ICloudConnectionCard";
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
  Textarea,
} from "@/components/ui";
import {
  createContact,
  deleteContact,
  importContactsGoogle,
  listConnectors,
  listContacts,
  updateContact,
} from "@/lib/api";
import type { Contact } from "@/lib/types";

function splitList(value: string): string[] {
  return value
    .split(",")
    .map((v) => v.trim())
    .filter(Boolean);
}

const emptyForm = { nombre: "", emails: "", phones: "", empresa: "", tags: "", notas: "" };

export default function ContactosPage() {
  const router = useRouter();

  const [items, setItems] = useState<Contact[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  const [form, setForm] = useState(emptyForm);
  const [saving, setSaving] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const [googleConnected, setGoogleConnected] = useState(false);
  const [importingGoogle, setImportingGoogle] = useState(false);
  const [googleMessage, setGoogleMessage] = useState<string | null>(null);

  useEffect(() => {
    void load();
  }, []);

  useEffect(() => {
    void loadGoogleConnectorStatus();
  }, []);

  async function load(q?: string) {
    setLoading(true);
    setError(null);
    try {
      setItems(await listContacts(q || undefined));
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudieron cargar los contactos.");
    } finally {
      setLoading(false);
    }
  }

  function startEdit(contact: Contact) {
    setEditingId(contact.id);
    setForm({
      nombre: contact.nombre,
      emails: contact.emails.join(", "),
      phones: contact.phones.join(", "),
      empresa: contact.empresa ?? "",
      tags: contact.tags.join(", "),
      notas: contact.notas ?? "",
    });
  }

  function cancelEdit() {
    setEditingId(null);
    setForm(emptyForm);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!form.nombre.trim()) return;
    setSaving(true);
    setError(null);
    const payload = {
      nombre: form.nombre.trim(),
      emails: splitList(form.emails),
      phones: splitList(form.phones),
      empresa: form.empresa.trim() || null,
      tags: splitList(form.tags),
      notas: form.notas.trim() || null,
    };
    try {
      if (editingId) {
        const updated = await updateContact(editingId, payload);
        setItems((prev) => prev.map((c) => (c.id === editingId ? updated : c)));
      } else {
        const created = await createContact(payload);
        setItems((prev) => [created, ...prev]);
      }
      cancelEdit();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo guardar el contacto.");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(id: string) {
    setBusyId(id);
    setError(null);
    try {
      await deleteContact(id);
      setItems((prev) => prev.filter((c) => c.id !== id));
      if (editingId === id) cancelEdit();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo borrar el contacto.");
    } finally {
      setBusyId(null);
    }
  }

  async function loadGoogleConnectorStatus() {
    try {
      const connectors = await listConnectors();
      const google = connectors.find((c) => c.key === "google");
      setGoogleConnected(Boolean(google && google.accounts.length > 0));
    } catch {
      // No bloquea el resto de la pantalla si esto falla.
    }
  }

  async function handleImportGoogle() {
    setImportingGoogle(true);
    setGoogleMessage(null);
    setError(null);
    try {
      const result = await importContactsGoogle();
      setGoogleMessage(
        result.importados > 0
          ? `${result.importados} contacto${result.importados === 1 ? "" : "s"} nuevo${result.importados === 1 ? "" : "s"} importado${result.importados === 1 ? "" : "s"} de ${result.total_google} en tu cuenta de Google.`
          : "Ya estaba todo al día — nada nuevo que importar de Google.",
      );
      await load(query);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo importar desde Google.");
    } finally {
      setImportingGoogle(false);
    }
  }

  return (
    <div>
      <PageHeader title="Contactos" description="CRM ligero: correos, teléfonos, empresa, notas y etiquetas por contacto." />
      {error && (
        <div className="mb-4">
          <Alert variant="error">{error}</Alert>
        </div>
      )}

      <Card className="mb-6">
        <CardHeader
          title="Importar desde Google"
          description="Trae tus contactos de Google (misma cuenta que ya conectaste para Gmail/Calendar en Conectores)."
          actions={
            googleConnected ? (
              <Badge variant="success">Conectado</Badge>
            ) : (
              <Badge variant="neutral">Sin conectar</Badge>
            )
          }
        />
        <CardBody className="space-y-3">
          {googleMessage && <Alert variant="success">{googleMessage}</Alert>}
          {googleConnected ? (
            <Button type="button" onClick={handleImportGoogle} loading={importingGoogle}>
              Importar desde Google
            </Button>
          ) : (
            <div className="flex flex-wrap items-center gap-3">
              <p className="text-sm text-slate-500 dark:text-slate-400">
                Conecta tu cuenta de Google en Conectores primero.
              </p>
              <Button type="button" variant="secondary" onClick={() => router.push("/app/conectores")}>
                Ir a Conectores
              </Button>
            </div>
          )}
        </CardBody>
      </Card>

      <div className="mb-6"><ICloudConnectionCard onImported={() => load(query)} /></div>

      <Card className="mb-6">
        <CardHeader title={editingId ? "Editar contacto" : "Nuevo contacto"} />
        <CardBody>
          <form onSubmit={handleSubmit} className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="Nombre" htmlFor="nombre">
              <Input id="nombre" value={form.nombre} onChange={(e) => setForm({ ...form, nombre: e.target.value })} />
            </Field>
            <Field label="Empresa" htmlFor="empresa">
              <Input id="empresa" value={form.empresa} onChange={(e) => setForm({ ...form, empresa: e.target.value })} />
            </Field>
            <Field label="Correos (separados por coma)" htmlFor="emails">
              <Input id="emails" value={form.emails} onChange={(e) => setForm({ ...form, emails: e.target.value })} />
            </Field>
            <Field label="Teléfonos (separados por coma)" htmlFor="phones">
              <Input id="phones" value={form.phones} onChange={(e) => setForm({ ...form, phones: e.target.value })} />
            </Field>
            <Field label="Etiquetas (separadas por coma)" htmlFor="tags">
              <Input id="tags" value={form.tags} onChange={(e) => setForm({ ...form, tags: e.target.value })} />
            </Field>
            <Field label="Notas" htmlFor="notas">
              <Textarea id="notas" rows={1} value={form.notas} onChange={(e) => setForm({ ...form, notas: e.target.value })} />
            </Field>
            <div className="flex gap-2 sm:col-span-2">
              <Button type="submit" loading={saving}>
                {editingId ? "Guardar cambios" : "Agregar contacto"}
              </Button>
              {editingId && (
                <Button type="button" variant="secondary" onClick={cancelEdit}>
                  Cancelar
                </Button>
              )}
            </div>
          </form>
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Tus contactos"
          actions={
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void load(query);
              }}
              className="flex items-center gap-2"
            >
              <Input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Buscar por nombre…"
                className="h-8 w-40 py-1 text-xs sm:w-56"
              />
              <Button type="submit" size="sm" variant="secondary" aria-label="Buscar">
                <SearchIcon className="h-3.5 w-3.5" />
              </Button>
            </form>
          }
        />
        <CardBody>
          {loading ? (
            <div className="flex justify-center py-8">
              <Spinner className="h-5 w-5 text-slate-400" />
            </div>
          ) : items.length === 0 ? (
            <EmptyState title="Sin contactos" description="Agrega uno arriba o pídeselo a tu asistente en el chat." />
          ) : (
            <ul className="space-y-2">
              {items.map((c) => (
                <li key={c.id} className="rounded-lg border border-slate-100 px-3 py-2.5 dark:border-slate-800">
                  <div className="flex items-start justify-between gap-3">
                    <button className="min-w-0 flex-1 text-left" onClick={() => startEdit(c)}>
                      <p className="truncate text-sm font-medium text-slate-700 dark:text-slate-200">{c.nombre}</p>
                      <p className="truncate text-xs text-slate-400">
                        {[...c.emails, ...c.phones].join(" · ") || "Sin datos de contacto"}
                        {c.empresa ? ` · ${c.empresa}` : ""}
                      </p>
                      {c.tags.length > 0 && (
                        <div className="mt-1 flex flex-wrap gap-1">
                          {c.tags.map((tag) => (
                            <Badge key={tag} variant="neutral">
                              {tag}
                            </Badge>
                          ))}
                        </div>
                      )}
                    </button>
                    <button
                      onClick={() => handleDelete(c.id)}
                      disabled={busyId === c.id}
                      className="shrink-0 rounded-md p-1.5 text-slate-400 hover:bg-rose-50 hover:text-rose-600 dark:hover:bg-rose-950/40"
                      aria-label="Borrar contacto"
                    >
                      {busyId === c.id ? <Spinner className="h-3.5 w-3.5" /> : <TrashIcon className="h-3.5 w-3.5" />}
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
