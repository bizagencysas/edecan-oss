/**
 * Hoja de detalle de un compañero: identidad, perfil rico, políticas JSONB y
 * la acción "Encolar" tarea. Tiene un modo edición que persiste con
 * `patchWorker` (name/purpose no se editan: el backend no los expone en PATCH).
 */

"use client";

import { useState } from "react";

import { PencilIcon, BellIcon, XIcon } from "@/components/icons";
import { Alert, Badge, Button, Field, Input, Select, Switch, Textarea } from "@/components/ui";
import {
  enqueueWorkerTask,
  patchWorker,
  type AutonomyLevel,
  type PersistentWorker,
  type WorkerAvatar,
  type WorkerPatchInput,
} from "@/lib/api";
import { formatDateTime } from "@/lib/format";

import { AGENT_ACCENTS, AgentAvatar } from "./AgentAvatar";
import { MemoryPanel } from "./MemoryPanel";
import { PRESENCE_LABELS, PresenceDot, presenceState } from "./PresenceDot";
import { AUTONOMY_OPTIONS, RELATION_OPTIONS, autonomyLabel, jsonBlock, relationLabel } from "./options";

function DetailField({
  label,
  value,
  mono = false,
}: {
  label: string;
  value?: string | null;
  mono?: boolean;
}) {
  const text = value?.trim();
  return (
    <div>
      <dt className="text-[11px] font-medium uppercase tracking-wide text-slate-400 dark:text-slate-500">
        {label}
      </dt>
      <dd
        className={`mt-0.5 whitespace-pre-wrap text-sm text-slate-700 dark:text-slate-300 ${
          mono ? "font-mono text-xs" : ""
        }`}
      >
        {text || "—"}
      </dd>
    </div>
  );
}

export function WorkerDetailSheet({
  worker,
  onClose,
  onChanged,
  onOpenActivity,
}: {
  worker: PersistentWorker;
  onClose: () => void;
  onChanged: (updated: PersistentWorker) => void;
  onOpenActivity?: () => void;
}) {
  const [editing, setEditing] = useState(false);

  // Modo edición
  const [displayName, setDisplayName] = useState(worker.display_name ?? "");
  const [roleTitle, setRoleTitle] = useState(worker.role_title ?? "");
  const [roleShort, setRoleShort] = useState(worker.role_short ?? "");
  const [jobDescription, setJobDescription] = useState(worker.job_description ?? "");
  const [personality, setPersonality] = useState(worker.personality ?? "");
  const [communicationStyle, setCommunicationStyle] = useState(worker.communication_style ?? "");
  const [instructions, setInstructions] = useState(worker.instructions ?? "");
  const [constraints, setConstraints] = useState(worker.constraints ?? "");
  const [autonomyLevel, setAutonomyLevel] = useState<AutonomyLevel>(worker.autonomy_level ?? "ask");
  const [relation, setRelation] = useState<string>(worker.relation ?? "profesional");
  const [accent, setAccent] = useState<string>(worker.avatar?.accent ?? "");
  const [enabled, setEnabled] = useState(worker.enabled);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // Encolar tarea
  const [task, setTask] = useState("");
  const [enqueuing, setEnqueuing] = useState(false);
  const [taskError, setTaskError] = useState<string | null>(null);

  const state = presenceState(worker.status, worker.enabled);
  const title = worker.display_name?.trim() || worker.name;

  function beginEdit() {
    setDisplayName(worker.display_name ?? "");
    setRoleTitle(worker.role_title ?? "");
    setRoleShort(worker.role_short ?? "");
    setJobDescription(worker.job_description ?? "");
    setPersonality(worker.personality ?? "");
    setCommunicationStyle(worker.communication_style ?? "");
    setInstructions(worker.instructions ?? "");
    setConstraints(worker.constraints ?? "");
    setAutonomyLevel(worker.autonomy_level ?? "ask");
    setRelation(worker.relation ?? "profesional");
    setAccent(worker.avatar?.accent ?? "");
    setEnabled(worker.enabled);
    setSaveError(null);
    setEditing(true);
  }

  async function handleSave(event: React.FormEvent) {
    event.preventDefault();
    setSaveError(null);
    setSaving(true);
    // Si el avatar original fue generado (lleva `style`: grok_face, geometric…),
    // conservar TODO el descriptor (ojos, seed, fill…) y solo tocar el acento
    // si el usuario lo cambió — guardar `{accent}` solo destruiría la cara del
    // compañero. Sin `style`, se manda el acento como antes.
    const originalAvatar = worker.avatar;
    const avatar: WorkerAvatar = originalAvatar?.style
      ? (originalAvatar.accent ?? "") === accent
        ? originalAvatar
        : { ...originalAvatar, accent: accent || null }
      : { accent: accent || null };
    const payload: WorkerPatchInput = {
      display_name: displayName.trim(),
      role_title: roleTitle.trim(),
      role_short: roleShort.trim(),
      job_description: jobDescription.trim(),
      personality: personality.trim(),
      communication_style: communicationStyle.trim(),
      instructions: instructions.trim(),
      constraints: constraints.trim(),
      autonomy_level: autonomyLevel,
      relation,
      avatar,
      enabled,
    };
    try {
      const updated = await patchWorker(worker.id, payload);
      onChanged(updated);
      setEditing(false);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "No se pudo guardar el perfil.");
    } finally {
      setSaving(false);
    }
  }

  async function handleEnqueue(event: React.FormEvent) {
    event.preventDefault();
    if (!task.trim()) return;
    setTaskError(null);
    setEnqueuing(true);
    try {
      await enqueueWorkerTask(worker.id, task.trim());
      setTask("");
    } catch (err) {
      setTaskError(err instanceof Error ? err.message : "No se pudo encolar la tarea.");
    } finally {
      setEnqueuing(false);
    }
  }

  const approvalBlock = jsonBlock(worker.approval_policy);
  const modelBlock = jsonBlock(worker.model_policy);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="detalle-companero-titulo"
    >
      <div className="flex max-h-[90vh] w-full max-w-lg flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-xl dark:border-slate-800 dark:bg-slate-900">
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4 dark:border-slate-800">
          <div className="flex min-w-0 items-center gap-3">
            <AgentAvatar name={worker.name} displayName={worker.display_name} avatar={worker.avatar} size="lg" />
            <div className="min-w-0">
              <h3
                id="detalle-companero-titulo"
                className="truncate text-sm font-semibold text-slate-900 dark:text-slate-100"
              >
                {title}
              </h3>
              <p className="truncate text-xs text-slate-500 dark:text-slate-400">
                {worker.role_title?.trim() || worker.purpose}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-1.5">
            {onOpenActivity && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={onOpenActivity}
                title="Ver actividad reciente"
                aria-label="Ver actividad reciente"
              >
                <BellIcon className="h-4 w-4" />
              </Button>
            )}
            <Button type="button" variant="ghost" size="sm" onClick={onClose} aria-label="Cerrar">
              <XIcon className="h-4 w-4" />
            </Button>
          </div>
        </div>

        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-5 py-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className="inline-flex items-center gap-1.5 text-xs text-slate-500 dark:text-slate-400">
              <PresenceDot state={state} />
              {PRESENCE_LABELS[state]}
            </span>
            <Badge variant="brand">{autonomyLabel(worker.autonomy_level)}</Badge>
            <Badge variant="neutral">{relationLabel(worker.relation)}</Badge>
          </div>

          {editing ? (
            <form onSubmit={handleSave} className="space-y-3">
              {saveError && <Alert variant="error">{saveError}</Alert>}

              <Field label="Nombre visible" htmlFor="w-edit-display">
                <Input id="w-edit-display" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
              </Field>
              <Field label="Rol" htmlFor="w-edit-role">
                <Input id="w-edit-role" value={roleTitle} onChange={(e) => setRoleTitle(e.target.value)} />
              </Field>
              <Field label="Rol corto" htmlFor="w-edit-role-short">
                <Input id="w-edit-role-short" value={roleShort} onChange={(e) => setRoleShort(e.target.value)} />
              </Field>
              <Field label="Descripción del trabajo" htmlFor="w-edit-job">
                <Textarea id="w-edit-job" rows={3} value={jobDescription} onChange={(e) => setJobDescription(e.target.value)} />
              </Field>
              <Field label="Personalidad" htmlFor="w-edit-personality">
                <Textarea id="w-edit-personality" rows={2} value={personality} onChange={(e) => setPersonality(e.target.value)} />
              </Field>
              <Field label="Estilo de comunicación" htmlFor="w-edit-style">
                <Textarea id="w-edit-style" rows={2} value={communicationStyle} onChange={(e) => setCommunicationStyle(e.target.value)} />
              </Field>
              <Field label="Instrucciones" htmlFor="w-edit-instructions">
                <Textarea id="w-edit-instructions" rows={3} value={instructions} onChange={(e) => setInstructions(e.target.value)} />
              </Field>
              <Field label="Restricciones" htmlFor="w-edit-constraints">
                <Textarea id="w-edit-constraints" rows={3} value={constraints} onChange={(e) => setConstraints(e.target.value)} />
              </Field>

              <Field label="Autonomía" htmlFor="w-edit-autonomy">
                <Select
                  id="w-edit-autonomy"
                  value={autonomyLevel}
                  onChange={(e) => setAutonomyLevel(e.target.value as AutonomyLevel)}
                >
                  {AUTONOMY_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </Select>
              </Field>

              <Field label="Relación" htmlFor="w-edit-relation">
                <Select
                  id="w-edit-relation"
                  value={relation}
                  onChange={(e) => setRelation(e.target.value)}
                >
                  {RELATION_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </Select>
                <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                  {RELATION_OPTIONS.find((o) => o.value === relation)?.description}
                </p>
              </Field>

              <Field label="Acento del avatar">
                <div className="flex flex-wrap gap-1.5">
                  {AGENT_ACCENTS.map((a) => {
                    const selected = a.key === accent;
                    return (
                      <button
                        key={a.key}
                        type="button"
                        title={a.label}
                        aria-label={a.label}
                        aria-pressed={selected}
                        onClick={() => setAccent(a.key)}
                        className={`h-6 w-6 rounded-full ${a.swatch} ${
                          selected
                            ? "ring-2 ring-brand-600 ring-offset-2 ring-offset-white dark:ring-offset-slate-900"
                            : "opacity-70 hover:opacity-100"
                        }`}
                      />
                    );
                  })}
                </div>
              </Field>

              <Switch
                checked={enabled}
                onChange={setEnabled}
                label={enabled ? "Activo" : "Desactivado"}
              />

              <div className="flex justify-end gap-2 pt-1">
                <Button type="button" variant="secondary" onClick={() => setEditing(false)} disabled={saving}>
                  Cancelar
                </Button>
                <Button type="submit" loading={saving}>
                  Guardar
                </Button>
              </div>
            </form>
          ) : (
            <>
              <div className="flex items-center justify-between gap-3">
                <p className="text-xs text-slate-400 dark:text-slate-500">
                  Creado: {formatDateTime(worker.updated_at)}
                  {worker.workspace ? ` · ${worker.workspace}` : ""}
                </p>
                <Button type="button" variant="secondary" size="sm" onClick={beginEdit}>
                  <PencilIcon className="h-3.5 w-3.5" />
                  Editar
                </Button>
              </div>

              <dl className="space-y-3">
                <DetailField label="Descripción del trabajo" value={worker.job_description} />
                <DetailField label="Personalidad" value={worker.personality} />
                <DetailField label="Estilo de comunicación" value={worker.communication_style} />
                <DetailField label="Instrucciones" value={worker.instructions} />
                <DetailField label="Restricciones" value={worker.constraints} />
                <DetailField label="Política de aprobación" value={approvalBlock ?? undefined} mono />
                <DetailField label="Política de modelo" value={modelBlock ?? undefined} mono />
              </dl>
            </>
          )}

          <form onSubmit={handleEnqueue} className="border-t border-slate-100 pt-4 dark:border-slate-800">
            {taskError && <Alert variant="error">{taskError}</Alert>}
            <Field label="Encolar tarea" htmlFor="worker-task" hint="El compañero la toma cuando esté disponible.">
              <div className="flex gap-2">
                <Input
                  id="worker-task"
                  value={task}
                  onChange={(e) => setTask(e.target.value)}
                  placeholder="Instrucción para este compañero…"
                  className="min-w-0 flex-1"
                />
                <Button type="submit" size="sm" loading={enqueuing} disabled={!task.trim()}>
                  Encolar
                </Button>
              </div>
            </Field>
          </form>

          <section
            aria-label="Memoria del compañero"
            className="border-t border-slate-100 pt-4 dark:border-slate-800"
          >
            <h4 className="mb-2 text-[11px] font-medium uppercase tracking-wide text-slate-400 dark:text-slate-500">
              Memoria
            </h4>
            <MemoryPanel workerId={worker.id} />
          </section>
        </div>
      </div>
    </div>
  );
}