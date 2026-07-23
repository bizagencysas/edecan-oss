"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";

import { PhoneAgentTemplatesSettings } from "@/components/configuracion/PhoneAgentTemplatesSettings";
import { CheckIcon, PhoneIcon } from "@/components/icons";
import {
  Alert,
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  Checkbox,
  EmptyState,
  Field,
  Input,
  PageHeader,
  Select,
  Spinner,
  Textarea,
} from "@/components/ui";
import {
  confirmPhoneCall,
  grantConsent,
  listConnectors,
  listPhoneAgentTemplates,
  listPhoneCalls,
  preparePhoneCall,
  setupIncomingCalls,
} from "@/lib/api";
import { getCredentials } from "@/lib/api-configuracion";
import type { PhoneAgentTemplate, PhoneCall } from "@/lib/types";

const TWILIO_KEY = "twilio";

function statusLabel(status: PhoneCall["status"]): string {
  const labels: Record<PhoneCall["status"], string> = {
    draft: "Por confirmar",
    confirmed: "Confirmada",
    queued: "En cola",
    ringing: "Sonando",
    in_progress: "En curso",
    completed: "Completada",
    failed: "Fallida",
    busy: "Ocupado",
    no_answer: "Sin respuesta",
    cancelled: "Cancelada",
  };
  return labels[status];
}

function isActive(status: PhoneCall["status"]): boolean {
  return ["draft", "confirmed", "queued", "ringing", "in_progress"].includes(status);
}

export default function LlamadasPage() {
  const [templates, setTemplates] = useState<PhoneAgentTemplate[]>([]);
  const [calls, setCalls] = useState<PhoneCall[]>([]);
  const [twilioNumber, setTwilioNumber] = useState<string | null>(null);
  const [llmReady, setLlmReady] = useState(false);
  const [ttsReady, setTtsReady] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<"prepare" | "confirm" | "incoming" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [draft, setDraft] = useState<PhoneCall | null>(null);

  const [agentId, setAgentId] = useState("");
  const [toE164, setToE164] = useState("");
  const [recipientName, setRecipientName] = useState("");
  const [goal, setGoal] = useState("");
  const [hasConsent, setHasConsent] = useState(false);
  const [consentSource, setConsentSource] = useState("");
  const [confirmDestination, setConfirmDestination] = useState(false);
  const [confirmRecipient, setConfirmRecipient] = useState(false);
  const [confirmGoal, setConfirmGoal] = useState(false);
  const [confirmAgent, setConfirmAgent] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [connectors, agents, recentCalls, credentials] = await Promise.all([
        listConnectors(),
        listPhoneAgentTemplates(),
        listPhoneCalls(),
        getCredentials(),
      ]);
      const twilio = connectors.find((connector) => connector.key === TWILIO_KEY);
      const account = twilio?.accounts.find((item) => item.status === "active") ?? twilio?.accounts[0];
      setTwilioNumber(account?.external_account_id ?? null);
      setTemplates(agents);
      setCalls(recentCalls);
      setLlmReady(Boolean(credentials.llm));
      setTtsReady(Boolean(credentials.voice_tts));
      const outboundAgents = agents.filter((item) => item.handles_outbound);
      if (!agentId && outboundAgents.length > 0) {
        const preferred = outboundAgents.find((item) => item.is_default) ?? outboundAgents[0];
        setAgentId(preferred.id);
        setGoal(preferred.default_goal);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo revisar el sistema de llamadas.");
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    void load();
    // El estado inicial del agente se resuelve dentro de la carga.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selectedAgent = useMemo(
    () => templates.find((template) => template.id === agentId) ?? null,
    [agentId, templates],
  );
  const outboundAgents = useMemo(
    () => templates.filter((template) => template.handles_outbound),
    [templates],
  );
  const inboundAgent = useMemo(
    () => templates.find((template) => template.is_inbound_default) ?? null,
    [templates],
  );
  const setupReady = Boolean(twilioNumber && llmReady && outboundAgents.length > 0);

  function chooseAgent(nextId: string) {
    setAgentId(nextId);
    const next = templates.find((template) => template.id === nextId);
    if (next) setGoal(next.default_goal);
    setDraft(null);
  }

  async function prepare(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSuccess(null);
    setBusy("prepare");
    try {
      if (!hasConsent || !consentSource.trim()) {
        throw new Error(
          "Confirma que esa persona autorizó la llamada y explica cómo obtuviste el permiso.",
        );
      }
      await grantConsent({
        phone_e164: toE164,
        kind: "voice",
        source: consentSource.trim(),
      });
      const prepared = await preparePhoneCall({
        to_e164: toE164,
        recipient_name: recipientName.trim(),
        goal: goal.trim(),
        agent_template_id: agentId,
      });
      setDraft(prepared);
      setConfirmDestination(false);
      setConfirmRecipient(false);
      setConfirmGoal(false);
      setConfirmAgent(false);
      setSuccess(
        "Borrador listo. Revisa la persona, el número, el agente y el objetivo antes de llamar.",
      );
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo preparar la llamada.");
    } finally {
      setBusy(null);
    }
  }

  async function confirm() {
    if (
      !draft ||
      !confirmDestination ||
      !confirmRecipient ||
      !confirmGoal ||
      !confirmAgent
    ) {
      return;
    }
    setBusy("confirm");
    setError(null);
    setSuccess(null);
    try {
      const sent = await confirmPhoneCall(draft);
      setDraft(sent);
      setSuccess("Twilio recibió la llamada. Puedes seguir su estado aquí y en Actividad.");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo iniciar la llamada.");
    } finally {
      setBusy(null);
    }
  }

  async function configureIncoming() {
    setBusy("incoming");
    setError(null);
    setSuccess(null);
    try {
      const result = await setupIncomingCalls();
      setSuccess(
        `Listo. Las llamadas que entren a ${result.phone_number} serán atendidas por ${result.agent_name}.`,
      );
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "No se pudo configurar la recepción de llamadas.",
      );
    } finally {
      setBusy(null);
    }
  }

  return (
    <div>
      <PageHeader
        title="Llamadas"
        description="Configura agentes que atienden o llaman por ti, con objetivo, contexto y resumen al terminar."
      />

      {error && <div className="mb-4"><Alert variant="error">{error}</Alert></div>}
      {success && <div className="mb-4"><Alert variant="success">{success}</Alert></div>}

      {loading ? (
        <div className="flex justify-center py-16">
          <Spinner className="h-6 w-6 text-slate-400" />
        </div>
      ) : (
        <div className="space-y-6">
          <Card>
            <CardHeader
              title="Qué falta para llamar"
              description="Edecan revisa la instalación y te lleva directamente a cada dato pendiente."
              actions={<Badge variant={setupReady ? "success" : "neutral"}>{setupReady ? "Listo" : "Falta configurar"}</Badge>}
            />
            <CardBody>
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <SetupStatus
                  ready={Boolean(twilioNumber)}
                  title="Número"
                  detail={twilioNumber ?? "Conecta tu número de Twilio"}
                  href="/app/conectores#connector-twilio"
                />
                <SetupStatus
                  ready={llmReady}
                  title="Inteligencia"
                  detail={llmReady ? "Modelo conectado" : "Elige Claude, Codex, Ollama u otro"}
                  href="/app/ajustes#conexiones"
                />
                <SetupStatus
                  ready={outboundAgents.length > 0}
                  title="Agente"
                  detail={
                    outboundAgents.length > 0
                      ? `${outboundAgents.length} disponible(s) para llamar`
                      : "Configura una identidad para llamadas salientes"
                  }
                  href="#agentes"
                />
                <SetupStatus
                  ready={ttsReady}
                  optional
                  title="Voz premium"
                  detail={ttsReady ? "ElevenLabs conectado" : "Opcional por ahora"}
                  href="/app/ajustes#conexiones"
                />
              </div>
            </CardBody>
          </Card>

          <section id="agentes" className="scroll-mt-6">
            <PhoneAgentTemplatesSettings onChanged={() => void load()} />
          </section>

          <Card>
            <CardHeader
              title="Recibir llamadas"
              description="El agente entrante predeterminado atenderá con su propia identidad, voz y límites."
            />
            <CardBody>
              <div className="flex flex-wrap items-center justify-between gap-4">
                <div>
                  <p className="text-sm font-medium text-slate-900 dark:text-slate-100">
                    {twilioNumber ?? "Todavía no hay un número conectado"}
                  </p>
                  <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
                    {inboundAgent
                      ? `${inboundAgent.name} se presenta como ${inboundAgent.agent_name}.`
                      : "Elige primero un agente predeterminado para recibir llamadas."}
                  </p>
                </div>
                <Button
                  type="button"
                  variant="secondary"
                  loading={busy === "incoming"}
                  disabled={!twilioNumber || !inboundAgent}
                  onClick={() => void configureIncoming()}
                >
                  Configurar o reparar recepción
                </Button>
              </div>
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              title="Probar una llamada"
              description="Primero se crea un borrador. La llamada real solo sale después de una segunda confirmación."
            />
            <CardBody>
              {!setupReady ? (
                <Alert variant="info">
                  Completa el número, la inteligencia y al menos un agente. Edecan mantendrá este formulario bloqueado hasta que pueda hacer una llamada real.
                </Alert>
              ) : (
                <form onSubmit={prepare} className="space-y-4">
                  <div className="grid gap-4 sm:grid-cols-2">
                    <Field label="Agente que llamará" htmlFor="call-agent">
                      <Select id="call-agent" value={agentId} onChange={(event) => chooseAgent(event.target.value)} required>
                        {outboundAgents.map((template) => (
                          <option key={template.id} value={template.id}>
                            {template.name} · {template.agent_name}
                          </option>
                        ))}
                      </Select>
                    </Field>
                    <Field label="Número de la persona" htmlFor="call-destination" hint="Formato internacional, por ejemplo +573001234567.">
                      <Input
                        id="call-destination"
                        value={toE164}
                        onChange={(event) => { setToE164(event.target.value); setDraft(null); }}
                        placeholder="+573001234567"
                        autoComplete="tel"
                        required
                      />
                    </Field>
                  </div>
                  <Field
                    label="A quién pertenece ese número"
                    htmlFor="call-recipient"
                    hint="Nombre de la persona o empresa. Edecan nunca lo deduce solo por el teléfono."
                  >
                    <Input
                      id="call-recipient"
                      value={recipientName}
                      onChange={(event) => {
                        setRecipientName(event.target.value);
                        setDraft(null);
                      }}
                      placeholder="María Pérez"
                      autoComplete="name"
                      maxLength={160}
                      required
                    />
                  </Field>
                  <Field label="Qué debe conseguir en esta llamada" htmlFor="call-goal">
                    <Textarea
                      id="call-goal"
                      value={goal}
                      onChange={(event) => { setGoal(event.target.value); setDraft(null); }}
                      placeholder="Confirmar la cita y preguntar si necesita cambiar la hora."
                      maxLength={500}
                      required
                    />
                  </Field>
                  <div className="rounded-xl border border-slate-200 p-4 dark:border-slate-700">
                    <Checkbox
                      checked={hasConsent}
                      onChange={(event) => setHasConsent(event.target.checked)}
                      label="Confirmo que esta persona autorizó recibir esta llamada"
                    />
                    {hasConsent && (
                      <div className="mt-3">
                        <Field label="Cómo obtuviste su autorización" htmlFor="call-consent-source">
                          <Input
                            id="call-consent-source"
                            value={consentSource}
                            onChange={(event) => setConsentSource(event.target.value)}
                            placeholder="Formulario de contacto del 23 de julio"
                            required
                          />
                        </Field>
                      </div>
                    )}
                  </div>
                  <Button type="submit" loading={busy === "prepare"} disabled={!hasConsent}>
                    Preparar llamada
                  </Button>
                </form>
              )}

              {draft && draft.status === "draft" && (
                <div className="mt-5 rounded-xl border-2 border-brand-300 bg-brand-50/60 p-4 dark:border-brand-800 dark:bg-brand-950/20">
                  <p className="text-sm font-semibold text-slate-900 dark:text-slate-100">Confirmación final</p>
                  <dl className="mt-3 grid gap-3 text-sm sm:grid-cols-2">
                    <div><dt className="text-slate-500">Persona</dt><dd className="font-medium">{draft.recipient_name}</dd></div>
                    <div><dt className="text-slate-500">Número</dt><dd className="font-medium">{draft.to_e164}</dd></div>
                    <div>
                      <dt className="text-slate-500">Agente exacto</dt>
                      <dd className="font-medium">
                        {draft.agent?.template_name ?? selectedAgent?.name} ·{" "}
                        {draft.agent?.name ?? selectedAgent?.agent_name}
                      </dd>
                    </div>
                    <div className="sm:col-span-2"><dt className="text-slate-500">Objetivo</dt><dd className="font-medium">{draft.goal}</dd></div>
                  </dl>
                  <div className="mt-4 space-y-2">
                    <Checkbox
                      checked={confirmRecipient}
                      onChange={(event) => setConfirmRecipient(event.target.checked)}
                      label={`Confirmo que el número pertenece a ${draft.recipient_name}`}
                    />
                    <Checkbox
                      checked={confirmDestination}
                      onChange={(event) => setConfirmDestination(event.target.checked)}
                      label={`Revisé y confirmo el número ${draft.to_e164}`}
                    />
                    <Checkbox
                      checked={confirmAgent}
                      onChange={(event) => setConfirmAgent(event.target.checked)}
                      label={`Confirmo que debe llamar ${draft.agent?.template_name ?? "este agente"}`}
                    />
                    <Checkbox
                      checked={confirmGoal}
                      onChange={(event) => setConfirmGoal(event.target.checked)}
                      label="Revisé y confirmo el objetivo de esta llamada"
                    />
                  </div>
                  <Button
                    type="button"
                    className="mt-4"
                    loading={busy === "confirm"}
                    disabled={
                      !confirmDestination ||
                      !confirmRecipient ||
                      !confirmGoal ||
                      !confirmAgent
                    }
                    onClick={() => void confirm()}
                  >
                    Llamar ahora
                  </Button>
                </div>
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Llamadas recientes" description="Estado, agente y resumen real de Twilio." />
            <CardBody>
              {calls.length === 0 ? (
                <EmptyState title="Todavía no hay llamadas" description="La primera aparecerá aquí después de preparar el borrador." />
              ) : (
                <ul className="space-y-2">
                  {calls.slice(0, 12).map((call) => (
                    <li key={call.id} className="rounded-xl border border-slate-200 p-4 dark:border-slate-700">
                      <div className="flex flex-wrap items-start justify-between gap-2">
                        <div>
                          <p className="font-medium text-slate-900 dark:text-slate-100">
                            {call.direction === "incoming"
                              ? call.from_e164
                              : call.recipient_name
                                ? `${call.recipient_name} · ${call.to_e164}`
                                : call.to_e164}
                          </p>
                          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">{call.goal}</p>
                        </div>
                        <Badge variant={call.status === "completed" ? "success" : isActive(call.status) ? "brand" : "neutral"}>
                          {statusLabel(call.status)}
                        </Badge>
                      </div>
                      {call.agent?.name && <p className="mt-2 text-xs text-slate-500">Agente: {call.agent.name}</p>}
                      {call.summary?.key_points?.length ? (
                        <p className="mt-2 text-sm text-slate-700 dark:text-slate-300">
                          {call.summary.key_points.slice(0, 2).join(" · ")}
                        </p>
                      ) : null}
                      {call.error && <p className="mt-2 text-sm text-red-600 dark:text-red-400">{call.error}</p>}
                    </li>
                  ))}
                </ul>
              )}
            </CardBody>
          </Card>
        </div>
      )}
    </div>
  );
}

function SetupStatus({
  ready,
  optional = false,
  title,
  detail,
  href,
}: {
  ready: boolean;
  optional?: boolean;
  title: string;
  detail: string;
  href: string;
}) {
  return (
    <Link
      href={href}
      className="rounded-xl border border-slate-200 p-4 transition-colors hover:border-brand-300 hover:bg-brand-50/40 dark:border-slate-700 dark:hover:border-brand-800 dark:hover:bg-brand-950/20"
    >
      <div className="flex items-center gap-2">
        {ready ? (
          <span className="flex h-6 w-6 items-center justify-center rounded-full bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300">
            <CheckIcon className="h-3.5 w-3.5" />
          </span>
        ) : (
          <span className="flex h-6 w-6 items-center justify-center rounded-full bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-300">
            <PhoneIcon className="h-3.5 w-3.5" />
          </span>
        )}
        <p className="text-sm font-semibold text-slate-900 dark:text-slate-100">{title}</p>
      </div>
      <p className="mt-2 text-xs leading-5 text-slate-500 dark:text-slate-400">{detail}</p>
      {!ready && <p className="mt-2 text-xs font-medium text-brand-700 dark:text-brand-300">{optional ? "Configurar si quieres" : "Configurar ahora"}</p>}
    </Link>
  );
}
