"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui";
import { downloadFile } from "@/lib/api";
import type { ArtifactRef } from "@/lib/types";

type PreviewKind = "html" | "pdf" | "image";

interface ArtifactPreview {
  artifact: ArtifactRef;
  kind: PreviewKind;
  objectUrl: string;
}

function previewKind(artifact: ArtifactRef): PreviewKind | null {
  const mime = (artifact.mime ?? "").toLowerCase();
  const filename = artifact.filename.toLowerCase();
  if (mime === "text/html" || filename.endsWith(".html") || filename.endsWith(".htm")) return "html";
  if (mime === "application/pdf" || filename.endsWith(".pdf")) return "pdf";
  if (mime.startsWith("image/") || /\.(png|jpe?g|webp|gif|svg)$/.test(filename)) return "image";
  return null;
}

const STUDIO_PREVIEW_CSP =
  "default-src 'none'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; " +
  "media-src data: blob:; connect-src 'none'; frame-src 'none'; object-src 'none'; " +
  "form-action 'none'; base-uri 'none'";

async function isolatedHtmlBlob(blob: Blob): Promise<Blob> {
  const raw = await blob.text();
  const csp = `<meta http-equiv="Content-Security-Policy" content="${STUDIO_PREVIEW_CSP}">`;
  const html = /<head(?:\s[^>]*)?>/i.test(raw)
    ? raw.replace(/<head(?:\s[^>]*)?>/i, (head) => `${head}${csp}`)
    : `<!doctype html><html><head>${csp}</head><body>${raw}</body></html>`;
  return new Blob([html], { type: "text/html;charset=utf-8" });
}

export function ArtifactLinks({ artifacts }: { artifacts: ArtifactRef[] }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<ArtifactPreview | null>(null);

  useEffect(() => {
    if (!preview) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setPreview(null);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [preview]);

  useEffect(
    () => () => {
      if (preview) URL.revokeObjectURL(preview.objectUrl);
    },
    [preview],
  );

  if (artifacts.length === 0) return null;

  function closePreview() {
    setPreview(null);
  }

  async function openPreview(artifact: ArtifactRef) {
    const kind = previewKind(artifact);
    if (!kind) return;
    setBusy(`preview:${artifact.file_id}`);
    setError(null);
    try {
      const blob = await downloadFile(artifact.file_id);
      const previewBlob = kind === "html" ? await isolatedHtmlBlob(blob) : blob;
      const objectUrl = URL.createObjectURL(previewBlob);
      setPreview({ artifact, kind, objectUrl });
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo abrir la vista previa.");
    } finally {
      setBusy(null);
    }
  }

  async function download(artifact: ArtifactRef) {
    setBusy(`download:${artifact.file_id}`);
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
      setBusy(null);
    }
  }

  return (
    <div className="mt-2 flex flex-wrap gap-2">
      {artifacts.map((artifact) => {
        const kind = previewKind(artifact);
        return (
          <div key={artifact.file_id} className="flex flex-wrap gap-2">
            {kind && (
              <Button
                type="button"
                size="sm"
                variant="secondary"
                loading={busy === `preview:${artifact.file_id}`}
                disabled={busy !== null}
                onClick={() => void openPreview(artifact)}
                title={`Abrir ${artifact.filename} dentro de Edecán`}
              >
                Vista previa
              </Button>
            )}
            <Button
              type="button"
              size="sm"
              variant="secondary"
              loading={busy === `download:${artifact.file_id}`}
              disabled={busy !== null}
              onClick={() => void download(artifact)}
              title={artifact.mime ?? undefined}
            >
              Descargar {artifact.filename}
            </Button>
          </div>
        );
      })}
      {error && <p className="w-full text-xs text-rose-600 dark:text-rose-400">{error}</p>}
      {preview && (
        <div
          className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-950/75 p-3 backdrop-blur-sm sm:p-6"
          role="dialog"
          aria-modal="true"
          aria-label={`Vista previa de ${preview.artifact.filename}`}
          onMouseDown={(event) => {
            if (event.currentTarget === event.target) closePreview();
          }}
        >
          <section className="flex h-[min(90dvh,900px)] w-full max-w-6xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl dark:bg-slate-950">
            <header className="flex items-center justify-between gap-3 border-b border-slate-200 px-4 py-3 dark:border-slate-800">
              <p className="min-w-0 truncate text-sm font-semibold text-slate-900 dark:text-white">
                {preview.artifact.filename}
              </p>
              <Button type="button" size="sm" variant="ghost" onClick={closePreview} autoFocus>
                Cerrar
              </Button>
            </header>
            <div className="min-h-0 flex-1 bg-slate-100 dark:bg-slate-900">
              {preview.kind === "html" && (
                <iframe
                  src={preview.objectUrl}
                  title={`Diseño ${preview.artifact.filename}`}
                  sandbox=""
                  referrerPolicy="no-referrer"
                  className="h-full w-full border-0 bg-white"
                />
              )}
              {preview.kind === "pdf" && (
                <iframe
                  src={preview.objectUrl}
                  title={`PDF ${preview.artifact.filename}`}
                  sandbox=""
                  referrerPolicy="no-referrer"
                  className="h-full w-full border-0 bg-white"
                />
              )}
              {preview.kind === "image" && (
                <div className="flex h-full items-center justify-center overflow-auto p-4">
                  {/* eslint-disable-next-line @next/next/no-img-element -- blob autenticado y efímero. */}
                  <img
                    src={preview.objectUrl}
                    alt={`Vista previa de ${preview.artifact.filename}`}
                    className="max-h-full max-w-full object-contain shadow-lg"
                  />
                </div>
              )}
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
