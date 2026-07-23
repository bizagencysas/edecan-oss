"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { CheckIcon, SendIcon, SparklesIcon, ZapIcon } from "@/components/icons";
import {
  Alert,
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  Checkbox,
  Field,
  PageHeader,
  Select,
  Textarea,
} from "@/components/ui";
import {
  createSocialContent,
  downloadFile,
  listFiles,
  listConnectors,
  publishLinkedInContent,
  type SocialContentResult,
} from "@/lib/api";
import {
  createAutomation,
  listAutomations,
  updateAutomation,
  type Automation,
} from "@/lib/api-automatizaciones";
import type { FileOut } from "@/lib/types";

const PLAN_NAME = "Plan de contenido de LinkedIn";
const MAX_VISIBLE_DRAFTS = 8;

interface SocialDraftManifest {
  schema_version: number;
  platform: string;
  topic: string;
  copy: string;
  visual?: {
    alt_text?: string;
  };
  sources?: Array<{ title: string; url: string; snippet?: string }>;
}

interface RecentDraft {
  manifestFile: FileOut;
  imageFile: FileOut | null;
  manifest: SocialDraftManifest;
}

function scheduleRule(frequency: 2 | 3): string {
  const localHours = frequency === 2 ? [9, 16] : [9, 14, 19];
  const offsetMinutes = new Date().getTimezoneOffset();
  const utcMinutes = localHours.map((hour) => (hour * 60 + offsetMinutes + 24 * 60) % (24 * 60));
  const hours = [...new Set(utcMinutes.map((value) => Math.floor(value / 60)))].join(",");
  const minutes = [...new Set(utcMinutes.map((value) => value % 60))].join(",");
  return `FREQ=DAILY;BYHOUR=${hours};BYMINUTE=${minutes};BYSECOND=0`;
}

function planInstruction(
  frequency: 2 | 3,
  themes: string,
  audience: string,
  brandContext: string,
  avoidTopics: string,
): string {
  const focus = themes.trim() || "derívalo del perfil vivo, los proyectos y la memoria";
  const targetAudience = audience.trim() || "derívala del perfil vivo sin inventarla";
  const brand = brandContext.trim() || "usa la identidad personal configurada en Edecán";
  const excluded = avoidTopics.trim() || "ningún tema adicional indicado";
  return [
    "Prepara un nuevo borrador privado de LinkedIn y una imagen original.",
    "Esta estrategia pertenece únicamente al usuario y a la marca de esta instalación. No reutilices un mapa editorial genérico ni el de otra persona.",
    `Temas o foco declarados por esta persona: ${focus}.`,
    `Audiencia declarada o inferida de forma conservadora: ${targetAudience}.`,
    `Marca, negocio o contexto que debe respetarse: ${brand}.`,
    `Temas que no deben tocarse: ${excluded}.`,
    "Construye y mantiene un mapa editorial propio a partir del perfil vivo, la memoria autorizada, sus proyectos, su audiencia y estas preferencias.",
    "Elige en cada ejecución el territorio, ángulo y formato que mejor sirvan a esa estrategia personal y al momento actual.",
    "Consulta memoria, archivos y borradores recientes antes de escribir. No repitas tema, tesis, apertura, estructura ni concepto visual de las ocho piezas anteriores.",
    "Si usas una noticia, cifra, producto, modelo, empresa o dato que pueda haber cambiado, investígalo primero en fuentes actuales y conserva los enlaces para la revisión.",
    "Separa el trabajo: primero investigación y selección de ángulo; después escritura; al final revisión factual, de naturalidad y de repetición.",
    "No inventes experiencias en primera persona, clientes, resultados, citas, fechas ni cifras.",
    "El texto debe tener una sola idea central, aportar algo concreto y sonar natural, no como un boletín ni como documentación.",
    "La imagen debe elegirse por ajuste semántico al post. Puede ser una escena, ilustración, comparación o composición editorial; evita plantillas repetidas y texto ilegible.",
    "Genera copy, manifiesto e imagen mediante crear_contenido_social y deja los artefactos visibles para la persona.",
    "No publiques automáticamente, no programes la publicación y no interpretes este plan como autorización. Entrega el borrador y avisa para que la persona lo revise.",
    `Este plan produce ${frequency} borradores al día en horarios separados.`,
  ].join(" ");
}

function imageArtifact(result: SocialContentResult | null) {
  return result?.artifacts.find((artifact) => artifact.mime?.startsWith("image/")) ?? null;
}

function manifestBaseName(filename: string): string {
  return filename.replace(/\.json$/i, "");
}

async function loadRecentDrafts(): Promise<RecentDraft[]> {
  const files = await listFiles();
  const manifests = files
    .filter(
      (file) =>
        file.status !== "error" &&
        file.mime === "application/json" &&
        /^linkedin-.+\.json$/i.test(file.filename),
    )
    .slice(0, MAX_VISIBLE_DRAFTS);

  const parsed = await Promise.allSettled(
    manifests.map(async (manifestFile): Promise<RecentDraft> => {
      const blob = await downloadFile(manifestFile.id);
      const manifest = JSON.parse(await blob.text()) as SocialDraftManifest;
      if (
        manifest.schema_version !== 1 ||
        manifest.platform !== "linkedin" ||
        typeof manifest.topic !== "string" ||
        typeof manifest.copy !== "string"
      ) {
        throw new Error("Manifiesto social incompatible.");
      }
      const baseName = manifestBaseName(manifestFile.filename);
      const imageFile =
        files.find(
          (file) =>
            file.status !== "error" &&
            file.mime.startsWith("image/") &&
            file.filename.replace(/\.[^.]+$/, "") === baseName,
        ) ?? null;
      return { manifestFile, imageFile, manifest };
    }),
  );

  return parsed.flatMap((item) => (item.status === "fulfilled" ? [item.value] : []));
}

export function SocialContentStudio() {
  const [platform, setPlatform] = useState<"linkedin" | "x">("linkedin");
  const [topic, setTopic] = useState("");
  const [objective, setObjective] = useState("Enseñar algo útil");
  const [tone, setTone] = useState("Claro, humano y con criterio");
  const [withImage, setWithImage] = useState(true);
  const [result, setResult] = useState<SocialContentResult | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [linkedinConnected, setLinkedinConnected] = useState(false);
  const [frequency, setFrequency] = useState<2 | 3>(2);
  const [themes, setThemes] = useState("");
  const [audience, setAudience] = useState("");
  const [brandContext, setBrandContext] = useState("");
  const [avoidTopics, setAvoidTopics] = useState("");
  const [plan, setPlan] = useState<Automation | null>(null);
  const [savingPlan, setSavingPlan] = useState(false);
  const [automationsAvailable, setAutomationsAvailable] = useState(true);
  const [recentDrafts, setRecentDrafts] = useState<RecentDraft[]>([]);
  const [loadingDrafts, setLoadingDrafts] = useState(true);
  const objectUrlRef = useRef<string | null>(null);

  const visual = useMemo(() => imageArtifact(result), [result]);

  const refreshConnections = useCallback(async () => {
    try {
      const connectors = await listConnectors();
      setLinkedinConnected(
        Boolean(connectors.find((item) => item.key === "linkedin")?.accounts.length),
      );
    } catch {
      setLinkedinConnected(false);
    }
  }, []);

  const refreshPlan = useCallback(async () => {
    try {
      const automations = await listAutomations();
      setPlan(automations.find((item) => item.nombre === PLAN_NAME) ?? null);
      setAutomationsAvailable(true);
    } catch {
      setAutomationsAvailable(false);
    }
  }, []);

  const refreshDrafts = useCallback(async () => {
    setLoadingDrafts(true);
    try {
      setRecentDrafts(await loadRecentDrafts());
    } catch {
      setRecentDrafts([]);
    } finally {
      setLoadingDrafts(false);
    }
  }, []);

  useEffect(() => {
    void Promise.allSettled([refreshConnections(), refreshPlan(), refreshDrafts()]);
    return () => {
      if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
    };
  }, [refreshConnections, refreshDrafts, refreshPlan]);

  useEffect(() => {
    let cancelled = false;
    if (!visual) {
      if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
      objectUrlRef.current = null;
      setPreviewUrl(null);
      return;
    }
    void downloadFile(visual.file_id)
      .then((blob) => {
        if (cancelled) return;
        if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
        objectUrlRef.current = URL.createObjectURL(blob);
        setPreviewUrl(objectUrlRef.current);
      })
      .catch(() => setPreviewUrl(null));
    return () => {
      cancelled = true;
    };
  }, [visual]);

  async function handleCreate(event: React.FormEvent) {
    event.preventDefault();
    if (!topic.trim()) return;
    setCreating(true);
    setError(null);
    setSuccess(null);
    try {
      const created = await createSocialContent({
        platform,
        topic: topic.trim(),
        objective: objective.trim(),
        tone: tone.trim(),
        with_image: withImage,
      });
      setResult(created);
      setSuccess(
        created.visual_warning ||
          "Borrador listo. Puedes editarlo antes de copiar, descargar o publicar.",
      );
      await refreshDrafts();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No pude crear el contenido.");
    } finally {
      setCreating(false);
    }
  }

  async function handleCopy() {
    if (!result) return;
    await navigator.clipboard.writeText(result.copy);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1_800);
  }

  async function handleDownload() {
    if (!visual) return;
    const blob = await downloadFile(visual.file_id);
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = visual.filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
  }

  async function handlePublish() {
    if (!result || platform !== "linkedin") return;
    if (!linkedinConnected) {
      setError("Conecta tu cuenta de LinkedIn antes de publicar.");
      return;
    }
    if (!window.confirm("¿Publicar este texto y su imagen ahora en tu perfil de LinkedIn?")) return;
    setPublishing(true);
    setError(null);
    setSuccess(null);
    try {
      await publishLinkedInContent({
        text: result.copy,
        image_file_id: visual?.file_id,
        alt_text: result.alt_text,
        confirmed: true,
      });
      setSuccess("Publicado en LinkedIn.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No pude publicar en LinkedIn.");
    } finally {
      setPublishing(false);
    }
  }

  async function handleSavePlan() {
    setSavingPlan(true);
    setError(null);
    setSuccess(null);
    const input = {
      nombre: PLAN_NAME,
      descripcion: `${frequency} borradores diarios con imagen, listos para revisión.`,
      trigger: { kind: "schedule" as const, rrule: scheduleRule(frequency) },
      accion: {
        instruccion: planInstruction(
          frequency,
          themes,
          audience,
          brandContext,
          avoidTopics,
        ),
      },
      enabled: true,
    };
    try {
      const saved = plan
        ? await updateAutomation(plan.id, input)
        : await createAutomation(input);
      setPlan(saved);
      setSuccess(`Plan activo: ${frequency} borradores diarios con imagen.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No pude guardar el plan de contenido.");
    } finally {
      setSavingPlan(false);
    }
  }

  async function handleTogglePlan() {
    if (!plan) return;
    setSavingPlan(true);
    try {
      const updated = await updateAutomation(plan.id, { enabled: !plan.enabled });
      setPlan(updated);
      setSuccess(updated.enabled ? "Plan reactivado." : "Plan pausado.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No pude cambiar el plan.");
    } finally {
      setSavingPlan(false);
    }
  }

  function restoreDraft(draft: RecentDraft) {
    const artifacts = [
      {
        file_id: draft.manifestFile.id,
        filename: draft.manifestFile.filename,
        mime: draft.manifestFile.mime,
      },
    ];
    if (draft.imageFile) {
      artifacts.push({
        file_id: draft.imageFile.id,
        filename: draft.imageFile.filename,
        mime: draft.imageFile.mime,
      });
    }
    setPlatform("linkedin");
    setTopic(draft.manifest.topic);
    setResult({
      status: "ready",
      platform: "linkedin",
      copy: draft.manifest.copy,
      parts: [draft.manifest.copy],
      alt_text: draft.manifest.visual?.alt_text ?? "",
      offline_visual: false,
      visual_warning: "",
      sources: (draft.manifest.sources ?? []).map((source) => ({
        title: source.title,
        url: source.url,
        snippet: source.snippet ?? "",
      })),
      artifacts,
      requires_human_confirmation: true,
    });
    setSuccess("Borrador recuperado. Puedes editarlo, copiarlo o publicarlo.");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  return (
    <div className="space-y-5">
      <PageHeader
        title="Contenido"
        description="Crea posts y visuales, revísalos aquí y publica con tu propia cuenta."
        actions={
          <Badge variant={linkedinConnected ? "success" : "neutral"}>
            {linkedinConnected ? "LinkedIn conectado" : "LinkedIn sin conectar"}
          </Badge>
        }
      />

      {error && <Alert variant="error">{error}</Alert>}
      {success && <Alert variant="success">{success}</Alert>}

      <div className="grid gap-5 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <Card>
          <CardHeader
            title="Crear una pieza"
            description="Funciona con Codex, Claude, Ollama o cualquier modelo conectado."
          />
          <CardBody>
            <form onSubmit={handleCreate} className="space-y-4">
              <Field label="Red" htmlFor="content-platform">
                <Select
                  id="content-platform"
                  value={platform}
                  onChange={(event) => setPlatform(event.target.value as "linkedin" | "x")}
                >
                  <option value="linkedin">LinkedIn</option>
                  <option value="x">X</option>
                </Select>
              </Field>
              <Field label="¿Sobre qué quieres hablar?" htmlFor="content-topic">
                <Textarea
                  id="content-topic"
                  value={topic}
                  onChange={(event) => setTopic(event.target.value)}
                  placeholder="Ejemplo: lo que aprendí construyendo un producto con IA para Latinoamérica"
                />
              </Field>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Objetivo" htmlFor="content-objective">
                  <Select
                    id="content-objective"
                    value={objective}
                    onChange={(event) => setObjective(event.target.value)}
                  >
                    <option>Enseñar algo útil</option>
                    <option>Construir autoridad</option>
                    <option>Contar una historia</option>
                    <option>Presentar un producto</option>
                    <option>Generar conversación</option>
                  </Select>
                </Field>
                <Field label="Tono" htmlFor="content-tone">
                  <Select id="content-tone" value={tone} onChange={(event) => setTone(event.target.value)}>
                    <option>Claro, humano y con criterio</option>
                    <option>Profesional y directo</option>
                    <option>Cercano y personal</option>
                    <option>Visionario y ambicioso</option>
                    <option>Educativo y sencillo</option>
                  </Select>
                </Field>
              </div>
              <Checkbox
                checked={withImage}
                onChange={(event) => setWithImage(event.target.checked)}
                label="Crear también una imagen original"
              />
              <Button type="submit" loading={creating} className="w-full">
                <SparklesIcon className="h-4 w-4" />
                Crear post
              </Button>
            </form>
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="Vista previa"
            description={result ? "Edita el texto antes de usarlo." : "Tu próxima pieza aparecerá aquí."}
          />
          <CardBody className="space-y-4">
            {result ? (
              <>
                {previewUrl && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={previewUrl}
                    alt={result.alt_text || "Visual del post"}
                    className="max-h-[28rem] w-full rounded-xl border border-slate-200 object-contain dark:border-slate-800"
                  />
                )}
                <Textarea
                  value={result.copy}
                  onChange={(event) => setResult({ ...result, copy: event.target.value })}
                  className="min-h-[14rem]"
                  aria-label="Texto del post"
                />
                {result.sources.length > 0 && (
                  <div className="rounded-xl border border-slate-200 p-4 dark:border-slate-800">
                    <p className="mb-2 text-sm font-semibold text-slate-900 dark:text-slate-100">
                      Fuentes verificadas
                    </p>
                    <div className="space-y-2">
                      {result.sources.map((source) => (
                        <a
                          key={source.url}
                          href={source.url}
                          target="_blank"
                          rel="noreferrer"
                          className="block text-sm text-brand-600 hover:underline dark:text-brand-400"
                        >
                          {source.title || source.url}
                        </a>
                      ))}
                    </div>
                  </div>
                )}
                <div className="flex flex-wrap gap-2">
                  <Button type="button" variant="secondary" onClick={handleCopy}>
                    <CheckIcon className="h-4 w-4" />
                    {copied ? "Copiado" : "Copiar texto"}
                  </Button>
                  {visual && (
                    <Button type="button" variant="secondary" onClick={handleDownload}>
                      Descargar imagen
                    </Button>
                  )}
                  {platform === "linkedin" && (
                    <Button type="button" onClick={handlePublish} loading={publishing}>
                      <SendIcon className="h-4 w-4" />
                      Publicar en LinkedIn
                    </Button>
                  )}
                </div>
              </>
            ) : (
              <div className="flex min-h-[26rem] items-center justify-center rounded-xl border border-dashed border-slate-300 p-8 text-center text-sm text-slate-500 dark:border-slate-700 dark:text-slate-400">
                Escribe la idea como se la dirías a una persona. Edecán preparará el texto, la
                imagen y los archivos.
              </div>
            )}
          </CardBody>
        </Card>
      </div>

      <Card>
        <CardHeader
          title={
            <span className="flex items-center gap-2">
              <ZapIcon className="h-4 w-4 text-brand-600" />
              Plan diario de LinkedIn
            </span>
          }
          description="Edecán prepara borradores distintos con imagen y te avisa. Tú decides qué publicar."
          actions={
            plan ? <Badge variant={plan.enabled ? "success" : "warning"}>{plan.enabled ? "Activo" : "Pausado"}</Badge> : null
          }
        />
        <CardBody className="space-y-4">
          {automationsAvailable ? (
            <>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Borradores por día" htmlFor="linkedin-frequency">
                  <Select
                    id="linkedin-frequency"
                    value={frequency}
                    onChange={(event) => setFrequency(Number(event.target.value) as 2 | 3)}
                  >
                    <option value={2}>2 al día</option>
                    <option value={3}>3 al día</option>
                  </Select>
                </Field>
                <Field
                  label="Temas y enfoque"
                  htmlFor="linkedin-themes"
                  hint="Edecán usa también tu perfil y memoria para evitar contenido genérico."
                >
                  <Textarea
                    id="linkedin-themes"
                    value={themes}
                    onChange={(event) => setThemes(event.target.value)}
                    placeholder="Mis empresas, producto, IA, aprendizajes de fundador, casos reales..."
                    className="min-h-[5rem]"
                  />
                </Field>
                <Field
                  label="Audiencia"
                  htmlFor="linkedin-audience"
                  hint="A quién quieres ayudar, convencer o atraer."
                >
                  <Textarea
                    id="linkedin-audience"
                    value={audience}
                    onChange={(event) => setAudience(event.target.value)}
                    placeholder="Fundadores de fintech, clientes locales, líderes de producto..."
                    className="min-h-[5rem]"
                  />
                </Field>
                <Field
                  label="Marca o negocio"
                  htmlFor="linkedin-brand-context"
                  hint="Edecán adapta la voz y el contexto a esta identidad."
                >
                  <Textarea
                    id="linkedin-brand-context"
                    value={brandContext}
                    onChange={(event) => setBrandContext(event.target.value)}
                    placeholder="Marca personal, nombre de empresa, propuesta, país, estilo..."
                    className="min-h-[5rem]"
                  />
                </Field>
                <Field
                  label="Temas que debe evitar"
                  htmlFor="linkedin-avoid-topics"
                  hint="Opcional. No se comparten con otras personas."
                >
                  <Textarea
                    id="linkedin-avoid-topics"
                    value={avoidTopics}
                    onChange={(event) => setAvoidTopics(event.target.value)}
                    placeholder="Política, información confidencial, clientes sin autorización..."
                    className="min-h-[5rem]"
                  />
                </Field>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button type="button" onClick={handleSavePlan} loading={savingPlan}>
                  {plan ? "Guardar cambios" : "Activar plan diario"}
                </Button>
                {plan && (
                  <Button type="button" variant="secondary" onClick={handleTogglePlan} loading={savingPlan}>
                    {plan.enabled ? "Pausar" : "Reactivar"}
                  </Button>
                )}
                <Link
                  href="/app/automatizaciones"
                  className="inline-flex items-center rounded-lg px-3.5 py-2 text-sm font-medium text-brand-600 hover:bg-brand-50 dark:text-brand-400 dark:hover:bg-brand-950/40"
                >
                  Ver actividad del plan
                </Link>
              </div>
            </>
          ) : (
            <Alert variant="info">
              Las rutinas no están habilitadas en esta instalación. Aún puedes crear y publicar
              piezas manualmente desde esta pantalla.
            </Alert>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Borradores recientes"
          description="El plan diario y tus creaciones manuales quedan aquí para revisar, recuperar y publicar cuando tú decidas."
          actions={
            <Button type="button" variant="secondary" onClick={refreshDrafts} loading={loadingDrafts}>
              Actualizar
            </Button>
          }
        />
        <CardBody>
          {loadingDrafts ? (
            <p className="text-sm text-slate-500 dark:text-slate-400">
              Buscando tus borradores...
            </p>
          ) : recentDrafts.length ? (
            <div className="grid gap-3 md:grid-cols-2">
              {recentDrafts.map((draft) => (
                <button
                  key={draft.manifestFile.id}
                  type="button"
                  onClick={() => restoreDraft(draft)}
                  className="rounded-xl border border-slate-200 p-4 text-left transition hover:border-brand-300 hover:bg-brand-50/50 dark:border-slate-800 dark:hover:border-brand-800 dark:hover:bg-brand-950/20"
                >
                  <span className="block font-medium text-slate-950 dark:text-white">
                    {draft.manifest.topic}
                  </span>
                  <span className="mt-1 block line-clamp-2 text-sm text-slate-600 dark:text-slate-400">
                    {draft.manifest.copy}
                  </span>
                  <span className="mt-3 block text-xs text-slate-400">
                    {new Date(draft.manifestFile.created_at).toLocaleString()}
                    {draft.imageFile ? " · Con imagen" : ""}
                  </span>
                </button>
              ))}
            </div>
          ) : (
            <p className="text-sm text-slate-500 dark:text-slate-400">
              Todavía no hay borradores de LinkedIn. Crea el primero arriba o activa el plan
              diario.
            </p>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
