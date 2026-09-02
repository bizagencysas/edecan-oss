/**
 * Flujo "Enseñar una tarea" (`POST /v1/skills/teach` → `/step` → `/finish` →
 * `POST /v1/skills/{id}/approve`). Captura pasos deterministas y produce una
 * skill `draft`; solo `approve` la activa. Hoja modal con el mismo patrón de
 * overlay que `CreateWorkerSheet`.
 */

"use client";

import { useState } from "react";

import { CheckIcon, PlusIcon, XIcon } from "@/components/icons";
import { Alert, Badge, Button, Field, Input, Textarea } from "@/components/ui";
import {
  approveSkill,
  teachSkillFinish,
  teachSkillStart,
  teachSkillStep,
  type SkillDetail,
  type TeachStep,
} from "@/lib/api-skills";

function EmptyStep(): TeachStep {
  return { action: "", selector: "", decision: "", input: "", output: "" };
}

export function TeachTaskSheet({
  onClose,
  onApproved,
}: {
  onClose: () => void;
  onApproved?: (skill: SkillDetail) => void;
}) {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [nombre, setNombre] = useState("");
  const [descripcion, setDescripcion] = useState("");
  const [step, setStep] = useState<TeachStep>(EmptyStep());
  const [pasos, setPasos] = useState<TeachStep[]>([]);
  const [draft, setDraft] = useState<SkillDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleStart(e: React.FormEvent) {
    e.preventDefault();
    if (!nombre.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const sesion = await teachSkillStart({
        nombre: nombre.trim(),
        descripcion: descripcion.trim() || undefined,
      });
      setSessionId(sesion.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo iniciar la sesión.");
    } finally {
      setBusy(false);
    }
  }

  async function handleAddStep(e: React.FormEvent) {
    e.preventDefault();
    if (!sessionId || !step.action.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const sesion = await teachSkillStep(sessionId, step);
      setPasos(sesion.pasos);
      setStep(EmptyStep());
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo guardar el paso.");
    } finally {
      setBusy(false);
    }
  }

  async function handleFinish() {
    if (!sessionId || pasos.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      const skill = await teachSkillFinish(sessionId);
      setDraft(skill);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo crear el borrador.");
    } finally {
      setBusy(false);
    }
  }

  async function handleApprove() {
    if (!draft) return;
    setBusy(true);
    setError(null);
    try {
      const skill = await approveSkill(draft.id);
      setDraft(skill);
      onApproved?.(skill);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo aprobar la skill.");
    } finally {
      setBusy(false);
    }
  }

  const mostrarPasos = draft === null && sessionId !== null;
  const draftActiva = draft !== null && draft.status === "active";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="ensenar-tarea-titulo"
    >
      <div className="flex max-h-[90vh] w-full max-w-lg flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-xl dark:border-slate-800 dark:bg-slate-900">
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4 dark:border-slate-800">
          <div>
            <h3
              id="ensenar-tarea-titulo"
              className="text-sm font-semibold text-slate-900 dark:text-slate-100"
            >
              Enseñar una tarea
            </h3>
            <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
              Captura los pasos y Edecán los convierte en una skill que apruebas tú.
            </p>
          </div>
          <Button type="button" variant="ghost" size="sm" onClick={onClose} aria-label="Cerrar">
            <XIcon className="h-4 w-4" />
          </Button>
        </div>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4">
          {error && <Alert variant="error">{error}</Alert>}

          {draft === null && sessionId === null && (
            <form onSubmit={handleStart} className="space-y-3">
              <Field label="Nombre de la tarea" htmlFor="teach-nombre">
                <Input
                  id="teach-nombre"
                  value={nombre}
                  onChange={(e) => setNombre(e.target.value)}
                  placeholder="Cierre de caja del día"
                  autoFocus
                />
              </Field>
              <Field label="Descripción" htmlFor="teach-descripcion">
                <Textarea
                  id="teach-descripcion"
                  rows={2}
                  value={descripcion}
                  onChange={(e) => setDescripcion(e.target.value)}
                  placeholder="Qué hace y para qué sirve esta rutina."
                />
              </Field>
              <div className="flex justify-end">
                <Button type="submit" loading={busy} disabled={!nombre.trim()}>
                  Comenzar
                </Button>
              </div>
            </form>
          )}

          {mostrarPasos && (
            <>
              <div>
                <p className="text-[11px] font-medium uppercase tracking-wide text-slate-400 dark:text-slate-500">
                  Pasos capturados ({pasos.length})
                </p>
                {pasos.length === 0 ? (
                  <p className="mt-1 text-xs text-slate-400 dark:text-slate-500">
                    Agrega el primer paso con el formulario de abajo.
                  </p>
                ) : (
                  <ol className="mt-1.5 space-y-1.5">
                    {pasos.map((p, i) => (
                      <li
                        key={i}
                        className="rounded-lg border border-slate-100 px-3 py-2 dark:border-slate-800"
                      >
                        <p className="text-xs font-medium text-slate-700 dark:text-slate-300">
                          {i + 1}. {p.action || "(sin acción)"}
                        </p>
                        {p.selector && (
                          <p className="mt-0.5 text-[11px] text-slate-500 dark:text-slate-400">
                            Selector: {p.selector}
                          </p>
                        )}
                      </li>
                    ))}
                  </ol>
                )}
              </div>

              <form onSubmit={handleAddStep} className="space-y-2.5 border-t border-slate-100 pt-4 dark:border-slate-800">
                <Field label="Acción" htmlFor="teach-step-action">
                  <Textarea
                    id="teach-step-action"
                    rows={2}
                    value={step.action}
                    onChange={(e) => setStep((prev) => ({ ...prev, action: e.target.value }))}
                    placeholder="Abre la aplicación de ventas y descarga el reporte del día."
                  />
                </Field>
                <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                  <Field label="Selector" htmlFor="teach-step-selector">
                    <Input
                      id="teach-step-selector"
                      value={step.selector}
                      onChange={(e) => setStep((prev) => ({ ...prev, selector: e.target.value }))}
                      placeholder="#reporte-diario"
                    />
                  </Field>
                  <Field label="Decisión" htmlFor="teach-step-decision">
                    <Input
                      id="teach-step-decision"
                      value={step.decision}
                      onChange={(e) => setStep((prev) => ({ ...prev, decision: e.target.value }))}
                      placeholder="Si está vacío, reintenta."
                    />
                  </Field>
                  <Field label="Input" htmlFor="teach-step-input">
                    <Input
                      id="teach-step-input"
                      value={step.input}
                      onChange={(e) => setStep((prev) => ({ ...prev, input: e.target.value }))}
                      placeholder="fecha de hoy"
                    />
                  </Field>
                  <Field label="Output" htmlFor="teach-step-output">
                    <Input
                      id="teach-step-output"
                      value={step.output}
                      onChange={(e) => setStep((prev) => ({ ...prev, output: e.target.value }))}
                      placeholder="total del día"
                    />
                  </Field>
                </div>
                <div className="flex items-center justify-between gap-2 pt-1">
                  <Button
                    type="submit"
                    variant="secondary"
                    size="sm"
                    loading={busy}
                    disabled={!step.action.trim()}
                  >
                    <PlusIcon className="h-3.5 w-3.5" />
                    Agregar paso
                  </Button>
                  <Button
                    type="button"
                    onClick={handleFinish}
                    loading={busy}
                    disabled={pasos.length === 0}
                  >
                    Terminar y crear borrador
                  </Button>
                </div>
              </form>
            </>
          )}

          {draft !== null && (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                  {draft.nombre}
                </span>
                <Badge variant={draftActiva ? "success" : "warning"}>
                  {draftActiva ? "activa" : "borrador"}
                </Badge>
              </div>
              {draft.descripcion && (
                <p className="text-xs text-slate-500 dark:text-slate-400">{draft.descripcion}</p>
              )}
              <div className="rounded-lg border border-slate-100 bg-slate-50 px-3 py-2.5 dark:border-slate-800 dark:bg-slate-950/40">
                <p className="whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-slate-600 dark:text-slate-300">
                  {draft.contenido}
                </p>
              </div>
              {!draftActiva ? (
                <div className="flex justify-end gap-2">
                  <Button type="button" variant="secondary" onClick={onClose} disabled={busy}>
                    Cerrar
                  </Button>
                  <Button type="button" onClick={handleApprove} loading={busy}>
                    <CheckIcon className="h-3.5 w-3.5" />
                    Aprobar y activar
                  </Button>
                </div>
              ) : (
                <div className="flex justify-end">
                  <Button type="button" onClick={onClose}>
                    Listo
                  </Button>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}