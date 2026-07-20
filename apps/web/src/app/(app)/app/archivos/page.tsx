"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { FileIcon, UploadIcon } from "@/components/icons";
import { Alert, Badge, Card, CardBody, CardHeader, EmptyState, PageHeader, Spinner } from "@/components/ui";
import { listFiles, uploadFile } from "@/lib/api";
import {
  createLatestRequestGuard,
  FILE_STATUS_POLL_MS,
  filesNeedRefresh,
  mergeFileSnapshots,
  upsertFile,
} from "@/lib/file-status";
import { formatDateTime } from "@/lib/format";
import type { FileOut } from "@/lib/types";

const STATUS_VARIANT: Record<string, "brand" | "success" | "warning" | "danger" | "neutral"> = {
  uploaded: "brand",
  processing: "warning",
  ready: "success",
  error: "danger",
};

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function ArchivosPage() {
  const [files, setFiles] = useState<FileOut[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [pollCycle, setPollCycle] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const requestGuardRef = useRef(createLatestRequestGuard());

  const load = useCallback(async (background = false) => {
    const ticket = requestGuardRef.current.begin();
    if (!background) {
      setLoading(true);
      setError(null);
    }
    try {
      const remoteFiles = await listFiles();
      if (!ticket.isCurrent()) return;
      setFiles((current) => mergeFileSnapshots(current, remoteFiles));
      setError(null);
    } catch (err) {
      if (!ticket.isCurrent()) return;
      setError(err instanceof Error ? err.message : "No se pudieron cargar los archivos.");
    } finally {
      if (!ticket.isCurrent()) return;
      if (background) {
        // A failed poll must schedule another attempt while an active file
        // remains; success also advances the cycle if statuses did not change.
        setPollCycle((cycle) => cycle + 1);
      } else {
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    const requestGuard = requestGuardRef.current;
    void load();
    return () => requestGuard.invalidate();
  }, [load]);

  useEffect(() => {
    if (loading || uploading || !filesNeedRefresh(files)) return;
    const timer = window.setTimeout(() => void load(true), FILE_STATUS_POLL_MS);
    return () => window.clearTimeout(timer);
  }, [files, load, loading, pollCycle, uploading]);

  async function handleFiles(selectedFiles: readonly File[]) {
    if (uploading || selectedFiles.length === 0) return;
    // Any list request started before this upload is now stale and must not
    // replace the newly created record when it eventually resolves.
    requestGuardRef.current.invalidate();
    setLoading(false);
    setUploading(true);
    setError(null);
    try {
      for (const file of selectedFiles) {
        const uploaded = await uploadFile(file);
        setFiles((prev) => upsertFile(prev, uploaded));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo subir el archivo.");
    } finally {
      setUploading(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="Archivos"
        description="Se suben a S3 y se procesan en segundo plano (job ingest_file) para consultarlos desde el chat."
      />
      {error && (
        <div className="mb-4">
          <Alert variant="error">{error}</Alert>
        </div>
      )}

      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          void handleFiles(Array.from(e.dataTransfer.files));
        }}
        onClick={() => {
          if (!uploading) inputRef.current?.click();
        }}
        className={`mb-6 flex cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed px-6 py-10 text-center transition-colors ${
          dragOver
            ? "border-brand-500 bg-brand-50 dark:bg-brand-950/30"
            : "border-slate-300 bg-white hover:border-brand-400 dark:border-slate-700 dark:bg-slate-900"
        }`}
      >
        <input
          ref={inputRef}
          type="file"
          multiple
          disabled={uploading}
          className="hidden"
          onChange={(event) => {
            const selectedFiles = Array.from(event.currentTarget.files ?? []);
            // Permite volver a elegir exactamente el mismo archivo después
            // de un error; los browsers no disparan change si el value queda.
            event.currentTarget.value = "";
            void handleFiles(selectedFiles);
          }}
        />
        {uploading ? <Spinner className="h-6 w-6 text-brand-600" /> : <UploadIcon className="h-6 w-6 text-slate-400" />}
        <p className="mt-2 text-sm font-medium text-slate-700 dark:text-slate-200">
          {uploading ? "Subiendo…" : "Arrastra un archivo aquí o haz clic para elegirlo"}
        </p>
      </div>

      <Card>
        <CardHeader title="Tus archivos" />
        <CardBody>
          {loading ? (
            <div className="flex justify-center py-8">
              <Spinner className="h-5 w-5 text-slate-400" />
            </div>
          ) : files.length === 0 ? (
            <EmptyState title="Sin archivos todavía" description="Sube contratos, documentos o cualquier archivo para que tu asistente los consulte." />
          ) : (
            <ul className="space-y-2">
              {files.map((file) => (
                <li
                  key={file.id}
                  className="flex items-center justify-between gap-3 rounded-lg border border-slate-100 px-3 py-2.5 dark:border-slate-800"
                >
                  <div className="flex min-w-0 items-center gap-3">
                    <FileIcon className="h-4 w-4 shrink-0 text-slate-400" />
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-slate-700 dark:text-slate-200">{file.filename}</p>
                      <p className="text-xs text-slate-400">
                        {file.mime} · {humanSize(file.size_bytes)} · {formatDateTime(file.created_at)}
                      </p>
                    </div>
                  </div>
                  <Badge variant={STATUS_VARIANT[file.status] ?? "neutral"}>{file.status}</Badge>
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
