"use client";

import { useState } from "react";

import { Button } from "@/components/ui";
import { downloadFile } from "@/lib/api";
import type { ArtifactRef } from "@/lib/types";

export function ArtifactLinks({ artifacts }: { artifacts: ArtifactRef[] }) {
  const [downloading, setDownloading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (artifacts.length === 0) return null;

  async function download(artifact: ArtifactRef) {
    setDownloading(artifact.file_id);
    setError(null);
    try {
      const blob = await downloadFile(artifact.file_id);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = artifact.filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      // Safari puede cancelar la descarga si el object URL se revoca en el
      // mismo tick que el click programático.
      window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo descargar el archivo.");
    } finally {
      setDownloading(null);
    }
  }

  return (
    <div className="mt-2 flex flex-wrap gap-2">
      {artifacts.map((artifact) => (
        <Button
          key={artifact.file_id}
          type="button"
          size="sm"
          variant="secondary"
          loading={downloading === artifact.file_id}
          disabled={downloading !== null}
          onClick={() => void download(artifact)}
          title={artifact.mime ?? undefined}
        >
          Descargar {artifact.filename}
        </Button>
      ))}
      {error && <p className="w-full text-xs text-rose-600 dark:text-rose-400">{error}</p>}
    </div>
  );
}
