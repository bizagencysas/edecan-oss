"use client";

/**
 * Borradores de campaña (`ad_drafts`) — listar, confirmar (con el guardrail
 * de dinero) y cancelar (`ARCHITECTURE.md` §13.b, `docs/ads.md`).
 *
 * Sin ningún formulario para CREAR un borrador acá a propósito, mismo
 * criterio que `/app/ordenes` (`ARCHITECTURE.md` §10.7): un borrador solo
 * nace desde el chat (`ads_preparar_campana`, tool `dangerous=True`) — esta
 * página únicamente lista lo que ya existe y deja confirmar/cancelar, con un
 * SEGUNDO gate explícito (el modal de abajo) antes de tocar
 * `POST .../confirmar`. Confirmar empuja la campaña a Meta **SIEMPRE en
 * pausa** — nunca activa gasto por su cuenta.
 */

import { useState } from "react";

import { Alert, Badge, Button, Card, CardBody, CardHeader, EmptyState, Spinner } from "@/components/ui";
import {
  ApiError,
  cancelarAdDraft,
  confirmarAdDraft,
  ESTADO_CONFIRMABLE,
  ESTADOS_CANCELABLES,
  type AdDraft,
  type AdDraftAccionResultado,
  type AdDraftStatus,
} from "@/lib/api-ads";
import { formatDateTime, formatMoney } from "@/lib/format";

// Mismas etiquetas que `/app/ordenes` para el vocabulario que comparten
// (`draft`/`confirmed`/`cancelled`) — la confirmación de un borrador de ads
// es sincrónica (una sola request HTTP hace `draft -> confirmed -> pushed`
// o `-> error`, ver `apps/api/edecan_api/routers/ads.py::confirmar_borrador`),
// así que `confirmed` en reposo es siempre un caso borde (el servidor se
// cayó a mitad de la request, entre marcar 'confirmed' y terminar el push a
// Meta) — no un estado "en progreso" que valga la pena decorar con spinner.
const STATUS_LABEL: Record<AdDraftStatus, string> = {
  draft: "Borrador",
  confirmed: "Confirmada",
  pushed: "Publicada (pausada)",
  error: "Error",
  cancelled: "Cancelada",
};

const STATUS_VARIANT: Record<AdDraftStatus, "neutral" | "brand" | "success" | "warning" | "danger"> = {
  draft: "neutral",
  confirmed: "brand",
  pushed: "success",
  error: "danger",
  cancelled: "danger",
};

const AVISO_DOBLE_CONFIRMACION =
  "Esto va a crear la campaña de verdad en tu cuenta de Meta — pero SIEMPRE en pausa " +
  "(status=PAUSED). Nada empieza a gastar tu presupuesto: activarla es una decisión tuya, " +
  "más adelante, desde el Ads Manager de Meta.";

function mensajeError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "No se pudo completar la acción.";
}

export function BorradoresAds({
  drafts,
  loading,
  onChange,
}: {
  drafts: AdDraft[];
  loading: boolean;
  onChange: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [confirmTarget, setConfirmTarget] = useState<AdDraft | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  async function handleCancelar(draft: AdDraft) {
    setBusyId(draft.id);
    setError(null);
    try {
      await cancelarAdDraft(draft.id);
      onChange();
    } catch (err) {
      setError(mensajeError(err));
    } finally {
      setBusyId(null);
    }
  }

  function handleConfirmado(_resultado: AdDraftAccionResultado) {
    setConfirmTarget(null);
    onChange();
  }

  return (
    <Card>
      <CardHeader
        title="Borradores de campaña"
        description="Nacen desde el chat (pídele a Edecán que prepare una campaña). Nada se publica sin tu confirmación aquí."
      />
      <CardBody>
        {error && (
          <div className="mb-3">
            <Alert variant="error">{error}</Alert>
          </div>
        )}

        {loading ? (
          <div className="flex justify-center py-8">
            <Spinner className="h-5 w-5 text-slate-400" />
          </div>
        ) : drafts.length === 0 ? (
          <EmptyState
            title="Sin borradores todavía"
            description="Pídele al chat que prepare una campaña de anuncios — aparecerá aquí como borrador."
          />
        ) : (
          <ul className="space-y-2">
            {drafts.map((draft) => {
              const expandido = expandedId === draft.id;
              return (
                <li
                  key={draft.id}
                  className="rounded-lg border border-slate-100 dark:border-slate-800"
                >
                  <button
                    type="button"
                    onClick={() => setExpandedId(expandido ? null : draft.id)}
                    className="flex w-full flex-wrap items-center justify-between gap-3 px-3 py-2.5 text-left"
                  >
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-slate-700 dark:text-slate-200">
                        {draft.nombre}
                      </p>
                      <p className="text-xs text-slate-400">
                        {draft.objetivo} · {formatMoney(draft.presupuesto_diario, draft.moneda)}/día ·{" "}
                        {formatDateTime(draft.created_at)}
                      </p>
                    </div>
                    <Badge variant={STATUS_VARIANT[draft.status]}>{STATUS_LABEL[draft.status]}</Badge>
                  </button>

                  {expandido && (
                    <div className="space-y-3 border-t border-slate-100 px-3 py-3 dark:border-slate-800">
                      {draft.error && <Alert variant="error">{draft.error}</Alert>}
                      {draft.external_id && (
                        <p className="text-xs text-slate-500 dark:text-slate-400">
                          ID de campaña en Meta: <code>{draft.external_id}</code> · gestiónala desde tu
                          Ads Manager de Meta.
                        </p>
                      )}
                      <div className="flex flex-wrap gap-2">
                        {draft.status === ESTADO_CONFIRMABLE && (
                          <Button size="sm" onClick={() => setConfirmTarget(draft)}>
                            Confirmar y publicar (pausada)
                          </Button>
                        )}
                        {ESTADOS_CANCELABLES.includes(draft.status) && (
                          <Button
                            size="sm"
                            variant="secondary"
                            loading={busyId === draft.id}
                            onClick={() => void handleCancelar(draft)}
                          >
                            Cancelar borrador
                          </Button>
                        )}
                      </div>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </CardBody>

      {confirmTarget && (
        <ConfirmModal
          draft={confirmTarget}
          onClose={() => setConfirmTarget(null)}
          onConfirmed={handleConfirmado}
        />
      )}
    </Card>
  );
}

function ConfirmModal({
  draft,
  onClose,
  onConfirmed,
}: {
  draft: AdDraft;
  onClose: () => void;
  onConfirmed: (resultado: AdDraftAccionResultado) => void;
}) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleConfirmar() {
    setLoading(true);
    setError(null);
    try {
      const resultado = await confirmarAdDraft(draft.id);
      onConfirmed(resultado);
    } catch (err) {
      setError(mensajeError(err));
      setLoading(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirmar-borrador-titulo"
    >
      <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-5 shadow-xl dark:border-slate-800 dark:bg-slate-900">
        <h3 id="confirmar-borrador-titulo" className="text-sm font-semibold text-slate-900 dark:text-slate-100">
          Confirmar campaña
        </h3>
        <p className="mt-2 text-sm text-slate-600 dark:text-slate-300">
          {draft.nombre} — {draft.objetivo} — {formatMoney(draft.presupuesto_diario, draft.moneda)}/día
        </p>

        <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300">
          {AVISO_DOBLE_CONFIRMACION}
        </div>

        {error && (
          <div className="mt-3">
            <Alert variant="error">{error}</Alert>
          </div>
        )}

        <div className="mt-5 flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose} disabled={loading}>
            Cancelar
          </Button>
          <Button onClick={() => void handleConfirmar()} loading={loading}>
            Sí, publicar en pausa
          </Button>
        </div>
      </div>
    </div>
  );
}
