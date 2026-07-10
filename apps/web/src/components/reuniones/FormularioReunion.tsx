"use client";

/**
 * Formulario "Transcribir y resumir" — selector de archivo (audio/video ya
 * subido, filtrado de `GET /v1/files` vía `listFiles()` de `lib/api.ts`),
 * campo título opcional, y el disclaimer de consentimiento SIEMPRE visible
 * junto al botón (ARCHITECTURE.md §15, WP-V6-05; ver `docs/reuniones.md`).
 */

import { useEffect, useState } from "react";

import { FileIcon } from "@/components/icons";
import { Alert, Button, Field, Input, Select } from "@/components/ui";
import { listFiles } from "@/lib/api";
import {
  ApiError,
  DISCLAIMER_CONSENTIMIENTO,
  crearReunion,
  type ReunionOut,
} from "@/lib/api-reuniones";
import { formatDateTime } from "@/lib/format";
import type { FileOut } from "@/lib/types";

export function esAudioOVideo(mime: string): boolean {
  return mime.startsWith("audio/") || mime.startsWith("video/");
}

export function FormularioReunion({ onCreated }: { onCreated: (r: ReunionOut) => void }) {
  const [archivos, setArchivos] = useState<FileOut[]>([]);
  const [loadingArchivos, setLoadingArchivos] = useState(true);
  const [fileId, setFileId] = useState("");
  const [titulo, setTitulo] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void cargarArchivos();
  }, []);

  async function cargarArchivos() {
    setLoadingArchivos(true);
    try {
      const todos = await listFiles();
      setArchivos(todos.filter((f) => esAudioOVideo(f.mime) && f.status === "ready"));
    } catch {
      // El selector queda vacío; el aviso de "sin archivos" de abajo ya lo cubre.
    } finally {
      setLoadingArchivos(false);
    }
  }

  async function enviar(e: React.FormEvent) {
    e.preventDefault();
    if (!fileId) {
      setError("Elegí un audio o video ya subido.");
      return;
    }
    setEnviando(true);
    setError(null);
    try {
      const reunion = await crearReunion({ file_id: fileId, titulo: titulo.trim() || undefined });
      setTitulo("");
      setFileId("");
      onCreated(reunion);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo encolar la transcripción.");
    } finally {
      setEnviando(false);
    }
  }

  return (
    <form onSubmit={enviar} className="space-y-4">
      {error && <Alert variant="error">{error}</Alert>}

      {!loadingArchivos && archivos.length === 0 ? (
        <Alert variant="info">
          Todavía no tienes audios o videos subidos. Sube la grabación de tu reunión en{" "}
          <a href="/app/archivos" className="font-medium underline">
            /app/archivos
          </a>{" "}
          y volvé acá.
        </Alert>
      ) : (
        <Field label="Audio o video de la reunión" htmlFor="reunion-archivo">
          <Select
            id="reunion-archivo"
            value={fileId}
            onChange={(e) => setFileId(e.target.value)}
            disabled={loadingArchivos}
          >
            <option value="">{loadingArchivos ? "Cargando archivos…" : "Elegí un archivo…"}</option>
            {archivos.map((f) => (
              <option key={f.id} value={f.id}>
                {f.filename} · {formatDateTime(f.created_at)}
              </option>
            ))}
          </Select>
        </Field>
      )}

      <Field
        label="Título (opcional)"
        htmlFor="reunion-titulo"
        hint="Si lo dejas vacío, usamos el nombre del archivo."
      >
        <Input
          id="reunion-titulo"
          value={titulo}
          onChange={(e) => setTitulo(e.target.value)}
          placeholder="Ej: Kickoff del proyecto"
        />
      </Field>

      <Alert variant="info">
        <span className="flex items-start gap-2">
          <FileIcon className="mt-0.5 h-4 w-4 shrink-0" />
          {DISCLAIMER_CONSENTIMIENTO}
        </span>
      </Alert>

      <Button type="submit" loading={enviando} disabled={!fileId || enviando}>
        Transcribir y resumir
      </Button>
    </form>
  );
}
