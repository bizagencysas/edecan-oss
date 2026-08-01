"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { MissionResumen } from "@/components/misiones/MissionResumen";
import { MissionStatusBadge } from "@/components/misiones/MissionStatusBadge";
import { PlantillasMisiones } from "@/components/misiones/PlantillasMisiones";
import { StepTimeline } from "@/components/misiones/StepTimeline";
import { PlusIcon } from "@/components/icons";
import {
  Alert,
  Button,
  Card,
  CardBody,
  CardHeader,
  EmptyState,
  PageHeader,
  Spinner,
  Textarea,
} from "@/components/ui";
import {
  ACTIVE_MISSION_STATUSES,
  FLAG_AGENTS_MISSIONS,
  cancelMission,
  confirmMission,
  createMission,
  getMissionDetalle,
  listMissions,
  type Mission,
  type MissionDetalle,
} from "@/lib/api-misiones";
import { useAuth } from "@/lib/auth-context";
import { formatDateTime } from "@/lib/format";

const POLL_INTERVAL_MS = 2000;

function isActive(status: Mission["status"]): boolean {
  return (ACTIVE_MISSION_STATUSES as readonly string[]).includes(status);
}

/** Misiones (ROADMAP_V2.md §7.4, §7.6, §7.9): Orchestrator multi-agente. */
export default function MisionesPage() {
  const { me } = useAuth();
  const canMissions = Boolean(me?.flags?.[FLAG_AGENTS_MISSIONS]);

  const [missions, setMissions] = useState<Mission[]>([]);
  const [missionsLoading, setMissionsLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);

  const [objetivo, setObjetivo] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const objetivoRef = useRef<HTMLTextAreaElement | null>(null);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<MissionDetalle | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState(false);

  const loadMissions = useCallback(async () => {
    try {
      const items = await listMissions();
      setMissions(items);
      setListError(null);
    } catch (err) {
      setListError(err instanceof Error ? err.message : "No se pudieron cargar las misiones.");
    } finally {
      setMissionsLoading(false);
    }
  }, []);

  const loadDetail = useCallback(async (id: string) => {
    setDetailLoading(true);
    setDetailError(null);
    try {
      setDetail(await getMissionDetalle(id));
    } catch (err) {
      setDetailError(err instanceof Error ? err.message : "No se pudo cargar la misión.");
    } finally {
      setDetailLoading(false);
    }
  }, []);

  /** Plantillas (WP-V6-10, `plantillas.ts`): deja el objetivo armado listo en
   * el textarea de "Nueva misión" para que el usuario lo revise/edite antes
   * de crear la misión — nunca crea la misión directo, mismo criterio de
   * "revisar antes de enviar" que el resto del producto. */
  function handleUseTemplate(objetivoGenerado: string) {
    setObjetivo(objetivoGenerado);
    setCreateError(null);
    requestAnimationFrame(() => {
      objetivoRef.current?.focus();
      objetivoRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  }

  useEffect(() => {
    if (!canMissions) {
      setMissionsLoading(false);
      return;
    }
    void loadMissions();
  }, [canMissions, loadMissions]);

  useEffect(() => {
    if (selectedId) void loadDetail(selectedId);
    else setDetail(null);
  }, [selectedId, loadDetail]);

  // Polling cada 2s: la lista mientras alguna misión siga activa, el detalle
  // mientras la misión seleccionada siga activa (ROADMAP_V2.md §7.9: el
  // Orchestrator corre en el worker, de forma asíncrona).
  const missionsRef = useRef(missions);
  missionsRef.current = missions;
  useEffect(() => {
    if (!canMissions) return;
    const interval = setInterval(() => {
      if (missionsRef.current.some((m) => isActive(m.status))) void loadMissions();
    }, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [canMissions, loadMissions]);

  const detailStatusRef = useRef<Mission["status"] | null>(null);
  detailStatusRef.current = detail?.mission.status ?? null;
  useEffect(() => {
    if (!selectedId) return;
    const interval = setInterval(() => {
      const status = detailStatusRef.current;
      if (status && isActive(status)) void loadDetail(selectedId);
    }, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [selectedId, loadDetail]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!objetivo.trim()) return;
    setCreating(true);
    setCreateError(null);
    try {
      const mission = await createMission(objetivo.trim());
      setObjetivo("");
      await loadMissions();
      setSelectedId(mission.id);
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : "No se pudo crear la misión.");
    } finally {
      setCreating(false);
    }
  }

  async function handleApprove() {
    if (!selectedId) return;
    setActionBusy(true);
    try {
      await confirmMission(selectedId, true);
      await Promise.all([loadDetail(selectedId), loadMissions()]);
    } catch (err) {
      setDetailError(err instanceof Error ? err.message : "No se pudo aprobar el paso.");
    } finally {
      setActionBusy(false);
    }
  }

  async function handleReject() {
    if (!selectedId) return;
    setActionBusy(true);
    try {
      await confirmMission(selectedId, false);
      await Promise.all([loadDetail(selectedId), loadMissions()]);
    } catch (err) {
      setDetailError(err instanceof Error ? err.message : "No se pudo rechazar el paso.");
    } finally {
      setActionBusy(false);
    }
  }

  async function handleCancel() {
    if (!selectedId) return;
    setActionBusy(true);
    try {
      await cancelMission(selectedId);
      await Promise.all([loadDetail(selectedId), loadMissions()]);
    } catch (err) {
      setDetailError(err instanceof Error ? err.message : "No se pudo cancelar la misión.");
    } finally {
      setActionBusy(false);
    }
  }

  if (!canMissions) {
    return (
      <div>
        <PageHeader
          title="Misiones"
          description="Objetivos que un orquestador planifica y ejecuta delegando en sub-agentes especializados."
        />
        <Alert variant="info">Las misiones no están disponibles en tu plan actual.</Alert>
      </div>
    );
  }

  const mission = detail?.mission;
  const canCancel = mission ? !["done", "error", "cancelled"].includes(mission.status) : false;

  return (
    <div>
      <PageHeader
        title="Misiones"
        description="Objetivos que un orquestador planifica y ejecuta delegando en sub-agentes especializados (investigación, análisis de datos, contenido)."
      />

      <div className="mb-6">
        <Card>
          <CardHeader
            title="Plantillas"
            description="Elige una, completa los datos y el objetivo queda listo para revisar abajo."
          />
          <CardBody>
            <PlantillasMisiones onUseTemplate={handleUseTemplate} />
          </CardBody>
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[380px_1fr]">
        <div className="space-y-6">
          <Card>
            <CardHeader title="Nueva misión" />
            <CardBody>
              {createError && (
                <div className="mb-3">
                  <Alert variant="error">{createError}</Alert>
                </div>
              )}
              <form onSubmit={handleCreate} className="space-y-3">
                <Textarea
                  ref={objetivoRef}
                  value={objetivo}
                  onChange={(e) => setObjetivo(e.target.value)}
                  placeholder="Ej: Investiga a los 3 principales competidores de mi producto y resume sus precios."
                  rows={3}
                />
                <Button type="submit" loading={creating} disabled={!objetivo.trim()} className="w-full">
                  <PlusIcon className="h-4 w-4" /> Crear misión
                </Button>
              </form>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Tus misiones" />
            <CardBody>
              {listError && (
                <div className="mb-3">
                  <Alert variant="error">{listError}</Alert>
                </div>
              )}
              {missionsLoading ? (
                <div className="flex justify-center py-8">
                  <Spinner className="h-5 w-5 text-slate-400" />
                </div>
              ) : missions.length === 0 ? (
                <EmptyState
                  title="Sin misiones todavía"
                  description="Crea una arriba, o pídeselo a tu asistente en el chat con delegar_mision."
                />
              ) : (
                <ul className="space-y-1.5">
                  {missions.map((m) => (
                    <li key={m.id}>
                      <button
                        type="button"
                        onClick={() => setSelectedId(m.id)}
                        className={`w-full rounded-lg border px-3 py-2.5 text-left transition-colors ${
                          m.id === selectedId
                            ? "border-brand-300 bg-brand-50 dark:border-brand-800 dark:bg-brand-950/40"
                            : "border-slate-100 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/60"
                        }`}
                      >
                        <p className="truncate text-sm font-medium text-slate-700 dark:text-slate-200">
                          {m.objetivo}
                        </p>
                        <div className="mt-1 flex items-center justify-between gap-2">
                          <span className="text-xs text-slate-400">{formatDateTime(m.created_at)}</span>
                          <MissionStatusBadge status={m.status} />
                        </div>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </CardBody>
          </Card>
        </div>

        <Card>
          {!selectedId || !mission ? (
            <CardBody>
              {detailLoading ? (
                <div className="flex justify-center py-8">
                  <Spinner className="h-5 w-5 text-slate-400" />
                </div>
              ) : (
                <EmptyState
                  title="Elige una misión"
                  description="Selecciona una misión de la lista para ver su avance paso a paso."
                />
              )}
            </CardBody>
          ) : (
            <>
              <CardHeader
                title={mission.objetivo}
                description={`Creada ${formatDateTime(mission.created_at)}`}
                actions={
                  <>
                    <MissionStatusBadge status={mission.status} />
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => selectedId && loadDetail(selectedId)}
                      loading={detailLoading}
                    >
                      Refrescar
                    </Button>
                    {canCancel && (
                      <Button size="sm" variant="secondary" onClick={handleCancel} loading={actionBusy}>
                        Cancelar
                      </Button>
                    )}
                  </>
                }
              />
              <CardBody>
                {detailError && (
                  <div className="mb-4">
                    <Alert variant="error">{detailError}</Alert>
                  </div>
                )}
                {mission.error && (
                  <div className="mb-4">
                    <Alert variant="error">{mission.error}</Alert>
                  </div>
                )}
                {mission.resultado && (
                  <div className="mb-5 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-200">
                    <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-emerald-700 dark:text-emerald-400">
                      Resultado
                    </p>
                    <p className="whitespace-pre-wrap">{mission.resultado}</p>
                  </div>
                )}
                {detail && <MissionResumen mission={mission} agregados={detail.agregados} />}
                <StepTimeline
                  steps={detail?.steps ?? []}
                  onApprove={handleApprove}
                  onReject={handleReject}
                  busy={actionBusy}
                />
              </CardBody>
            </>
          )}
        </Card>
      </div>
    </div>
  );
}
