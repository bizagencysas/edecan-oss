"use client";

import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui";
import type { StudioPreviewArtifact } from "@/lib/studio";
import { CanvasAnnotations } from "./CanvasAnnotations";
import type { StudioSelection } from "./InlineInspector";

export interface StudioPreview extends StudioPreviewArtifact { objectUrl: string }

function PreviewContent({ preview, label }: { preview: StudioPreview; label: string }) {
  if (preview.kind === "image") {
    // eslint-disable-next-line @next/next/no-img-element -- object URL privado, autenticado y efímero.
    return <img src={preview.objectUrl} alt={label} className="h-full w-full object-contain" />;
  }
  return <iframe src={preview.objectUrl} title={label} sandbox="" referrerPolicy="no-referrer" className="h-full w-full border-0 bg-white" />;
}

export function MasterCanvas({ preview, width, height, projectName, revisionLabel, selection, onSelectionChange, onAnnotationsChange, onDownload }: {
  preview: StudioPreview | null;
  width: number;
  height: number;
  projectName: string;
  revisionLabel: string;
  selection: StudioSelection | null;
  onSelectionChange: (selection: StudioSelection | null) => void;
  onAnnotationsChange: (description: string) => void;
  onDownload: () => void;
}) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const [fitScale, setFitScale] = useState(0.8);
  const [zoom, setZoom] = useState(1);
  const [inspect, setInspect] = useState(false);
  const [annotate, setAnnotate] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);

  useEffect(() => {
    const element = viewportRef.current;
    if (!element) return;
    const resize = () => setFitScale(Math.max(0.12, Math.min(1, (element.clientWidth - 48) / width, (element.clientHeight - 48) / height)));
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(element);
    return () => observer.disconnect();
  }, [width, height]);

  useEffect(() => {
    if (!fullscreen) return;
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") setFullscreen(false); };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [fullscreen]);

  const scale = fitScale * zoom;
  function placePin(event: React.MouseEvent<HTMLButtonElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    const x = Math.round(((event.clientX - rect.left) / rect.width) * width);
    const y = Math.round(((event.clientY - rect.top) / rect.height) * height);
    onSelectionChange({ x, y, label: `Punto señalado en x ${x}, y ${y} del lienzo ${width}×${height}` });
    setInspect(false);
  }

  return (
    <section className="flex h-full min-h-0 flex-col bg-slate-100 dark:bg-slate-950" aria-label="Lienzo maestro">
      <header className="flex min-h-12 flex-wrap items-center justify-between gap-2 border-b border-slate-200 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-900">
        <div className="min-w-0"><p className="truncate text-xs font-semibold text-slate-800 dark:text-white">{projectName || "Nuevo diseño"}</p><p className="text-[10px] text-slate-500">{revisionLabel || "Sin revisión"} · {width}×{height}</p></div>
        <div className="flex flex-wrap items-center justify-end gap-1">
          <Button type="button" size="sm" variant={inspect ? "primary" : "ghost"} onClick={() => { setInspect((value) => !value); setAnnotate(false); }} disabled={!preview}>Señalar</Button>
          <Button type="button" size="sm" variant={annotate ? "primary" : "ghost"} onClick={() => { setAnnotate((value) => !value); setInspect(false); }} disabled={!preview}>Anotar</Button>
          <Button type="button" size="sm" variant="ghost" onClick={() => setZoom((value) => Math.max(0.5, value - 0.1))} disabled={!preview} aria-label="Alejar">−</Button>
          <span className="w-10 text-center text-[10px] tabular-nums text-slate-500">{Math.round(zoom * 100)}%</span>
          <Button type="button" size="sm" variant="ghost" onClick={() => setZoom((value) => Math.min(2, value + 0.1))} disabled={!preview} aria-label="Acercar">+</Button>
          <Button type="button" size="sm" variant="ghost" onClick={() => setZoom(1)} disabled={!preview}>Ajustar</Button>
          <Button type="button" size="sm" variant="secondary" onClick={() => setFullscreen(true)} disabled={!preview}>Pantalla completa</Button>
          <Button type="button" size="sm" onClick={onDownload} disabled={!preview}>Descargar</Button>
        </div>
      </header>
      <div ref={viewportRef} className="min-h-0 flex-1 overflow-auto p-6">
        {!preview ? <div className="flex h-full min-h-72 items-center justify-center"><div className="max-w-sm text-center"><div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-white text-2xl text-brand-600 shadow-sm dark:bg-slate-900">✦</div><h2 className="mt-4 text-lg font-semibold text-slate-800 dark:text-white">Tu idea se vuelve editable aquí</h2><p className="mt-2 text-sm leading-relaxed text-slate-500">Describe una imagen, campaña, producto, interfaz, presentación o historia. Edecán guardará cada cambio como una revisión reversible.</p></div></div> : (
          <div className="mx-auto origin-top-left overflow-hidden rounded-xl bg-white shadow-[0_20px_70px_rgba(15,23,42,0.16)] ring-1 ring-slate-900/10" style={{ width: width * scale, height: height * scale }}>
            <div className="relative origin-top-left" style={{ width, height, transform: `scale(${scale})` }}>
              <PreviewContent preview={preview} label={`Diseño ${projectName}`} />
              {inspect && <button type="button" aria-label="Señalar una zona del diseño" className="absolute inset-0 cursor-crosshair bg-brand-500/5" onClick={placePin} />}
              {selection && <div className="pointer-events-none absolute z-30 flex h-7 w-7 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border-2 border-white bg-brand-600 text-xs font-bold text-white shadow-lg" style={{ left: selection.x, top: selection.y }}>1</div>}
              <CanvasAnnotations width={width} height={height} active={annotate} onChange={onAnnotationsChange} />
            </div>
          </div>
        )}
      </div>
      {fullscreen && preview && <div className="fixed inset-0 z-[85] flex flex-col bg-slate-950/95 p-3" role="dialog" aria-modal="true" aria-label={`Pantalla completa de ${projectName}`}><div className="mb-2 flex items-center justify-between text-white"><span className="truncate text-sm font-medium">{projectName} · {revisionLabel}</span><Button type="button" size="sm" variant="secondary" onClick={() => setFullscreen(false)} autoFocus>Cerrar</Button></div><div className="min-h-0 flex-1 overflow-auto rounded-xl bg-slate-200 p-4"><div className="mx-auto h-full max-w-full bg-white"><PreviewContent preview={preview} label={`Pantalla completa ${projectName}`} /></div></div></div>}
    </section>
  );
}
