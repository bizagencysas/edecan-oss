"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";

import {
  createPhoneAgentTemplate,
  deletePhoneAgentTemplate,
  listPhoneAgentTemplates,
  updatePhoneAgentTemplate,
} from "@/lib/api";
import type { PhoneAgentTemplate, PhoneAgentTemplateInput } from "@/lib/types";
import { Alert, Button, Card, CardBody, CardHeader, Checkbox, Field, Input, Textarea } from "@/components/ui";

const EMPTY_FORM: PhoneAgentTemplateInput = {
  name: "",
  agent_name: "",
  persona_prompt: "",
  default_goal: "",
  opening_message: "",
  is_default: false,
};

const STARTERS: Array<{ label: string; value: PhoneAgentTemplateInput }> = [
  {
    label: "Asistente",
    value: {
      name: "Asistente personal",
      agent_name: "Sofía",
      persona_prompt:
        "Sé cordial, concreta y resolutiva. Escucha primero, haz una pregunta a la vez y deja un resumen claro para continuar desde Edecan.",
      default_goal: "Entender la solicitud y acordar el siguiente paso.",
      opening_message: "Te llamo para ayudarte con una gestión pendiente.",
      is_default: false,
    },
  },
  {
    label: "Ventas",
    value: {
      name: "Ventas consultivas",
      agent_name: "Camila",
      persona_prompt:
        "Actúa como una asesora consultiva: entiende la necesidad antes de ofrecer, explica con claridad, nunca presiona y busca un siguiente paso concreto.",
      default_goal: "Entender la necesidad y acordar una demostración o seguimiento.",
      opening_message: "Quisiera conocer brevemente qué necesitas y ver si podemos ayudarte.",
      is_default: false,
    },
  },
  {
    label: "Seguimiento",
    value: {
      name: "Seguimiento y citas",
      agent_name: "Sara",
      persona_prompt:
        "Habla con calma, confirma fechas y datos de forma explícita, no inventes disponibilidad y registra cualquier cambio solicitado.",
      default_goal: "Confirmar la cita o pendiente y registrar cualquier cambio.",
      opening_message: "Te llamo para confirmar un pendiente y saber si necesitas hacer algún cambio.",
      is_default: false,
    },
  },
];

function toInput(template: PhoneAgentTemplate): PhoneAgentTemplateInput {
  return {
    name: template.name,
    agent_name: template.agent_name,
    persona_prompt: template.persona_prompt,
    default_goal: template.default_goal,
    opening_message: template.opening_message,
    is_default: template.is_default,
  };
}

export function PhoneAgentTemplatesSettings() {
  const [templates, setTemplates] = useState<PhoneAgentTemplate[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [form, setForm] = useState<PhoneAgentTemplateInput>(EMPTY_FORM);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const rows = await listPhoneAgentTemplates();
      setTemplates(rows);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudieron cargar tus agentes.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  function newTemplate(initial: PhoneAgentTemplateInput = EMPTY_FORM) {
    setSelectedId(null);
    setForm({ ...initial, is_default: templates.length === 0 || initial.is_default });
    setError(null);
    setSuccess(null);
  }

  function selectTemplate(template: PhoneAgentTemplate) {
    setSelectedId(template.id);
    setForm(toInput(template));
    setError(null);
    setSuccess(null);
  }

  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setSuccess(null);
    try {
      const saved = selectedId
        ? await updatePhoneAgentTemplate(selectedId, form)
        : await createPhoneAgentTemplate(form);
      await load();
      setSelectedId(saved.id);
      setForm(toInput(saved));
      setSuccess("Guardado. Las próximas llamadas usarán esta configuración.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo guardar el agente.");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!selectedId) return;
    if (!window.confirm("¿Eliminar este agente de llamada? Las llamadas anteriores conservarán su configuración.")) {
      return;
    }
    setBusy(true);
    setError(null);
    setSuccess(null);
    try {
      await deletePhoneAgentTemplate(selectedId);
      setSelectedId(null);
      setForm(EMPTY_FORM);
      await load();
      setSuccess("Agente eliminado. Las llamadas anteriores no cambiaron.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo eliminar el agente.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="lg:col-span-2">
      <CardHeader
        title="Agentes para llamadas"
        description="Guarda formas de llamar para ventas, seguimiento o asistencia. Edecan elige el predeterminado, pero siempre te muestra el número y el objetivo antes de llamar."
      />
      <CardBody className="space-y-5">
        {error && <Alert variant="error">{error}</Alert>}
        {success && <Alert variant="success">{success}</Alert>}

        <div className="grid gap-5 lg:grid-cols-[minmax(14rem,0.75fr)_minmax(0,1.5fr)]">
          <div className="space-y-3">
            <div className="flex items-center justify-between gap-2">
              <p className="text-sm font-semibold text-slate-900 dark:text-slate-100">Tus agentes</p>
              <Button type="button" size="sm" variant="secondary" onClick={() => newTemplate()}>
                Nuevo
              </Button>
            </div>
            {loading ? (
              <p className="text-sm text-slate-500 dark:text-slate-400">Cargando…</p>
            ) : templates.length === 0 ? (
              <div className="rounded-xl border border-dashed border-slate-300 p-4 dark:border-slate-700">
                <p className="text-sm text-slate-600 dark:text-slate-300">
                  Crea uno desde cero o empieza con una idea.
                </p>
                <div className="mt-3 flex flex-wrap gap-2">
                  {STARTERS.map((starter) => (
                    <Button
                      key={starter.label}
                      type="button"
                      size="sm"
                      variant="secondary"
                      onClick={() => newTemplate(starter.value)}
                    >
                      {starter.label}
                    </Button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="space-y-2">
                {templates.map((template) => (
                  <button
                    key={template.id}
                    type="button"
                    onClick={() => selectTemplate(template)}
                    className={`w-full rounded-xl border p-3 text-left transition-colors ${
                      selectedId === template.id
                        ? "border-brand-500 bg-brand-50/70 dark:bg-brand-950/30"
                        : "border-slate-200 hover:border-slate-300 dark:border-slate-700 dark:hover:border-slate-600"
                    }`}
                  >
                    <span className="flex items-center justify-between gap-2">
                      <span className="text-sm font-medium text-slate-900 dark:text-slate-100">
                        {template.name}
                      </span>
                      {template.is_default && (
                        <span className="rounded-full bg-brand-100 px-2 py-0.5 text-[11px] font-medium text-brand-700 dark:bg-brand-950 dark:text-brand-300">
                          Predeterminado
                        </span>
                      )}
                    </span>
                    <span className="mt-1 block text-xs text-slate-500 dark:text-slate-400">
                      Se presenta como {template.agent_name}
                    </span>
                  </button>
                ))}
                <div className="flex flex-wrap gap-2 pt-1">
                  {STARTERS.map((starter) => (
                    <button
                      key={starter.label}
                      type="button"
                      onClick={() => newTemplate(starter.value)}
                      className="text-xs text-brand-700 hover:underline dark:text-brand-300"
                    >
                      + {starter.label}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>

          <form onSubmit={save} className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Nombre para reconocerlo" htmlFor="phone_agent_template_name">
                <Input
                  id="phone_agent_template_name"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  placeholder="Ventas consultivas"
                  maxLength={80}
                  required
                />
              </Field>
              <Field label="Cómo se presenta" htmlFor="phone_agent_name">
                <Input
                  id="phone_agent_name"
                  value={form.agent_name}
                  onChange={(e) => setForm({ ...form, agent_name: e.target.value })}
                  placeholder="Camila"
                  maxLength={80}
                  required
                />
              </Field>
            </div>

            <Field
              label="Personalidad y forma de conversar"
              htmlFor="phone_agent_persona"
              hint="Describe cómo escucha, pregunta, explica y cierra. No hace falta escribir instrucciones técnicas."
            >
              <Textarea
                id="phone_agent_persona"
                value={form.persona_prompt}
                onChange={(e) => setForm({ ...form, persona_prompt: e.target.value })}
                placeholder="Escucha antes de ofrecer, habla con calidez y busca un siguiente paso claro."
                maxLength={4000}
                required
              />
            </Field>

            <Field
              label="Qué debe lograr normalmente"
              htmlFor="phone_agent_goal"
              hint="Edecan lo mostrará para confirmación y puedes cambiarlo en cada llamada."
            >
              <Textarea
                id="phone_agent_goal"
                value={form.default_goal}
                onChange={(e) => setForm({ ...form, default_goal: e.target.value })}
                placeholder="Entender la necesidad y acordar una demostración."
                maxLength={500}
                required
              />
            </Field>

            <Field
              label="Primera frase después de identificarse"
              htmlFor="phone_agent_opening"
              hint="Opcional. Edecan siempre aclara primero que es un asistente automatizado."
            >
              <Textarea
                id="phone_agent_opening"
                value={form.opening_message}
                onChange={(e) => setForm({ ...form, opening_message: e.target.value })}
                placeholder="Quisiera conocer brevemente qué necesitas y ver si podemos ayudarte."
                maxLength={700}
              />
            </Field>

            <Checkbox
              checked={form.is_default}
              onChange={(e) => setForm({ ...form, is_default: e.target.checked })}
              label="Usar este agente por defecto en llamadas salientes"
            />

            <div className="flex flex-wrap gap-2">
              <Button type="submit" loading={busy} disabled={loading}>
                {selectedId ? "Guardar cambios" : "Crear agente"}
              </Button>
              {selectedId && (
                <Button type="button" variant="danger" onClick={() => void remove()} disabled={busy}>
                  Eliminar
                </Button>
              )}
            </div>
            <p className="text-xs leading-5 text-slate-500 dark:text-slate-400">
              Cambiar una plantilla afecta solo llamadas futuras. Cada borrador conserva la versión que revisaste, y ninguna plantilla puede saltarse el consentimiento ni la confirmación final.
            </p>
          </form>
        </div>
      </CardBody>
    </Card>
  );
}
