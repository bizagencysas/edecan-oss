/**
 * Hoja "Nuevo compañero": recoge identidad (name, display_name, role_title),
 * propósito/descripción, autonomía y el acento del avatar, y crea con
 * `createWorker`. Mismo patrón de overlay que los modales del resto de `(app)`.
 */

"use client";

import { useState } from "react";

import { Alert, Button, Field, Input, Select, Textarea } from "@/components/ui";
import { XIcon } from "@/components/icons";
import {
  createWorker,
  type AutonomyLevel,
  type PersistentWorker,
  type WorkerAvatar,
} from "@/lib/api";

import { AGENT_ACCENTS, HEX_ACCENTS, AgentAvatar } from "./AgentAvatar";
import { AUTONOMY_OPTIONS, RELATION_OPTIONS } from "./options";

export function CreateWorkerSheet({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (worker: PersistentWorker) => void;
}) {
  const [name, setName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [roleTitle, setRoleTitle] = useState("");
  const [purpose, setPurpose] = useState("");
  const [jobDescription, setJobDescription] = useState("");
  const [autonomyLevel, setAutonomyLevel] = useState<AutonomyLevel>("ask");
  const [relation, setRelation] = useState<string>("profesional");
  const [accent, setAccent] = useState<string>("stone");
  const [hexAccent, setHexAccent] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!name.trim() || !purpose.trim()) return;
    setError(null);
    setBusy(true);
    // Un color hex se guarda con `style: "geometric"` (módulo
    // `edecan_creative.avatars`); un acento tonal usa la clave de antes.
    const avatar: WorkerAvatar = hexAccent
      ? { style: "geometric", accent: hexAccent }
      : { accent };
    try {
      const worker = await createWorker({
        name: name.trim(),
        purpose: purpose.trim(),
        display_name: displayName.trim() || null,
        role_title: roleTitle.trim() || null,
        job_description: jobDescription.trim() || null,
        autonomy_level: autonomyLevel,
        relation,
        avatar,
      });
      onCreated(worker);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo crear el compañero.");
      setBusy(false);
    }
  }

  const previewInitials = displayName.trim() || name.trim() || "?";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="crear-companero-titulo"
    >
      <div className="flex max-h-[90vh] w-full max-w-lg flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-xl dark:border-slate-800 dark:bg-slate-900">
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4 dark:border-slate-800">
          <div>
            <h3
              id="crear-companero-titulo"
              className="text-sm font-semibold text-slate-900 dark:text-slate-100"
            >
              Nuevo compañero
            </h3>
            <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
              Dale identidad y una forma de trabajar.
            </p>
          </div>
          <Button type="button" variant="ghost" size="sm" onClick={onClose} aria-label="Cerrar">
            <XIcon className="h-4 w-4" />
          </Button>
        </div>

        <form onSubmit={handleSubmit} className="min-h-0 flex-1 space-y-3 overflow-y-auto px-5 py-4">
          {error && <Alert variant="error">{error}</Alert>}

          <div className="flex items-center gap-3">
            <AgentAvatar
              name={name || "?"}
              displayName={displayName}
              avatar={hexAccent ? { style: "geometric", accent: hexAccent } : { accent }}
              size="xl"
            />
            <div className="min-w-0 flex-1">
              <Field label="Acento del avatar">
                <div className="flex flex-wrap gap-1.5">
                  {AGENT_ACCENTS.map((a) => {
                    const selected = !hexAccent && a.key === accent;
                    return (
                      <button
                        key={a.key}
                        type="button"
                        title={a.label}
                        aria-label={a.label}
                        aria-pressed={selected}
                        onClick={() => {
                          setAccent(a.key);
                          setHexAccent(null);
                        }}
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
              <Field label="Color">
                <div className="flex flex-wrap gap-1.5">
                  {HEX_ACCENTS.map((c) => {
                    const selected = hexAccent === c.value;
                    return (
                      <button
                        key={c.value}
                        type="button"
                        title={c.label}
                        aria-label={c.label}
                        aria-pressed={selected}
                        onClick={() => {
                          setHexAccent(c.value);
                          setAccent("");
                        }}
                        style={{ backgroundColor: c.value }}
                        className={`h-6 w-6 rounded-full ${
                          selected
                            ? "ring-2 ring-brand-600 ring-offset-2 ring-offset-white dark:ring-offset-slate-900"
                            : "opacity-80 hover:opacity-100"
                        }`}
                      />
                    );
                  })}
                </div>
              </Field>
            </div>
          </div>
          <p className="text-[11px] text-slate-400">
            Iniciales: {previewInitials ? previewInitials : "—"}
          </p>

          <Field label="Nombre" htmlFor="worker-name">
            <Input
              id="worker-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Investigador nocturno"
              autoFocus
            />
          </Field>

          <Field
            label="Nombre visible"
            htmlFor="worker-display-name"
            hint="Cómo lo ves en la lista y los chats. Si lo dejas vacío, usa el nombre."
          >
            <Input
              id="worker-display-name"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="Nico"
            />
          </Field>

          <Field label="Rol" htmlFor="worker-role">
            <Input
              id="worker-role"
              value={roleTitle}
              onChange={(e) => setRoleTitle(e.target.value)}
              placeholder="Analista de mercado"
            />
          </Field>

          <Field label="Para qué existe" htmlFor="worker-purpose">
            <Textarea
              id="worker-purpose"
              value={purpose}
              onChange={(e) => setPurpose(e.target.value)}
              rows={3}
              placeholder="Vigila noticias de tu sector cada mañana y resume lo que importa."
            />
          </Field>

          <Field
            label="Descripción del trabajo"
            htmlFor="worker-job"
            hint="Opcional. Detalla sus responsabilidades para que sepa qué cubre."
          >
            <Textarea
              id="worker-job"
              value={jobDescription}
              onChange={(e) => setJobDescription(e.target.value)}
              rows={3}
            />
          </Field>

          <Field label="Autonomía" htmlFor="worker-autonomy">
            <Select
              id="worker-autonomy"
              value={autonomyLevel}
              onChange={(e) => setAutonomyLevel(e.target.value as AutonomyLevel)}
            >
              {AUTONOMY_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </Select>
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
              {AUTONOMY_OPTIONS.find((o) => o.value === autonomyLevel)?.description}
            </p>
          </Field>

          <Field label="Relación" htmlFor="worker-relation">
            <Select
              id="worker-relation"
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

          <div className="flex justify-end gap-2 pt-1">
            <Button type="button" variant="secondary" onClick={onClose} disabled={busy}>
              Cancelar
            </Button>
            <Button type="submit" loading={busy} disabled={!name.trim() || !purpose.trim()}>
              Crear
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}