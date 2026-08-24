"use client";

/**
 * Pestaña "Voces" de `/app/voz` (WP-V6-04 extrajo el contenido que antes vivía
 * directo en `app/voz/page.tsx` a este componente — mismo patrón de pestañas
 * que `/app/rrhh`, ver `components/rrhh/EmpleadosTab.tsx`): voces disponibles
 * para síntesis (TTS) y clonación de voz con consentimiento verificado.
 * Contenido/lógica sin cambios respecto a la versión anterior de la página.
 */

import { useEffect, useRef, useState } from "react";

import {
  CheckIcon,
  MicIcon,
  PlayIcon,
  SquareIcon,
  TrashIcon,
  UploadIcon,
} from "@/components/icons";
import {
  Alert,
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  Checkbox,
  EmptyState,
  Field,
  Input,
  Spinner,
  Textarea,
} from "@/components/ui";
import { speakText } from "@/lib/api";
import {
  ApiError,
  crearClon,
  listClones,
  listVoces,
  revocarClon,
  type VozClon,
  type VozDisponible,
} from "@/lib/api-voz";
import { formatDateTime } from "@/lib/format";

// Texto EXACTO de la declaración de consentimiento (pinned — no reformules
// esta frase: es la que el usuario declara aceptar, ver
// `apps/api/edecan_api/routers/voz_avanzada.py` y `docs/voz-telefonia.md`).
const ATTESTATION_TEXT =
  "Declaro que tengo el consentimiento explícito y grabado de la persona cuya voz voy a " +
  "clonar, y que la grabación adjunta lo demuestra.";

const MAX_MUESTRAS = 5;

function categoriaLabel(categoria: string): string {
  if (categoria === "cloned") return "Clonada";
  if (categoria === "premade" || categoria.startsWith("stub-")) return "Estándar";
  return categoria;
}

function VozBadge({ voz }: { voz: VozDisponible }) {
  const esClon = voz.categoria === "cloned";
  return <Badge variant={esClon ? "brand" : "neutral"}>{categoriaLabel(voz.categoria)}</Badge>;
}

function ClonStatusBadge({ status }: { status: VozClon["status"] }) {
  if (status === "attested") return <Badge variant="success">Activo</Badge>;
  return <Badge variant="neutral">Revocado</Badge>;
}

export function VocesTab() {
  const [voces, setVoces] = useState<VozDisponible[]>([]);
  const [clones, setClones] = useState<VozClon[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [nombre, setNombre] = useState("");
  const [descripcion, setDescripcion] = useState("");
  const [consentimiento, setConsentimiento] = useState<File | null>(null);
  const [muestras, setMuestras] = useState<File[]>([]);
  const [attestation, setAttestation] = useState(false);
  const [creando, setCreando] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [revocandoId, setRevocandoId] = useState<string | null>(null);
  const [previewLoadingId, setPreviewLoadingId] = useState<string | null>(null);
  const [previewPlayingId, setPreviewPlayingId] = useState<string | null>(null);
  const [previewErrors, setPreviewErrors] = useState<Record<string, string>>({});

  const consentimientoInputRef = useRef<HTMLInputElement | null>(null);
  const muestrasInputRef = useRef<HTMLInputElement | null>(null);
  const previewAudioRef = useRef<HTMLAudioElement | null>(null);
  const previewObjectUrlRef = useRef<string | null>(null);

  useEffect(() => {
    void load();
  }, []);

  useEffect(
    () => () => {
      previewAudioRef.current?.pause();
      if (previewObjectUrlRef.current) URL.revokeObjectURL(previewObjectUrlRef.current);
    },
    [],
  );

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [voicesRes, clonesRes] = await Promise.all([listVoces(), listClones()]);
      setVoces(voicesRes);
      setClones(clonesRes);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "No se pudieron cargar las voces. ¿Tu plan incluye voz web?",
      );
    } finally {
      setLoading(false);
    }
  }

  const puedeEnviar =
    nombre.trim().length > 0 &&
    consentimiento !== null &&
    muestras.length >= 1 &&
    muestras.length <= MAX_MUESTRAS &&
    attestation;

  function resetFormulario() {
    setNombre("");
    setDescripcion("");
    setConsentimiento(null);
    setMuestras([]);
    setAttestation(false);
    if (consentimientoInputRef.current) consentimientoInputRef.current.value = "";
    if (muestrasInputRef.current) muestrasInputRef.current.value = "";
  }

  async function handleCrearClon() {
    if (!puedeEnviar || !consentimiento) return;
    setCreando(true);
    setFormError(null);
    try {
      const nuevo = await crearClon({
        nombre: nombre.trim(),
        attestation,
        descripcion: descripcion.trim() || undefined,
        consentimiento,
        muestras,
      });
      setClones((prev) => [nuevo, ...prev]);
      resetFormulario();
    } catch (err) {
      setFormError(
        err instanceof ApiError ? err.message : "No se pudo crear el clon de voz.",
      );
    } finally {
      setCreando(false);
    }
  }

  async function handleRevocar(clon: VozClon) {
    setRevocandoId(clon.id);
    setError(null);
    try {
      const actualizado = await revocarClon(clon.id);
      setClones((prev) => prev.map((c) => (c.id === actualizado.id ? actualizado : c)));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo revocar el clon.");
    } finally {
      setRevocandoId(null);
    }
  }

  function stopPreview() {
    previewAudioRef.current?.pause();
    previewAudioRef.current = null;
    setPreviewPlayingId(null);
    if (previewObjectUrlRef.current) {
      URL.revokeObjectURL(previewObjectUrlRef.current);
      previewObjectUrlRef.current = null;
    }
  }

  async function previewVoice(voz: VozDisponible) {
    if (previewPlayingId === voz.voice_id) {
      stopPreview();
      return;
    }

    stopPreview();
    setPreviewLoadingId(voz.voice_id);
    setPreviewErrors((current) => {
      const next = { ...current };
      delete next[voz.voice_id];
      return next;
    });
    try {
      const blob = await speakText(
        `Hola. Soy ${voz.nombre}. Esta es una muestra de mi voz en Edecan.`,
        voz.voice_id,
      );
      const objectUrl = URL.createObjectURL(blob);
      const audio = new Audio(objectUrl);
      previewObjectUrlRef.current = objectUrl;
      previewAudioRef.current = audio;
      audio.onended = stopPreview;
      audio.onerror = () => {
        setPreviewErrors((current) => ({
          ...current,
          [voz.voice_id]: "El audio se generó, pero este equipo no pudo reproducirlo.",
        }));
        stopPreview();
      };
      await audio.play();
      setPreviewPlayingId(voz.voice_id);
    } catch (err) {
      stopPreview();
      setPreviewErrors((current) => ({
        ...current,
        [voz.voice_id]:
          err instanceof ApiError
            ? err.message
            : "No se pudo generar la muestra. Revisa la conexión de ElevenLabs.",
      }));
    } finally {
      setPreviewLoadingId(null);
    }
  }

  return (
    <div>
      {error && (
        <div className="mb-4">
          <Alert variant="error">{error}</Alert>
        </div>
      )}

      {/* --- Voces disponibles --------------------------------------------- */}
      <div className="mb-6">
        <Card>
          <CardHeader
            title="Voces disponibles"
            description="Las de tu cuenta de ElevenLabs si la conectaste (Configuración → Voz), o 2 voces de ejemplo si no."
          />
          <CardBody>
            {loading ? (
              <div className="flex justify-center py-8">
                <Spinner className="h-5 w-5 text-slate-400" />
              </div>
            ) : voces.length === 0 ? (
              <EmptyState title="Sin voces disponibles" />
            ) : (
              <ul className="space-y-2">
                {voces.map((voz) => (
                  <li
                    key={voz.voice_id}
                    className="flex items-center justify-between gap-3 rounded-lg border border-slate-100 px-3 py-2.5 dark:border-slate-800"
                  >
                    <div className="flex min-w-0 items-center gap-3">
                      <MicIcon className="h-4 w-4 shrink-0 text-slate-400" />
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium text-slate-700 dark:text-slate-200">
                          {voz.nombre}
                        </p>
                        <p className="truncate text-xs text-slate-400">{voz.voice_id}</p>
                      </div>
                    </div>
                    <div className="flex min-w-0 shrink-0 flex-col items-end gap-1">
                      <div className="flex items-center gap-2">
                        <Button
                          type="button"
                          size="sm"
                          variant="secondary"
                          loading={previewLoadingId === voz.voice_id}
                          onClick={() => void previewVoice(voz)}
                          aria-label={
                            previewPlayingId === voz.voice_id
                              ? `Detener muestra de ${voz.nombre}`
                              : `Escuchar muestra de ${voz.nombre}`
                          }
                        >
                          {previewPlayingId === voz.voice_id ? (
                            <>
                              <SquareIcon className="h-3.5 w-3.5" />
                              Detener
                            </>
                          ) : (
                            <>
                              <PlayIcon className="h-3.5 w-3.5" />
                              Escuchar
                            </>
                          )}
                        </Button>
                        <VozBadge voz={voz} />
                      </div>
                      {previewErrors[voz.voice_id] && (
                        <p className="max-w-xs text-right text-xs text-red-600 dark:text-red-400">
                          {previewErrors[voz.voice_id]}
                        </p>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>
      </div>

      {/* --- Clonación de voz autorizada ------------------------------------ */}
      <div className="mb-6">
        <Card>
          <CardHeader
            title="Clonar una voz (con consentimiento verificado)"
            description="Solo se clona con una grabación del consentimiento explícito de la persona. El asistente NUNCA clona una voz por su cuenta — esta es la única vía, y requiere tu confirmación aquí."
          />
          <CardBody>
            <Alert variant="info">
              Clonar la voz de otra persona sin su permiso puede ser ilegal y puede violar sus
              derechos de imagen/voz según tu jurisdicción. Úsalo solo con personas que
              conozcas y que hayan dado su consentimiento explícito, grabado en el archivo de
              abajo.
            </Alert>

            <div className="mt-4 grid gap-4 sm:grid-cols-2">
              <Field label="Nombre de la voz" htmlFor="voz-nombre">
                <Input
                  id="voz-nombre"
                  value={nombre}
                  onChange={(e) => setNombre(e.target.value)}
                  placeholder="p. ej. Voz de Ana"
                  maxLength={200}
                />
              </Field>
              <Field label="Descripción (opcional)" htmlFor="voz-descripcion">
                <Textarea
                  id="voz-descripcion"
                  value={descripcion}
                  onChange={(e) => setDescripcion(e.target.value)}
                  placeholder="Notas para reconocer esta voz más adelante."
                  className="min-h-[2.5rem]"
                />
              </Field>
            </div>

            <div className="mt-4 grid gap-4 sm:grid-cols-2">
              <Field
                label={`Muestras de audio de la voz (1 a ${MAX_MUESTRAS})`}
                htmlFor="voz-muestras"
                hint="Archivos de audio limpios, sin ruido de fondo, con la persona hablando."
              >
                <input
                  ref={muestrasInputRef}
                  id="voz-muestras"
                  type="file"
                  accept="audio/*"
                  multiple
                  onChange={(e) =>
                    setMuestras(Array.from(e.target.files ?? []).slice(0, MAX_MUESTRAS))
                  }
                  className="block w-full text-sm text-slate-600 file:mr-3 file:rounded-lg file:border-0 file:bg-slate-100 file:px-3 file:py-1.5 file:text-sm file:font-medium hover:file:bg-slate-200 dark:text-slate-300 dark:file:bg-slate-800 dark:hover:file:bg-slate-700"
                />
                {muestras.length > 0 && (
                  <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                    {muestras.length} archivo(s) seleccionado(s).
                  </p>
                )}
              </Field>
              <Field
                label="Grabación del consentimiento (obligatoria)"
                htmlFor="voz-consentimiento"
                hint="La persona diciendo, en su propia voz, que autoriza esta clonación."
              >
                <input
                  ref={consentimientoInputRef}
                  id="voz-consentimiento"
                  type="file"
                  accept="audio/*"
                  onChange={(e) => setConsentimiento(e.target.files?.[0] ?? null)}
                  className="block w-full text-sm text-slate-600 file:mr-3 file:rounded-lg file:border-0 file:bg-slate-100 file:px-3 file:py-1.5 file:text-sm file:font-medium hover:file:bg-slate-200 dark:text-slate-300 dark:file:bg-slate-800 dark:hover:file:bg-slate-700"
                />
                {consentimiento && (
                  <p className="mt-1 flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
                    <CheckIcon className="h-3.5 w-3.5" /> {consentimiento.name}
                  </p>
                )}
              </Field>
            </div>

            <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3 dark:border-amber-900 dark:bg-amber-950/30">
              <Checkbox
                label={ATTESTATION_TEXT}
                checked={attestation}
                onChange={(e) => setAttestation(e.target.checked)}
              />
            </div>

            {formError && (
              <div className="mt-3">
                <Alert variant="error">{formError}</Alert>
              </div>
            )}

            <div className="mt-4 flex justify-end">
              <Button onClick={handleCrearClon} disabled={!puedeEnviar} loading={creando}>
                <UploadIcon className="h-4 w-4" />
                Clonar voz
              </Button>
            </div>
          </CardBody>
        </Card>
      </div>

      {/* --- Tus clones ------------------------------------------------------ */}
      <Card>
        <CardHeader
          title="Tus clones"
          description="Registro de cada clonación, con evidencia del consentimiento. Revocar borra el clon técnico en ElevenLabs, pero el registro de consentimiento se conserva siempre."
        />
        <CardBody>
          {loading ? (
            <div className="flex justify-center py-8">
              <Spinner className="h-5 w-5 text-slate-400" />
            </div>
          ) : clones.length === 0 ? (
            <EmptyState
              title="Todavía no clonaste ninguna voz"
              description="Usa el formulario de arriba cuando tengas el consentimiento grabado de la persona."
            />
          ) : (
            <ul className="space-y-2">
              {clones.map((clon) => (
                <li
                  key={clon.id}
                  className="flex items-center justify-between gap-3 rounded-lg border border-slate-100 px-3 py-2.5 dark:border-slate-800"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <p className="truncate text-sm font-medium text-slate-700 dark:text-slate-200">
                        {clon.voice_name}
                      </p>
                      <ClonStatusBadge status={clon.status} />
                      {!clon.provider_voice_id && clon.status === "attested" && (
                        <Badge variant="warning">Pendiente de conectar ElevenLabs</Badge>
                      )}
                    </div>
                    <p className="text-xs text-slate-400">
                      Creado {formatDateTime(clon.created_at)}
                    </p>
                  </div>
                  {clon.status === "attested" && (
                    <Button
                      variant="danger"
                      size="sm"
                      onClick={() => void handleRevocar(clon)}
                      loading={revocandoId === clon.id}
                    >
                      <TrashIcon className="h-3.5 w-3.5" />
                      Revocar
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
