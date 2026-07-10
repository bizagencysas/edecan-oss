"use client";

import { useEffect, useState } from "react";

import { CheckIcon, SearchIcon, TrashIcon } from "@/components/icons";
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
  Select,
  Spinner,
  Textarea,
} from "@/components/ui";
import {
  addMemory,
  confirmImportMemoria,
  deleteMemory,
  listMemory,
  previewImportMemoria,
} from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { MemoryImportItem, MemoryItem } from "@/lib/types";

const PROMPT_SUGERIDO =
  "Cuéntame todo lo que sabes de mí hasta ahora: mis preferencias, datos personales, " +
  "contexto de trabajo, relaciones importantes, decisiones que he tomado, y cualquier otra cosa " +
  "relevante que deberías recordar sobre mí. Sé lo más detallado posible.";

const KIND_VARIANT: Record<string, "brand" | "success" | "warning" | "neutral"> = {
  fact: "brand",
  preference: "success",
  event: "warning",
  entity: "neutral",
};

export default function MemoriaPage() {
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);

  const [content, setContent] = useState("");
  const [kind, setKind] = useState("fact");
  const [importance, setImportance] = useState(0.5);
  const [saving, setSaving] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const [textoImportar, setTextoImportar] = useState("");
  const [extrayendo, setExtrayendo] = useState(false);
  const [propuestos, setPropuestos] = useState<MemoryImportItem[] | null>(null);
  const [seleccionados, setSeleccionados] = useState<Set<number>>(new Set());
  const [guardandoImport, setGuardandoImport] = useState(false);
  const [copiado, setCopiado] = useState(false);

  useEffect(() => {
    void load();
  }, []);

  async function load(q?: string) {
    setLoading(true);
    setError(null);
    try {
      setItems(await listMemory(q || undefined, 50));
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo cargar la memoria.");
    } finally {
      setLoading(false);
    }
  }

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    if (!content.trim()) return;
    setSaving(true);
    setError(null);
    try {
      await addMemory({ content: content.trim(), kind, importance, source: "user" });
      setContent("");
      setImportance(0.5);
      await load(query);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo guardar el recuerdo.");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(id: string) {
    setDeletingId(id);
    setError(null);
    try {
      await deleteMemory(id);
      setItems((prev) => prev.filter((i) => i.id !== id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo borrar el recuerdo.");
    } finally {
      setDeletingId(null);
    }
  }

  async function handleCopiarPrompt() {
    try {
      await navigator.clipboard.writeText(PROMPT_SUGERIDO);
      setCopiado(true);
      setTimeout(() => setCopiado(false), 2500);
    } catch {
      // Clipboard puede fallar sin HTTPS/permiso — el usuario igual puede
      // seleccionar y copiar el texto a mano, no es un error bloqueante.
    }
  }

  async function handleExtraer() {
    if (!textoImportar.trim()) return;
    setExtrayendo(true);
    setError(null);
    setPropuestos(null);
    try {
      const items = await previewImportMemoria(textoImportar.trim());
      setPropuestos(items);
      setSeleccionados(new Set(items.map((_, i) => i)));
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo extraer recuerdos de ese texto.");
    } finally {
      setExtrayendo(false);
    }
  }

  function toggleSeleccionado(index: number) {
    setSeleccionados((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  }

  async function handleGuardarSeleccionados() {
    if (!propuestos || seleccionados.size === 0) return;
    setGuardandoImport(true);
    setError(null);
    try {
      const elegidos = propuestos.filter((_, i) => seleccionados.has(i));
      await confirmImportMemoria(elegidos);
      setPropuestos(null);
      setTextoImportar("");
      await load(query);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudieron guardar los recuerdos.");
    } finally {
      setGuardandoImport(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="Memoria"
        description="Hechos, preferencias, eventos y entidades que tu asistente recuerda sobre ti."
      />
      {error && (
        <div className="mb-4">
          <Alert variant="error">{error}</Alert>
        </div>
      )}

      <Card className="mb-6">
        <CardHeader title="Agregar un recuerdo manual" />
        <CardBody>
          <form onSubmit={handleAdd} className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_140px_120px_auto]">
            <Field label="Contenido" htmlFor="content">
              <Input
                id="content"
                value={content}
                onChange={(e) => setContent(e.target.value)}
                placeholder="Su hijo se llama Mateo"
              />
            </Field>
            <Field label="Tipo" htmlFor="kind">
              <Select id="kind" value={kind} onChange={(e) => setKind(e.target.value)}>
                <option value="fact">fact</option>
                <option value="preference">preference</option>
                <option value="event">event</option>
                <option value="entity">entity</option>
              </Select>
            </Field>
            <Field label="Importancia" htmlFor="importance">
              <Input
                id="importance"
                type="number"
                min={0}
                max={1}
                step={0.1}
                value={importance}
                onChange={(e) => setImportance(Number(e.target.value))}
              />
            </Field>
            <div className="flex items-end">
              <Button type="submit" loading={saving} className="w-full sm:w-auto">
                Agregar
              </Button>
            </div>
          </form>
        </CardBody>
      </Card>

      <Card className="mb-6">
        <CardHeader
          title="Importar desde otra IA"
          description="¿Ya le contaste todo a ChatGPT, Gemini u otra IA? Copiá el prompt, pegá su respuesta acá, y Edecán saca los recuerdos de ahí."
        />
        <CardBody className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <Button type="button" variant="secondary" size="sm" onClick={handleCopiarPrompt}>
              {copiado ? "Copiado ✓" : "Copiar prompt"}
            </Button>
            <span className="text-xs text-slate-400">
              Pegalo en tu IA preferida, copiá toda su respuesta, y pegala abajo.
            </span>
          </div>

          <Field label="Respuesta pegada" htmlFor="texto_importar">
            <Textarea
              id="texto_importar"
              rows={5}
              value={textoImportar}
              onChange={(e) => setTextoImportar(e.target.value)}
              placeholder="Pegá acá la respuesta completa de tu IA…"
            />
          </Field>

          <Button
            type="button"
            onClick={handleExtraer}
            loading={extrayendo}
            disabled={!textoImportar.trim()}
          >
            Extraer recuerdos
          </Button>

          {propuestos && (
            <div className="mt-3 space-y-2 border-t border-slate-100 pt-3 dark:border-slate-800">
              {propuestos.length === 0 ? (
                <p className="text-sm text-slate-400">
                  No encontramos nada que valga la pena recordar en ese texto.
                </p>
              ) : (
                <>
                  <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
                    {seleccionados.size} de {propuestos.length} seleccionados
                  </p>
                  <ul className="space-y-1.5">
                    {propuestos.map((item, i) => (
                      <li
                        key={i}
                        className="flex items-start gap-2.5 rounded-lg border border-slate-100 px-3 py-2 text-sm dark:border-slate-800"
                      >
                        <button
                          type="button"
                          onClick={() => toggleSeleccionado(i)}
                          aria-label={seleccionados.has(i) ? "Quitar de la selección" : "Agregar a la selección"}
                          className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border ${
                            seleccionados.has(i)
                              ? "border-brand-600 bg-brand-600 text-white"
                              : "border-slate-300 dark:border-slate-600"
                          }`}
                        >
                          {seleccionados.has(i) && <CheckIcon className="h-3 w-3" />}
                        </button>
                        <div className="min-w-0 flex-1">
                          <div className="mb-0.5 flex items-center gap-2">
                            <Badge variant={KIND_VARIANT[item.kind] ?? "neutral"}>{item.kind}</Badge>
                          </div>
                          <p className="text-slate-700 dark:text-slate-200">{item.content}</p>
                        </div>
                      </li>
                    ))}
                  </ul>
                  <Button
                    type="button"
                    onClick={handleGuardarSeleccionados}
                    loading={guardandoImport}
                    disabled={seleccionados.size === 0}
                  >
                    Guardar seleccionados
                  </Button>
                </>
              )}
            </div>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Recuerdos"
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
                placeholder="Buscar…"
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
            <EmptyState title="Sin recuerdos todavía" description="Se llenará automáticamente a medida que converses, o agrega uno manual arriba." />
          ) : (
            <ul className="space-y-2">
              {items.map((item) => (
                <li
                  key={item.id}
                  className="flex items-start justify-between gap-3 rounded-lg border border-slate-100 px-3 py-2.5 dark:border-slate-800"
                >
                  <div className="min-w-0 flex-1">
                    <div className="mb-1 flex flex-wrap items-center gap-2">
                      <Badge variant={KIND_VARIANT[item.kind ?? ""] ?? "neutral"}>{item.kind}</Badge>
                      <span className="text-xs text-slate-400">
                        importancia {item.importance?.toFixed(1)} · {item.source} · {formatDateTime(item.created_at)}
                      </span>
                    </div>
                    <p className="text-sm text-slate-700 dark:text-slate-200">{item.content}</p>
                  </div>
                  <button
                    onClick={() => handleDelete(item.id)}
                    disabled={deletingId === item.id}
                    className="shrink-0 rounded-md p-1.5 text-slate-400 hover:bg-rose-50 hover:text-rose-600 dark:hover:bg-rose-950/40"
                    aria-label="Borrar recuerdo"
                  >
                    {deletingId === item.id ? <Spinner className="h-3.5 w-3.5" /> : <TrashIcon className="h-3.5 w-3.5" />}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
