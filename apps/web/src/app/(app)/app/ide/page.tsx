"use client";

/**
 * IDE embebido sobre el companion de escritorio (pantalla 3 del mockup,
 * `ROADMAP_V2.md` §7.8/§7.10, WP-V2-08): tabs Editor/Archivos + panel de
 * terminal permanente abajo. Requiere el companion emparejado y corriendo
 * (`docs/ide.md`) — si no, se muestra un banner con instrucciones y el resto
 * de la página igual intenta funcionar (cada acción termina en el mismo
 * error 503 legible del router, ver `lib/api-ide.ts`).
 */

import Link from "next/link";
import { useEffect, useState } from "react";

import { CodeEditor } from "@/components/ide/CodeEditor";
import { FileTree } from "@/components/ide/FileTree";
import { SearchPanel } from "@/components/ide/SearchPanel";
import { Terminal } from "@/components/ide/Terminal";
import { Alert, Badge, PageHeader } from "@/components/ui";
import { getIdeFile, getIdeStatus, putIdeFile } from "@/lib/api-ide";

type Tab = "editor" | "archivos";

const TABS: { key: Tab; label: string }[] = [
  { key: "editor", label: "Editor" },
  { key: "archivos", label: "Archivos" },
];

export default function IdePage() {
  const [tab, setTab] = useState<Tab>("editor");
  const [connected, setConnected] = useState<boolean | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);

  const [path, setPath] = useState<string | null>(null);
  const [content, setContent] = useState("");
  const [originalContent, setOriginalContent] = useState("");
  const [fileLoading, setFileLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [fileError, setFileError] = useState<string | null>(null);

  useEffect(() => {
    void checkStatus();
  }, []);

  async function checkStatus() {
    setStatusError(null);
    try {
      const result = await getIdeStatus();
      setConnected(result.connected);
    } catch (err) {
      setConnected(false);
      setStatusError(
        err instanceof Error ? err.message : "No se pudo consultar el estado del companion.",
      );
    }
  }

  async function handleOpenFile(filePath: string) {
    setTab("editor");
    setPath(filePath);
    setFileLoading(true);
    setFileError(null);
    try {
      const file = await getIdeFile(filePath);
      setContent(file.content);
      setOriginalContent(file.content);
    } catch (err) {
      setContent("");
      setOriginalContent("");
      setFileError(err instanceof Error ? err.message : "No se pudo abrir el archivo.");
    } finally {
      setFileLoading(false);
    }
  }

  async function handleSave() {
    if (!path) return;
    setSaving(true);
    setFileError(null);
    try {
      await putIdeFile(path, content);
      setOriginalContent(content);
    } catch (err) {
      setFileError(err instanceof Error ? err.message : "No se pudo guardar el archivo.");
    } finally {
      setSaving(false);
    }
  }

  const dirty = content !== originalContent;

  return (
    <div>
      <PageHeader
        title="IDE"
        description="Explora, edita y ejecuta comandos en el sandbox de tu companion de escritorio."
        actions={
          connected !== null ? (
            <Badge variant={connected ? "success" : "danger"}>
              {connected ? "Companion conectado" : "Companion desconectado"}
            </Badge>
          ) : undefined
        }
      />

      {statusError && (
        <div className="mb-4">
          <Alert variant="error">No se pudo verificar el estado del companion: {statusError}</Alert>
        </div>
      )}
      {!statusError && connected === false && (
        <div className="mb-4">
          <Alert variant="info">
            No hay companion conectado. Instálalo y empareja tu equipo desde{" "}
            <Link href="/app/ajustes" className="font-medium underline">
              Ajustes
            </Link>{" "}
            para poder usar el IDE.
          </Alert>
        </div>
      )}

      <div className="mb-3 flex gap-1 border-b border-slate-200 dark:border-slate-800">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setTab(t.key)}
            className={`border-b-2 px-3 py-2 text-sm font-medium transition-colors ${
              tab === t.key
                ? "border-brand-600 text-brand-700 dark:text-brand-400"
                : "border-transparent text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="flex flex-col gap-3">
        {tab === "editor" ? (
          <div className="flex h-[34rem] flex-col gap-3 md:flex-row">
            <div className="h-40 shrink-0 overflow-y-auto rounded-2xl border border-slate-200 bg-white p-2 dark:border-slate-800 dark:bg-slate-900 md:h-auto md:w-56">
              <FileTree activePath={path} onOpenFile={handleOpenFile} />
            </div>
            <CodeEditor
              path={path}
              content={content}
              dirty={dirty}
              loading={fileLoading}
              saving={saving}
              error={fileError}
              onChange={setContent}
              onSave={handleSave}
            />
          </div>
        ) : (
          <div className="grid h-[34rem] grid-cols-1 gap-3 md:grid-cols-2">
            <div className="overflow-y-auto rounded-2xl border border-slate-200 bg-white p-3 dark:border-slate-800 dark:bg-slate-900">
              <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
                Árbol
              </h2>
              <FileTree activePath={path} onOpenFile={handleOpenFile} />
            </div>
            <div className="overflow-y-auto rounded-2xl border border-slate-200 bg-white p-3 dark:border-slate-800 dark:bg-slate-900">
              <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
                Buscar
              </h2>
              <SearchPanel onOpenFile={handleOpenFile} />
            </div>
          </div>
        )}

        <Terminal disabled={connected === false} />
      </div>
    </div>
  );
}
