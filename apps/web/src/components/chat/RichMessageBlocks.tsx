"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button, Spinner } from "@/components/ui";
import { downloadFile } from "@/lib/api";
import { chatScreenPath, flightStopsLabel, sourceModeLabel } from "@/lib/chat-blocks";
import type {
  ChatAction,
  ChatBlock,
  FlightCardBlock,
  HotelCardBlock,
  MediaBlock,
  ChartBlock,
  QuestionBlock,
  SourcesBlock,
} from "@/lib/types";

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
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
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
    <article className="overflow-hidden rounded-xl border border-slate-200 bg-slate-50 dark:border-slate-700 dark:bg-slate-950/40">
      {block.image_url ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={block.image_url} alt="" className="h-36 w-full object-cover" />
      ) : null}
      <div className="p-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="font-semibold text-slate-900 dark:text-white">{block.name}</p>
            <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
              {block.address || block.city}
              {block.rating ? ` · ${block.rating}★` : ""}
            </p>
          </div>
          <p className="whitespace-nowrap text-right text-lg font-semibold text-brand-700 dark:text-brand-300">
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
      </div>
    </article>
  );
}

export function RichMessageBlocks({
  blocks,
  onPrefillMessage,
  onAnswer,
}: {
  blocks: ChatBlock[];
  onPrefillMessage?: (message: string) => void;
  /**
   * Manda la respuesta de un `QuestionBlock` como mensaje del usuario. Si no se
   * pasa, se cae a `onPrefillMessage`: la respuesta queda escrita en el campo y
   * el usuario la manda -- un paso más, pero nunca se pierde la elección.
   */
  onAnswer?: (message: string) => void;
}) {
  if (blocks.length === 0) return null;
  return (
    <div className="mt-3 grid gap-3">
      {blocks.map((block, index) => {
        const key = `${block.type}-${index}`;
        if (block.type === "media") return <AuthenticatedMedia key={key} block={block} />;
        if (block.type === "flight") return <FlightCard key={key} block={block} onPrefillMessage={onPrefillMessage} />;
        if (block.type === "hotel") return <HotelCard key={key} block={block} onPrefillMessage={onPrefillMessage} />;
        if (block.type === "question")
          return <QuestionCard key={key} block={block} onAnswer={onAnswer ?? onPrefillMessage} />;
        if (block.type === "sources") return <SourcesCard key={key} block={block} />;
        if (block.type === "chart") return <ChartCard key={key} block={block} />;
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

/**
 * Modal de opciones de `QuestionBlock`: el agente pregunta en vez de adivinar.
 *
 * Una vez respondida queda deshabilitada y con la elección marcada, para que al
 * releer el hilo se vea qué se contestó y no se pueda responder dos veces.
 */
function ChartCard({ block }: { block: ChartBlock }) {
  const max = Math.max(...block.series.map((point) => Math.abs(point.value)), 1);
  return (
    <article className="rounded-xl border border-slate-200 bg-slate-50 p-3 dark:border-slate-700 dark:bg-slate-950/40">
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
        {block.title}
      </p>
      <ul className="mt-2 space-y-1.5">
        {block.series.map((point) => (
          <li key={point.label} className="text-xs">
            <div className="mb-0.5 flex justify-between gap-2 text-slate-600 dark:text-slate-300">
              <span>{point.label}</span>
              <span>{point.value}</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800">
              <div
                className="h-full rounded-full bg-brand-500"
                style={{ width: `${Math.max(8, (Math.abs(point.value) / max) * 100)}%` }}
              />
            </div>
          </li>
        ))}
      </ul>
    </article>
  );
}

function SourcesCard({ block }: { block: SourcesBlock }) {
  return (
    <details className="rounded-xl border border-slate-200 bg-slate-50 p-3 dark:border-slate-700 dark:bg-slate-950/40">
      <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
        Fuentes · {block.citations.length}
      </summary>
      <ul className="mt-2 space-y-2">
        {block.citations.map((citation) => (
          <li key={citation.id}>
            <a
              href={citation.url}
              target="_blank"
              rel="noopener noreferrer"
              className="block text-sm font-medium text-slate-900 hover:text-brand-700 dark:text-white dark:hover:text-brand-300"
            >
              {citation.title}
            </a>
            <p className="text-[11px] text-slate-500 dark:text-slate-400">
              {[citation.source, citation.retrieved_at].filter(Boolean).join(" · ")}
            </p>
            {citation.excerpt && (
              <p className="mt-0.5 text-xs text-slate-600 dark:text-slate-300">{citation.excerpt}</p>
            )}
          </li>
        ))}
      </ul>
    </details>
  );
}

function QuestionCard({
  block,
  onAnswer,
}: {
  block: QuestionBlock;
  onAnswer?: (message: string) => void;
}) {
  const [selected, setSelected] = useState<string[]>([]);
  const [answered, setAnswered] = useState(false);

  const textOf = (label: string) => {
    const option = block.options.find((item) => item.label === label);
    const raw = option?.value?.trim();
    return raw && raw.length > 0 ? raw : label;
  };

  const choose = (label: string) => {
    if (answered) return;
    if (block.multi_select) {
      setSelected((prev) => (prev.includes(label) ? prev.filter((x) => x !== label) : [...prev, label]));
      return;
    }
    setSelected([label]);
    setAnswered(true);
    onAnswer?.(textOf(label));
  };

  const sendMulti = () => {
    if (answered || selected.length === 0) return;
    // Se recorre `block.options` y no `selected` para respetar el orden en que
    // se mostraron: al revés, el mensaje resultante confundiría.
    const texts = block.options.filter((o) => selected.includes(o.label)).map((o) => textOf(o.label));
    setAnswered(true);
    onAnswer?.(texts.join(", "));
  };

  return (
    <article className="rounded-xl border border-brand-200 bg-brand-50/40 p-3 dark:border-brand-800 dark:bg-brand-950/20">
      {block.header && (
        <p className="text-[11px] font-bold uppercase tracking-wide text-brand-700 dark:text-brand-300">
          {block.header}
        </p>
      )}
      <p className="mt-1 text-sm font-semibold text-slate-900 dark:text-white">{block.question}</p>
      <div className="mt-3 grid gap-2">
        {block.options.map((option) => {
          const active = selected.includes(option.label);
          return (
            <button
              key={option.label}
              type="button"
              disabled={answered}
              onClick={() => choose(option.label)}
              aria-pressed={active}
              className={`rounded-lg border p-2.5 text-left transition disabled:cursor-default ${
                active
                  ? "border-brand-500 bg-brand-100/70 dark:border-brand-400 dark:bg-brand-900/40"
                  : "border-slate-200 bg-white hover:border-brand-300 dark:border-slate-700 dark:bg-slate-950/40"
              }`}
            >
              <span className="block text-xs font-semibold text-slate-900 dark:text-white">{option.label}</span>
              {option.description && (
                <span className="mt-0.5 block text-[11px] text-slate-600 dark:text-slate-300">{option.description}</span>
              )}
            </button>
          );
        })}
      </div>
      {block.multi_select && !answered && (
        <Button className="mt-3 w-full" disabled={selected.length === 0} onClick={sendMulti}>
          Enviar respuesta
        </Button>
      )}
      {block.allow_free_text && !answered && (
        <p className="mt-2 text-[11px] text-slate-500 dark:text-slate-400">O escribe tu propia respuesta abajo.</p>
      )}
    </article>
  );
}
