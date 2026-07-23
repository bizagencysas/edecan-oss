"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";

import {
  createPhoneAgentTemplate,
  deletePhoneAgentTemplate,
  listPhoneAgentTemplates,
  updatePhoneAgentTemplate,
} from "@/lib/api";
import { listVoces, type VozDisponible } from "@/lib/api-voz";
import type { PhoneAgentTemplate, PhoneAgentTemplateInput } from "@/lib/types";
import {
  Alert,
  Button,
  Card,
  CardBody,
  CardHeader,
  Checkbox,
  Field,
  Input,
  Select,
  Textarea,
} from "@/components/ui";

const EMPTY_FORM: PhoneAgentTemplateInput = {
  name: "",
  agent_name: "",
  persona_prompt: "",
  default_goal: "",
  opening_message: "",
  knowledge_context: "",
  required_information: "",
  voice_id: "",
  operating_profile: {
    funcion_y_mision: "",
    capabilities: "",
    out_of_scope: "",
    allowed_actions: "",
    prohibited_actions: "",
    escalation_rules: "",
    success_criteria: "",
  },
  handles_inbound: true,
  handles_outbound: true,
  is_default: false,
  is_inbound_default: false,
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
      knowledge_context: "",
      required_information: "Nombre, motivo de contacto y mejor forma de continuar.",
      voice_id: "",
      operating_profile: {
        funcion_y_mision:
          "Ser la primera línea de atención: entender la solicitud, resolver gestiones simples y dejar el caso listo para continuar.",
        capabilities:
          "Responder preguntas generales autorizadas, recopilar datos, confirmar información y coordinar el siguiente paso.",
        out_of_scope:
          "Negociaciones, decisiones contractuales, soporte especializado y cualquier tema no documentado.",
        allowed_actions:
          "Hacer preguntas, explicar información autorizada, confirmar datos y tomar un recado.",
        prohibited_actions:
          "No negociar precios, no aceptar contratos, no prometer resultados y no revelar información privada.",
        escalation_rules:
          "Pedir ayuda o tomar un recado cuando la solicitud sea sensible, no esté documentada o requiera una decisión humana.",
        success_criteria:
          "La persona entiende qué ocurrirá después y el registro contiene los datos necesarios.",
      },
      handles_inbound: true,
      handles_outbound: true,
      is_default: false,
      is_inbound_default: false,
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
      knowledge_context: "",
      required_information: "Necesidad principal, presupuesto aproximado y fecha deseada.",
      voice_id: "",
      operating_profile: {
        funcion_y_mision:
          "Entender la necesidad comercial, determinar si existe encaje y acordar un siguiente paso concreto.",
        capabilities:
          "Descubrimiento consultivo, explicación de la oferta autorizada, manejo de dudas y coordinación de demostraciones.",
        out_of_scope:
          "Asesoría legal, soporte técnico profundo, aprobación de crédito y condiciones comerciales no documentadas.",
        allowed_actions:
          "Calificar la oportunidad, explicar la oferta aprobada y coordinar una demostración o seguimiento.",
        prohibited_actions:
          "No inventar descuentos, no garantizar resultados, no presionar y no cerrar condiciones fuera del contexto autorizado.",
        escalation_rules:
          "Escalar precios especiales, requisitos legales, integraciones no documentadas o cualquier compromiso extraordinario.",
        success_criteria:
          "Existe una necesidad clara y se acordó una demostración, seguimiento o cierre respetuoso sin encaje.",
      },
      handles_inbound: true,
      handles_outbound: true,
      is_default: false,
      is_inbound_default: false,
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
      knowledge_context: "",
      required_information: "Confirmación, nueva fecha solicitada y cualquier observación importante.",
      voice_id: "",
      operating_profile: {
        funcion_y_mision:
          "Confirmar citas y pendientes, detectar cambios y mantener la información operativa actualizada.",
        capabilities:
          "Confirmar asistencia, recopilar una nueva fecha solicitada y registrar observaciones.",
        out_of_scope:
          "Diagnósticos, cambios de servicio, cobros y solicitudes ajenas a la cita o pendiente.",
        allowed_actions:
          "Confirmar datos, registrar una solicitud de cambio y dejar observaciones para seguimiento.",
        prohibited_actions:
          "No inventar disponibilidad, no confirmar cambios que el sistema no pueda garantizar y no ampliar el alcance de la cita.",
        escalation_rules:
          "Tomar un recado cuando la nueva fecha no esté disponible o la persona plantee una solicitud distinta.",
        success_criteria:
          "La cita queda confirmada o existe una solicitud de cambio clara para revisión.",
      },
      handles_inbound: true,
      handles_outbound: true,
      is_default: false,
      is_inbound_default: false,
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
    knowledge_context: template.knowledge_context ?? "",
    required_information: template.required_information ?? "",
    voice_id: template.voice_id ?? "",
    operating_profile: {
      funcion_y_mision: template.operating_profile?.funcion_y_mision ?? "",
      capabilities: template.operating_profile?.capabilities ?? "",
      out_of_scope: template.operating_profile?.out_of_scope ?? "",
      allowed_actions: template.operating_profile?.allowed_actions ?? "",
      prohibited_actions: template.operating_profile?.prohibited_actions ?? "",
      escalation_rules: template.operating_profile?.escalation_rules ?? "",
      success_criteria: template.operating_profile?.success_criteria ?? "",
    },
    handles_inbound: template.handles_inbound,
    handles_outbound: template.handles_outbound,
    is_default: template.is_default,
    is_inbound_default: template.is_inbound_default,
  };
}

export function PhoneAgentTemplatesSettings({ onChanged }: { onChanged?: () => void } = {}) {
  const [templates, setTemplates] = useState<PhoneAgentTemplate[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [form, setForm] = useState<PhoneAgentTemplateInput>(EMPTY_FORM);
  const [voices, setVoices] = useState<VozDisponible[]>([]);
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
    void listVoces()
      .then(setVoices)
      .catch(() => setVoices([]));
  }, [load]);

  function newTemplate(initial: PhoneAgentTemplateInput = EMPTY_FORM) {
    setSelectedId(null);
    setForm({
      ...initial,
      operating_profile: { ...initial.operating_profile },
      is_default:
        (templates.length === 0 && initial.handles_outbound) || initial.is_default,
      is_inbound_default:
        (templates.length === 0 && initial.handles_inbound) ||
        initial.is_inbound_default,
    });
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
      onChanged?.();
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
      onChanged?.();
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
        description="Crea hasta 20 identidades separadas para ventas, negocios, marketing o asistencia. Puedes pedir una por nombre desde el chat; Edecan nunca la sustituye por otra."
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
                      <span className="flex flex-wrap justify-end gap-1">
                        {template.is_default && (
                          <span className="rounded-full bg-brand-100 px-2 py-0.5 text-[11px] font-medium text-brand-700 dark:bg-brand-950 dark:text-brand-300">
                            Llama
                          </span>
                        )}
                        {template.is_inbound_default && (
                          <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-medium text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300">
                            Recibe
                          </span>
                        )}
                      </span>
                    </span>
                    <span className="mt-1 block text-xs text-slate-500 dark:text-slate-400">
                      Se presenta como {template.agent_name} ·{" "}
                      {[
                        template.handles_outbound ? "salientes" : "",
                        template.handles_inbound ? "entrantes" : "",
                      ]
                        .filter(Boolean)
                        .join(" y ")}
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
              label="Cómo piensa y conversa"
              htmlFor="phone_agent_persona"
              hint="Define su criterio, ritmo, vocabulario y forma de escuchar, preguntar, explicar y cerrar."
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

            <div className="rounded-xl border border-brand-200 bg-brand-50/40 p-4 dark:border-brand-900 dark:bg-brand-950/20">
              <p className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                Identidad operativa
              </p>
              <p className="mt-1 text-xs text-slate-600 dark:text-slate-400">
                Su trabajo, alcance, límites y criterio de cierre quedan congelados en cada
                llamada.
              </p>
              <div className="mt-4 space-y-4">
                <Field label="Función y misión" htmlFor="phone_agent_mission">
                  <Textarea
                    id="phone_agent_mission"
                    value={form.operating_profile.funcion_y_mision}
                    onChange={(event) =>
                      setForm({
                        ...form,
                        operating_profile: {
                          ...form.operating_profile,
                          funcion_y_mision: event.target.value,
                        },
                      })
                    }
                    placeholder="Ser la asesora comercial que entiende la necesidad y acuerda el siguiente paso."
                    maxLength={2000}
                    required
                  />
                </Field>
                <Field
                  label="Problemas que sí puede resolver"
                  htmlFor="phone_agent_capabilities"
                >
                  <Textarea
                    id="phone_agent_capabilities"
                    value={form.operating_profile.capabilities}
                    onChange={(event) =>
                      setForm({
                        ...form,
                        operating_profile: {
                          ...form.operating_profile,
                          capabilities: event.target.value,
                        },
                      })
                    }
                    placeholder="Descubrir necesidades, responder preguntas autorizadas y coordinar demostraciones."
                    maxLength={4000}
                    required
                  />
                </Field>
                <Field
                  label="Problemas que no puede resolver"
                  htmlFor="phone_agent_out_of_scope"
                >
                  <Textarea
                    id="phone_agent_out_of_scope"
                    value={form.operating_profile.out_of_scope}
                    onChange={(event) =>
                      setForm({
                        ...form,
                        operating_profile: {
                          ...form.operating_profile,
                          out_of_scope: event.target.value,
                        },
                      })
                    }
                    placeholder="Asesoría legal, soporte técnico profundo y decisiones comerciales no documentadas."
                    maxLength={4000}
                    required
                  />
                </Field>
                <Field
                  label="Acciones que sí puede realizar"
                  htmlFor="phone_agent_allowed_actions"
                >
                  <Textarea
                    id="phone_agent_allowed_actions"
                    value={form.operating_profile.allowed_actions}
                    onChange={(event) =>
                      setForm({
                        ...form,
                        operating_profile: {
                          ...form.operating_profile,
                          allowed_actions: event.target.value,
                        },
                      })
                    }
                    placeholder="Preguntar, explicar información autorizada, coordinar una reunión y tomar un recado."
                    maxLength={4000}
                    required
                  />
                </Field>
                <Field label="Lo que nunca debe hacer" htmlFor="phone_agent_prohibitions">
                  <Textarea
                    id="phone_agent_prohibitions"
                    value={form.operating_profile.prohibited_actions}
                    onChange={(event) =>
                      setForm({
                        ...form,
                        operating_profile: {
                          ...form.operating_profile,
                          prohibited_actions: event.target.value,
                        },
                      })
                    }
                    placeholder="No inventar precios, no garantizar resultados y no aceptar condiciones especiales."
                    maxLength={4000}
                    required
                  />
                </Field>
                <Field label="Cuándo debe pedir ayuda" htmlFor="phone_agent_escalation">
                  <Textarea
                    id="phone_agent_escalation"
                    value={form.operating_profile.escalation_rules}
                    onChange={(event) =>
                      setForm({
                        ...form,
                        operating_profile: {
                          ...form.operating_profile,
                          escalation_rules: event.target.value,
                        },
                      })
                    }
                    placeholder="Escalar temas legales, precios especiales o preguntas sin respuesta autorizada."
                    maxLength={3000}
                  />
                </Field>
                <Field label="Cómo sabe que terminó bien" htmlFor="phone_agent_success">
                  <Textarea
                    id="phone_agent_success"
                    value={form.operating_profile.success_criteria}
                    onChange={(event) =>
                      setForm({
                        ...form,
                        operating_profile: {
                          ...form.operating_profile,
                          success_criteria: event.target.value,
                        },
                      })
                    }
                    placeholder="La necesidad quedó clara y se acordó una reunión o un seguimiento."
                    maxLength={2000}
                  />
                </Field>
              </div>
            </div>

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

            <Field
              label="Voz de este agente"
              htmlFor="phone_agent_voice"
              hint="Usará una voz de tu cuenta de ElevenLabs. Si no eliges una, usa la voz predeterminada."
            >
              <Select
                id="phone_agent_voice"
                value={form.voice_id}
                onChange={(e) => setForm({ ...form, voice_id: e.target.value })}
              >
                <option value="">Voz predeterminada</option>
                {voices.map((voice) => (
                  <option key={voice.voice_id} value={voice.voice_id}>
                    {voice.nombre}
                  </option>
                ))}
              </Select>
            </Field>

            <div className="rounded-xl border border-slate-200 p-4 dark:border-slate-700">
              <p className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                En qué llamadas participa
              </p>
              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                <Checkbox
                  checked={form.handles_outbound}
                  onChange={(event) =>
                    setForm({
                      ...form,
                      handles_outbound: event.target.checked,
                      is_default: event.target.checked ? form.is_default : false,
                    })
                  }
                  label="Puede realizar llamadas"
                />
                <Checkbox
                  checked={form.handles_inbound}
                  onChange={(event) =>
                    setForm({
                      ...form,
                      handles_inbound: event.target.checked,
                      is_inbound_default: event.target.checked
                        ? form.is_inbound_default
                        : false,
                    })
                  }
                  label="Puede recibir llamadas"
                />
                {form.handles_outbound && (
                  <Checkbox
                    checked={form.is_default}
                    onChange={(event) =>
                      setForm({ ...form, is_default: event.target.checked })
                    }
                    label="Predeterminado para llamar"
                  />
                )}
                {form.handles_inbound && (
                  <Checkbox
                    checked={form.is_inbound_default}
                    onChange={(event) =>
                      setForm({
                        ...form,
                        is_inbound_default: event.target.checked,
                      })
                    }
                    label="Predeterminado para recibir"
                  />
                )}
              </div>
            </div>

            <Field
              label="Información que este agente puede usar y decir"
              htmlFor="phone_agent_knowledge"
              hint="Contexto autorizado para terceros: producto, precios, horarios, condiciones o preguntas frecuentes. Edecan no comparte el resto de tu memoria."
            >
              <Textarea
                id="phone_agent_knowledge"
                value={form.knowledge_context}
                onChange={(e) => setForm({ ...form, knowledge_context: e.target.value })}
                placeholder="Ofrecemos una demostración de 20 minutos. El plan inicial cuesta…"
                maxLength={6000}
              />
            </Field>

            <Field
              label="Información que debe obtener"
              htmlFor="phone_agent_required_information"
              hint="Qué datos o respuestas debe preguntar antes de cerrar la llamada."
            >
              <Textarea
                id="phone_agent_required_information"
                value={form.required_information}
                onChange={(e) => setForm({ ...form, required_information: e.target.value })}
                placeholder="Necesidad principal, presupuesto, fecha estimada y próximo paso."
                maxLength={3000}
              />
            </Field>

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
              Puedes guardar hasta 20 agentes. Cambiar una plantilla afecta solo llamadas futuras. Cada borrador conserva la versión que revisaste, y ninguna plantilla puede saltarse el consentimiento ni la confirmación final.
            </p>
          </form>
        </div>
      </CardBody>
    </Card>
  );
}
