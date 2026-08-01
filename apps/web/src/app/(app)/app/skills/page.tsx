"use client";

/**
 * `/app/skills` — marketplace de "Agent Skills" (estándar abierto que indexa
 * skills.sh, `ARCHITECTURE.md` §12.a/§12.e, `DIRECCION_ACTUAL.md`; dueño
 * WP-V3-04). Sin flag de plan (`edecan_skills.tools` no declara
 * `requires_flags`, ver `docs/skills.md`): disponible para cualquier tenant
 * autenticado, sin el chequeo `useAuth().me?.flags?.[...]` que sí usan
 * `/app/misiones`/`/app/ordenes`.
 *
 * Compone los tres bloques que pide el paquete de trabajo:
 * - `SkillSearchPanel` — buscador del marketplace + "instalar directo" por
 *   `owner/repo`, con su propio estado de instalando/error (ver ese archivo).
 * - `InstalledSkillsList`/`InstalledSkillItem` — instaladas, con toggle
 *   activar/desactivar, desinstalar con confirmación inline, y "ver
 *   contenido" perezoso en un `<details>` monoespaciado.
 * - El aviso de seguridad fijo de abajo: las skills son instrucciones de
 *   terceros que el agente seguirá literalmente al activarlas
 *   (`usar_skill`) — revisarlas antes de confiar en ellas.
 *
 * **Flujo de `acknowledge` (WP-V5-04)**: `handleToggle` intenta activar sin
 * `acknowledge`; si `PUT /v1/skills/{id}` responde `400` (capacidades
 * peligrosas y/o hallazgos de inyección — `err.message` ya trae el detalle
 * exacto armado por el backend, `edecan_api.routers.skills._detalle_acknowledge`),
 * en vez de mostrarlo como un error genérico se guarda en `pendingAcknowledge`
 * para que `InstalledSkillItem` lo muestre como una confirmación inline (mismo
 * patrón visual que ya usa para desinstalar) — el switch vuelve a su posición
 * porque `skills` nunca se actualiza en ese camino. `handleConfirmEnable`
 * reintenta con `acknowledge: true` tras el clic explícito del usuario.
 */

import { useCallback, useEffect, useState } from "react";

import { InstalledSkillsList } from "@/components/skills/InstalledSkillsList";
import { SkillSearchPanel } from "@/components/skills/SkillSearchPanel";
import { Alert, Card, CardBody, CardHeader, PageHeader } from "@/components/ui";
import {
  ApiError,
  deleteSkill,
  listSkills,
  setSkillEnabled,
  type PendingAcknowledge,
  type SkillDetail,
  type SkillSummary,
} from "@/lib/api-skills";

function describeError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "Ocurrió un error inesperado.";
}

/** Encabeza la lista con la fila que trae `SkillDetail` (instalar/reinstalar),
 * reemplazando cualquier fila previa con el mismo `id` (reinstalar actualiza
 * el contenido pero no crea una fila duplicada, ver `edecan_skills.store`). */
function upsertByInstall(skills: SkillSummary[], detail: SkillDetail): SkillSummary[] {
  const summary: SkillSummary = {
    id: detail.id,
    nombre: detail.nombre,
    slug: detail.slug,
    source: detail.source,
    descripcion: detail.descripcion,
    version: detail.version,
    enabled: detail.enabled,
    trust_tier: detail.trust_tier,
    capabilities: detail.capabilities,
    capabilities_peligrosas: detail.capabilities_peligrosas,
    created_at: detail.created_at,
  };
  const yaEstaba = skills.some((s) => s.id === summary.id);
  if (yaEstaba) return skills.map((s) => (s.id === summary.id ? summary : s));
  return [summary, ...skills];
}

export default function SkillsPage() {
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [pendingAcknowledge, setPendingAcknowledge] = useState<PendingAcknowledge | null>(null);

  const loadSkills = useCallback(async () => {
    try {
      setSkills(await listSkills());
      setListError(null);
    } catch (err) {
      setListError(describeError(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSkills();
  }, [loadSkills]);

  function handleInstalled(skill: SkillDetail) {
    setActionError(null);
    setSkills((prev) => upsertByInstall(prev, skill));
  }

  async function handleToggle(skill: SkillSummary, enabled: boolean) {
    setBusyId(skill.id);
    setActionError(null);
    setPendingAcknowledge((prev) => (prev?.skillId === skill.id ? null : prev));
    try {
      await setSkillEnabled(skill.id, enabled);
      setSkills((prev) => prev.map((s) => (s.id === skill.id ? { ...s, enabled } : s)));
    } catch (err) {
      if (enabled && err instanceof ApiError && err.status === 400) {
        // Activar exige `acknowledge` (capacidades peligrosas y/o hallazgos de inyección,
        // ver docstring del componente) — se muestra como confirmación, no como error.
        setPendingAcknowledge({ skillId: skill.id, mensaje: err.message });
      } else {
        setActionError(describeError(err));
      }
    } finally {
      setBusyId(null);
    }
  }

  async function handleConfirmEnable(skill: SkillSummary) {
    setBusyId(skill.id);
    setActionError(null);
    try {
      await setSkillEnabled(skill.id, true, true);
      setSkills((prev) => prev.map((s) => (s.id === skill.id ? { ...s, enabled: true } : s)));
      setPendingAcknowledge(null);
    } catch (err) {
      setActionError(describeError(err));
    } finally {
      setBusyId(null);
    }
  }

  function handleCancelAcknowledge() {
    setPendingAcknowledge(null);
  }

  async function handleDelete(skill: SkillSummary) {
    setBusyId(skill.id);
    setActionError(null);
    try {
      await deleteSkill(skill.id);
      setSkills((prev) => prev.filter((s) => s.id !== skill.id));
    } catch (err) {
      setActionError(describeError(err));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div>
      <PageHeader
        title="Skills"
        description="Capacidades instalables para tu asistente, del estándar abierto de 'Agent Skills' que indexa skills.sh."
      />

      <div className="mb-6">
        <Alert variant="info">
          Las skills son instrucciones escritas por terceros, no por Edecán: revísalas (con
          «Ver contenido» en cada skill instalada) antes de activarlas. El agente las sigue
          como una guía del usuario — nunca anulan las reglas de seguridad del sistema, y
          Edecán jamás ejecuta scripts ni binarios incluidos en una skill, solo lee su{" "}
          <span className="font-mono">SKILL.md</span> como texto.
        </Alert>
      </div>

      <div className="space-y-6">
        <Card>
          <CardHeader
            title="Buscar en el marketplace"
            description="Por palabra clave, o instala directo con 'owner/repo' si ya sabes cuál quieres."
          />
          <CardBody>
            <SkillSearchPanel onInstalled={handleInstalled} />
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Tus skills instaladas" />
          <CardBody>
            {listError && (
              <div className="mb-3">
                <Alert variant="error">{listError}</Alert>
              </div>
            )}
            {actionError && (
              <div className="mb-3">
                <Alert variant="error">{actionError}</Alert>
              </div>
            )}
            <InstalledSkillsList
              skills={skills}
              loading={loading}
              busyId={busyId}
              onToggle={handleToggle}
              onDelete={handleDelete}
              pendingAcknowledge={pendingAcknowledge}
              onConfirmEnable={handleConfirmEnable}
              onCancelAcknowledge={handleCancelAcknowledge}
            />
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
