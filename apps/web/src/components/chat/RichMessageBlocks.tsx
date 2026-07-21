"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button, Spinner } from "@/components/ui";
import { downloadFile } from "@/lib/api";
import { chatScreenPath, flightStopsLabel, sourceModeLabel } from "@/lib/chat-blocks";
import type { ChatAction, ChatBlock, FlightCardBlock, HotelCardBlock, MediaBlock } from "@/lib/types";

import { ArtifactLinks } from "./ArtifactLinks";

function SourceBadge({ mode }: { mode: "demo" | "live" | "unknown" }) {
  const styles =
    mode === "live"
      ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300"
      : mode === "demo"
        ? "bg-amber-50 text-amber-700 dark:bg-amber-950/50 dark:text-amber-300"
        : "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300";
  return <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${styles}`}>{sourceModeLabel(mode)}</span>;
}

function BlockActions({
  actions,
  onPrefillMessage,
}: {
  actions: ChatAction[];
  onPrefillMessage?: (message: string) => void;
}) {
  const router = useRouter();
  if (actions.length === 0) return null;

  function run(action: ChatAction) {
    if (action.action === "open_url") {
      window.open(action.url, "_blank", "noopener,noreferrer");
    } else if (action.action === "open_screen") {
      router.push(chatScreenPath(action.screen));
    } else {
      onPrefillMessage?.(action.message);
    }
  }

  return (
    <div className="mt-3 flex flex-wrap gap-2">
      {actions.map((action) => (
        <Button key={action.id} type="button" size="sm" variant="secondary" onClick={() => run(action)}>
          {action.label}
        </Button>
      ))}
    </div>
  );
}

function AuthenticatedMedia({ block }: { block: MediaBlock }) {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    let createdUrl: string | null = null;
    setObjectUrl(null);
    setError(null);
    downloadFile(block.artifact.file_id)
      .then((blob) => {
        if (!active) return;
        createdUrl = URL.createObjectURL(blob);
        setObjectUrl(createdUrl);
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : "No se pudo cargar este archivo.");
      });
    return () => {
      active = false;
      if (createdUrl) URL.revokeObjectURL(createdUrl);
    };
  }, [block.artifact.file_id]);

  return (
    <section className="overflow-hidden rounded-xl border border-slate-200 bg-slate-50 dark:border-slate-700 dark:bg-slate-950/40">
      {!objectUrl && !error && (
        <div className="flex min-h-24 items-center justify-center gap-2 text-xs text-slate-500">
          <Spinner className="h-4 w-4" /> Cargando {block.media_kind === "image" ? "imagen" : block.media_kind === "video" ? "video" : "audio"}…
        </div>
      )}
      {objectUrl && block.media_kind === "image" && (
        // eslint-disable-next-line @next/next/no-img-element -- blob autenticado, sin URL estable para next/image.
        <img src={objectUrl} alt={block.alt} className="max-h-[32rem] w-full object-contain" />
      )}
      {objectUrl && block.media_kind === "video" && (
        <video src={objectUrl} controls preload="metadata" className="max-h-[32rem] w-full" aria-label={block.alt || block.caption || block.artifact.filename} />
      )}
      {objectUrl && block.media_kind === "audio" && (
        <div className="p-3">
          <audio src={objectUrl} controls preload="metadata" className="w-full" aria-label={block.alt || block.caption || block.artifact.filename} />
        </div>
      )}
      {error && <p className="p-3 text-xs text-rose-600 dark:text-rose-400">{error}</p>}
      {(block.caption || error) && (
        <div className="border-t border-slate-200 px-3 py-2 dark:border-slate-700">
          {block.caption && <p className="text-xs text-slate-600 dark:text-slate-300">{block.caption}</p>}
          {error && <ArtifactLinks artifacts={[block.artifact]} />}
        </div>
      )}
    </section>
  );
}

function TravelMeta({ block }: { block: FlightCardBlock | HotelCardBlock }) {
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
      <SourceBadge mode={block.source_mode} />
      {block.provider && <span>{block.provider}</span>}
      {block.taxes && <span>Impuestos: {block.taxes}</span>}
    </div>
  );
}

function FlightCard({ block, onPrefillMessage }: { block: FlightCardBlock; onPrefillMessage?: (message: string) => void }) {
  return (
    <article className="rounded-xl border border-slate-200 bg-slate-50 p-3 dark:border-slate-700 dark:bg-slate-950/40">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-semibold text-slate-900 dark:text-white">{block.airline}</p>
          <p className="mt-1 text-lg font-semibold tracking-wide">
            {block.origin} <span className="text-slate-400">→</span> {block.destination}
          </p>
        </div>
        <p className="whitespace-nowrap text-right font-semibold text-brand-700 dark:text-brand-300">
          {block.currency} {block.price}
        </p>
      </div>
      <div className="mt-2 grid gap-1 text-xs text-slate-600 dark:text-slate-300 sm:grid-cols-2">
        {block.departure && <span>Salida: {block.departure}</span>}
        {block.arrival && <span>Llegada: {block.arrival}</span>}
        <span>{flightStopsLabel(block.stops)}</span>
        {block.expires_at && <span>Oferta hasta: {block.expires_at}</span>}
      </div>
      <TravelMeta block={block} />
      {block.cancellation && <p className="mt-2 text-xs text-slate-500">{block.cancellation}</p>}
      <BlockActions actions={block.actions} onPrefillMessage={onPrefillMessage} />
    </article>
  );
}

function HotelCard({ block, onPrefillMessage }: { block: HotelCardBlock; onPrefillMessage?: (message: string) => void }) {
  return (
    <article className="rounded-xl border border-slate-200 bg-slate-50 p-3 dark:border-slate-700 dark:bg-slate-950/40">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-semibold text-slate-900 dark:text-white">{block.name}</p>
          <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
            {block.city}{block.rating ? ` · ${block.rating}★` : ""}
          </p>
        </div>
        <p className="whitespace-nowrap text-right font-semibold text-brand-700 dark:text-brand-300">
          {block.currency} {block.price}
        </p>
      </div>
      {(block.checkin || block.checkout) && (
        <p className="mt-2 text-xs text-slate-600 dark:text-slate-300">
          {block.checkin || "—"} <span className="text-slate-400">→</span> {block.checkout || "—"}
        </p>
      )}
      <TravelMeta block={block} />
      {block.cancellation && <p className="mt-2 text-xs text-slate-500">{block.cancellation}</p>}
      <BlockActions actions={block.actions} onPrefillMessage={onPrefillMessage} />
    </article>
  );
}

export function RichMessageBlocks({
  blocks,
  onPrefillMessage,
}: {
  blocks: ChatBlock[];
  onPrefillMessage?: (message: string) => void;
}) {
  if (blocks.length === 0) return null;
  return (
    <div className="mt-3 grid gap-3">
      {blocks.map((block, index) => {
        const key = `${block.type}-${index}`;
        if (block.type === "media") return <AuthenticatedMedia key={key} block={block} />;
        if (block.type === "flight") return <FlightCard key={key} block={block} onPrefillMessage={onPrefillMessage} />;
        if (block.type === "hotel") return <HotelCard key={key} block={block} onPrefillMessage={onPrefillMessage} />;
        return (
          <article key={key} className="rounded-xl border border-slate-200 bg-slate-50 p-3 dark:border-slate-700 dark:bg-slate-950/40">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="truncate text-xs text-slate-500 dark:text-slate-400">{block.site_name ?? new URL(block.url).hostname}</p>
                <a href={block.url} target="_blank" rel="noopener noreferrer" className="mt-0.5 block font-semibold text-slate-900 hover:text-brand-700 dark:text-white dark:hover:text-brand-300">
                  {block.title}
                </a>
              </div>
              <SourceBadge mode={block.source_mode} />
            </div>
            {block.description && <p className="mt-2 text-xs text-slate-600 dark:text-slate-300">{block.description}</p>}
            <BlockActions actions={block.actions} onPrefillMessage={onPrefillMessage} />
          </article>
        );
      })}
    </div>
  );
}
