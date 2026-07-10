"use client";

/**
 * Una skill instalada (`/app/skills`, WP-V3-04): nombre + fuente + badge
 * activa/inactiva, toggle, «Ver contenido» que trae el `SKILL.md` completo
 * perezosamente (`GET /v1/skills/{id}` solo cuando se abre el `<details>` —
 * `GET /v1/skills` de la lista NUNCA trae `contenido`, ver
 * `edecan_api.routers.skills`) y desinstalar con confirmación inline (mismo
 * patrón que `/app/perfil-vivo`: `Alert` + dos botones, sin `window.confirm`).
 *
 * **Seguridad de terceros (WP-V5-04)**: badge de `trust_tier` ("indexada"/"sin
 * revisar") junto al de activa/inactiva; un chip por cada `capabilities`
 * declarada, en rojo (`danger`) las que están en `capabilities_peligrosas`
 * (el backend ya filtra ese subconjunto, ver `api-skills.ts`); y el bloque de
 * confirmación `acknowledgeMensaje` (mismo patrón visual que desinstalar:
 * `Alert` + dos botones) cuando `/app/skills/page.tsx` detecta que activar
 * esta skill exige `{"acknowledge": true}`.
 *
 * Este archivo vive en `components/skills/`, fuera de las rutas asignadas a
 * WP-V5-04 (`app/skills/`, `lib/api-skills.ts`) — igual que WP-V3-07 tocó
 * `api-skills.ts` "fuera de sus rutas asignadas" (ver el comentario al fondo
 * de ese archivo), el ajuste es quirúrgico y obligado: el badge de tier y los
 * chips de capacidades que pide el paquete de trabajo van junto al badge
 * activa/inactiva que YA vive acá (WP-V3-04) — no hay forma de cumplir ese
 * requisito sin tocar este componente. Documentado en vez de silenciado.
 */

import { useState } from "react";

import { TrashIcon } from "@/components/icons";
import { Alert, Badge, Button, Spinner, Switch } from "@/components/ui";
import { ApiError, getSkill, type SkillDetail, type SkillSummary } from "@/lib/api-skills";
import { formatDateTime } from "@/lib/format";

function describeError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "Ocurrió un error inesperado.";
}

function TrustTierBadge({ trustTier }: { trustTier: SkillSummary["trust_tier"] }) {
  if (trustTier === "indexada") {
    return <Badge variant="success">indexada</Badge>;
  }
  return <Badge variant="warning">sin revisar</Badge>;
}

function CapabilityChips({ skill }: { skill: SkillSummary }) {
  if (skill.capabilities.length === 0) return null;
  const peligrosas = new Set(skill.capabilities_peligrosas);
  return (
    <div className="mt-1.5 flex flex-wrap gap-1.5">
      {skill.capabilities.map((cap) => (
        <Badge key={cap} variant={peligrosas.has(cap) ? "danger" : "neutral"}>
          {cap}
        </Badge>
      ))}
    </div>
  );
}

export function InstalledSkillItem({
  skill,
  busy,
  onToggle,
  onDelete,
  acknowledgeMensaje,
  onConfirmEnable,
  onCancelAcknowledge,
}: {
  skill: SkillSummary;
  busy: boolean;
  onToggle: (enabled: boolean) => void;
  onDelete: () => void;
  acknowledgeMensaje: string | null;
  onConfirmEnable: () => void;
  onCancelAcknowledge: () => void;
}) {
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  async function handleToggleDetails(open: boolean) {
    if (!open || detail || loadingDetail) return;
    setLoadingDetail(true);
    setDetailError(null);
    try {
      setDetail(await getSkill(skill.id));
    } catch (err) {
      setDetailError(describeError(err));
    } finally {
      setLoadingDetail(false);
    }
  }

  return (
    <li className="rounded-xl border border-slate-100 px-4 py-3 dark:border-slate-800">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="truncate text-sm font-medium text-slate-700 dark:text-slate-200">
              {skill.nombre}
            </p>
            <Badge variant={skill.enabled ? "success" : "neutral"}>
              {skill.enabled ? "activa" : "inactiva"}
            </Badge>
            <TrustTierBadge trustTier={skill.trust_tier} />
          </div>
          <p className="mt-0.5 truncate font-mono text-xs text-slate-400">{skill.source}</p>
          {skill.descripcion && (
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{skill.descripcion}</p>
          )}
          <CapabilityChips skill={skill} />
          <p className="mt-1 text-xs text-slate-400">
            Instalada {formatDateTime(skill.created_at)}
            {skill.version ? ` · v${skill.version}` : ""}
          </p>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {busy && <Spinner className="h-4 w-4 text-slate-400" />}
          <Switch
            id={`skill-enabled-${skill.id}`}
            checked={skill.enabled}
            onChange={(enabled) => {
              if (!busy) onToggle(enabled);
            }}
            label=""
          />
        </div>
      </div>

      {acknowledgeMensaje && (
        <div className="mt-3 space-y-2">
          <Alert variant="error">{acknowledgeMensaje}</Alert>
          <div className="flex gap-2">
            <Button variant="danger" size="sm" loading={busy} onClick={onConfirmEnable}>
              Sí, activar de todos modos
            </Button>
            <Button
              variant="secondary"
              size="sm"
              disabled={busy}
              onClick={onCancelAcknowledge}
            >
              Cancelar
            </Button>
          </div>
        </div>
      )}

      <details className="mt-3" onToggle={(e) => void handleToggleDetails(e.currentTarget.open)}>
        <summary className="cursor-pointer text-xs font-medium text-brand-600 hover:underline dark:text-brand-400">
          Ver contenido (SKILL.md)
        </summary>
        <div className="mt-2">
          {loadingDetail ? (
            <div className="flex justify-center py-3">
              <Spinner className="h-4 w-4 text-slate-400" />
            </div>
          ) : detailError ? (
            <Alert variant="error">{detailError}</Alert>
          ) : detail ? (
            <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-3 font-mono text-xs text-slate-700 dark:bg-slate-950/60 dark:text-slate-300">
              {detail.contenido}
            </pre>
          ) : null}
        </div>
      </details>

      <div className="mt-3">
        {!confirmingDelete ? (
          <Button variant="danger" size="sm" onClick={() => setConfirmingDelete(true)}>
            <TrashIcon className="h-3.5 w-3.5" /> Desinstalar
          </Button>
        ) : (
          <div className="space-y-2">
            <Alert variant="error">¿Desinstalar «{skill.nombre}»? El agente ya no podrá usarla.</Alert>
            <div className="flex gap-2">
              <Button variant="danger" size="sm" loading={busy} onClick={onDelete}>
                Sí, desinstalar
              </Button>
              <Button
                variant="secondary"
                size="sm"
                disabled={busy}
                onClick={() => setConfirmingDelete(false)}
              >
                Cancelar
              </Button>
            </div>
          </div>
        )}
      </div>
    </li>
  );
}
