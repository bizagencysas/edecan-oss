"use client";

import { useEffect, useRef, useState } from "react";

import { FileIcon, UploadIcon } from "@/components/icons";
import { Alert, Badge, Card, CardBody, CardHeader, EmptyState, PageHeader, Spinner } from "@/components/ui";
import { listFiles, uploadFile } from "@/lib/api";
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
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    void load();
  }, []);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setFiles(await listFiles());
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudieron cargar los archivos.");
    } finally {
      setLoading(false);
    }
  }

  async function handleFiles(fileList: FileList | null) {
    if (!fileList || fileList.length === 0) return;
    setUploading(true);
    setError(null);
    try {
      for (const file of Array.from(fileList)) {
        const uploaded = await uploadFile(file);
        setFiles((prev) => [uploaded, ...prev]);
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
          void handleFiles(e.dataTransfer.files);
        }}
        onClick={() => inputRef.current?.click()}
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
          className="hidden"
          onChange={(e) => void handleFiles(e.target.files)}
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
