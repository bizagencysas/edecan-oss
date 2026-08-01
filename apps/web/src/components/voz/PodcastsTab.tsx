"use client";

/**
 * Pestaña "Podcasts" de `/app/voz` (WP-V6-04, `ARCHITECTURE.md` §14/§10.7).
 * Formulario (título + guion dinámico de segmentos) → `POST /v1/voz/podcasts`
 * (`201`, encola `generate_podcast`) → lista con `Badge` de estado y polling
 * suave mientras haya algo `pending`/`running` (mismo patrón que
 * `app/misiones/page.tsx`: `setInterval` + `useRef` espejo del estado para
 * evitar cierres obsoletos, con `clearInterval` en el cleanup del efecto).
 *
 * El botón "Descargar" para un podcast `done` NO descarga bytes directamente
 * — ver el docstring de `lib/api-voz.ts` ("Descarga de un podcast done") para
 * el porqué (`GET /v1/files/{id}` solo da metadata hoy, en TODO el producto,
 * no solo acá) — confirma que el archivo existe con `getFile` y lleva al
 * usuario a `/app/archivos`, la misma página donde ya aparece cualquier otro
 * archivo generado.
 */

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { PlusIcon, TrashIcon } from "@/components/icons";
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
  Label,
  Select,
  Spinner,
  Textarea,
} from "@/components/ui";
import {
  ApiError,
  crearPodcast,
  getFile,
  listPodcasts,
  listVoces,
  type Podcast,
  type PodcastStatus,
  type VozDisponible,
} from "@/lib/api-voz";
import { formatDateTime } from "@/lib/format";

const POLL_INTERVAL_MS = 3000;
const MAX_SEGMENTOS = 30;

interface SegmentoForm {
  texto: string;
  voz: string;
}

function segmentoVacio(): SegmentoForm {
  return { texto: "", voz: "" };
}

function estaActivo(status: PodcastStatus): boolean {
  return status === "pending" || status === "running";
}

const STATUS_LABEL: Record<PodcastStatus, string> = {
  pending: "Pendiente",
  running: "Generando…",
  done: "Listo",
  error: "Error",
};

const STATUS_VARIANT: Record<PodcastStatus, "neutral" | "warning" | "success" | "danger"> = {
  pending: "neutral",
  running: "warning",
  done: "success",
  error: "danger",
};

function PodcastStatusBadge({ status }: { status: PodcastStatus }) {
  return <Badge variant={STATUS_VARIANT[status]}>{STATUS_LABEL[status]}</Badge>;
}

export function PodcastsTab() {
  const router = useRouter();

  const [podcasts, setPodcasts] = useState<Podcast[]>([]);
  const [voces, setVoces] = useState<VozDisponible[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [titulo, setTitulo] = useState("");
  const [segmentos, setSegmentos] = useState<SegmentoForm[]>([segmentoVacio()]);
  const [creando, setCreando] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [descargandoId, setDescargandoId] = useState<string | null>(null);

  useEffect(() => {
    void load();
    // Voces disponibles para el selector de cada segmento — mismo `GET
    // /v1/voces` que ya usa la pestaña "Voces" (no se repite si falla: un
    // tenant sin `voice.web` simplemente ve el selector con solo "Voz por
    // defecto", no bloquea crear un podcast).
    listVoces()
      .then(setVoces)
      .catch(() => setVoces([]));
  }, []);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setPodcasts(await listPodcasts());
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "No se pudieron cargar los podcasts. ¿Tu plan incluye podcasts?",
      );
    } finally {
      setLoading(false);
    }
  }

  // Polling suave: solo mientras haya algún podcast pending/running (mismo
  // patrón que `app/misiones/page.tsx`) — `podcastsRef` evita que el
  // intervalo capture una lista obsoleta.
  const podcastsRef = useRef(podcasts);
  podcastsRef.current = podcasts;
  useEffect(() => {
    const interval = setInterval(() => {
      if (podcastsRef.current.some((p) => estaActivo(p.status))) void load();
    }, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, []);

  function handleAddSegmento() {
    setSegmentos((prev) =>
      prev.length >= MAX_SEGMENTOS ? prev : [...prev, segmentoVacio()],
    );
  }

  function handleRemoveSegmento(idx: number) {
    setSegmentos((prev) => (prev.length <= 1 ? prev : prev.filter((_, i) => i !== idx)));
  }

  function handleChangeSegmento(idx: number, campo: keyof SegmentoForm, valor: string) {
    setSegmentos((prev) => prev.map((s, i) => (i === idx ? { ...s, [campo]: valor } : s)));
  }

  const puedeEnviar =
    titulo.trim().length > 0 && segmentos.some((s) => s.texto.trim().length > 0);

  function resetFormulario() {
    setTitulo("");
    setSegmentos([segmentoVacio()]);
  }

  async function handleCrear() {
    if (!puedeEnviar) return;
    setCreando(true);
    setFormError(null);
    try {
      const guion = segmentos
        .filter((s) => s.texto.trim().length > 0)
        .map((s) => ({
          texto: s.texto.trim(),
          voz: s.voz.trim() || undefined,
        }));
      const nuevo = await crearPodcast({ titulo: titulo.trim(), guion });
      setPodcasts((prev) => [nuevo, ...prev]);
      resetFormulario();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "No se pudo crear el podcast.");
    } finally {
      setCreando(false);
    }
  }

  async function handleDescargar(podcast: Podcast) {
    if (!podcast.file_id) return;
    setDescargandoId(podcast.id);
    setError(null);
    try {
      await getFile(podcast.file_id);
      router.push("/app/archivos");
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "No se pudo abrir el archivo del podcast.",
      );
    } finally {
      setDescargandoId(null);
    }
  }

  return (
    <div>
      {error && (
        <div className="mb-4">
          <Alert variant="error">{error}</Alert>
        </div>
      )}

      {/* --- Crear podcast --------------------------------------------------- */}
      <div className="mb-6">
        <Card>
          <CardHeader
            title="Crear un podcast"
            description="Escribe el guion segmento por segmento. Se genera en segundo plano con la voz (TTS) de tu cuenta — ElevenLabs si la conectaste, o un audio de prueba offline si no."
          />
          <CardBody>
            <Field label="Título" htmlFor="podcast-titulo">
              <Input
                id="podcast-titulo"
                value={titulo}
                onChange={(e) => setTitulo(e.target.value)}
                placeholder="p. ej. Novedades de la semana"
                maxLength={300}
              />
            </Field>

            <div className="mt-4 space-y-3">
              <Label className="!mb-0">Guion</Label>
              {segmentos.map((segmento, idx) => (
                <div
                  key={idx}
                  className="flex flex-col gap-2 rounded-lg border border-slate-100 p-3 sm:flex-row sm:items-start dark:border-slate-800"
                >
                  <div className="flex-1 space-y-2">
                    <Textarea
                      value={segmento.texto}
                      onChange={(e) => handleChangeSegmento(idx, "texto", e.target.value)}
                      placeholder={`Segmento ${idx + 1}: qué dice este orador…`}
                      className="min-h-[3rem]"
                      maxLength={2000}
                    />
                    <Select
                      value={segmento.voz}
                      onChange={(e) => handleChangeSegmento(idx, "voz", e.target.value)}
                    >
                      <option value="">Voz por defecto de tu cuenta</option>
                      {voces.map((voz) => (
                        <option key={voz.voice_id} value={voz.voice_id}>
                          {voz.nombre}
                        </option>
                      ))}
                    </Select>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => handleRemoveSegmento(idx)}
                    disabled={segmentos.length <= 1}
                    aria-label={`Quitar segmento ${idx + 1}`}
                  >
                    <TrashIcon className="h-3.5 w-3.5" />
                  </Button>
                </div>
              ))}
              <Button
                variant="secondary"
                size="sm"
                onClick={handleAddSegmento}
                disabled={segmentos.length >= MAX_SEGMENTOS}
              >
                <PlusIcon className="h-4 w-4" />
                Agregar segmento
              </Button>
            </div>

            {formError && (
              <div className="mt-3">
                <Alert variant="error">{formError}</Alert>
              </div>
            )}

            <div className="mt-4 flex justify-end">
              <Button onClick={handleCrear} disabled={!puedeEnviar} loading={creando}>
                Crear podcast
              </Button>
            </div>
          </CardBody>
        </Card>
      </div>

      {/* --- Tus podcasts ------------------------------------------------------ */}
      <Card>
        <CardHeader
          title="Tus podcasts"
          description="Aparecen aquí en cuanto los pides desde este formulario o desde el chat."
        />
        <CardBody>
          {loading ? (
            <div className="flex justify-center py-8">
              <Spinner className="h-5 w-5 text-slate-400" />
            </div>
          ) : podcasts.length === 0 ? (
            <EmptyState
              title="Todavía no creaste ningún podcast"
              description="Usa el formulario de arriba, o pídeselo a tu asistente por chat."
            />
          ) : (
            <ul className="space-y-2">
              {podcasts.map((podcast) => (
                <li
                  key={podcast.id}
                  className="flex items-center justify-between gap-3 rounded-lg border border-slate-100 px-3 py-2.5 dark:border-slate-800"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <p className="truncate text-sm font-medium text-slate-700 dark:text-slate-200">
                        {podcast.titulo}
                      </p>
                      <PodcastStatusBadge status={podcast.status} />
                    </div>
                    <p className="text-xs text-slate-400">
                      {podcast.guion.length} segmento(s) · Creado{" "}
                      {formatDateTime(podcast.created_at)}
                    </p>
                    {podcast.status === "error" && podcast.error && (
                      <p className="mt-1 text-xs text-rose-600 dark:text-rose-400">
                        {podcast.error}
                      </p>
                    )}
                  </div>
                  {podcast.status === "running" || podcast.status === "pending" ? (
                    <Spinner className="h-4 w-4 shrink-0 text-slate-400" />
                  ) : podcast.status === "done" && podcast.file_id ? (
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => void handleDescargar(podcast)}
                      loading={descargandoId === podcast.id}
                    >
                      Descargar
                    </Button>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
