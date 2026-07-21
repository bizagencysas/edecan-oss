"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { BellIcon, CheckIcon, PhoneIcon, RocketIcon, ZapIcon } from "@/components/icons";
import { Alert, Badge, Button, Card, CardBody, CardHeader, EmptyState, PageHeader, Spinner } from "@/components/ui";
import { cancelPhoneCall, confirmPhoneCall, listPhoneCalls, listReminders } from "@/lib/api";
import { FLAG_AUTOMATIONS_RULES, listAutomations, type Automation } from "@/lib/api-automatizaciones";
import { FLAG_AGENTS_MISSIONS, listMissions, type Mission } from "@/lib/api-misiones";
import { buildActivityOverview, type ActivityEntry } from "@/lib/activity";
import { useAuth } from "@/lib/auth-context";
import { formatDateTime } from "@/lib/format";
import { FLAG_VOICE_TELEPHONY, type PhoneCall, type Reminder } from "@/lib/types";

const TONE_VARIANT = {
  attention: "warning",
  error: "danger",
  active: "brand",
  scheduled: "neutral",
  complete: "success",
} as const;

export default function ActividadPage() {
  const { me } = useAuth();
  const canMissions = Boolean(me?.flags?.[FLAG_AGENTS_MISSIONS]);
  const canAutomations = Boolean(me?.flags?.[FLAG_AUTOMATIONS_RULES]);
  const canPhone = Boolean(me?.flags?.[FLAG_VOICE_TELEPHONY]);
  const [reminders, setReminders] = useState<Reminder[]>([]);
  const [missions, setMissions] = useState<Mission[]>([]);
  const [automations, setAutomations] = useState<Automation[]>([]);
  const [calls, setCalls] = useState<PhoneCall[]>([]);
  const [loading, setLoading] = useState(true);
  const [warning, setWarning] = useState<string | null>(null);
  const [callActionId, setCallActionId] = useState<string | null>(null);
  const loadGenerationRef = useRef(0);

  const load = useCallback(async () => {
    const generation = ++loadGenerationRef.current;
    setLoading(true);
    const requests: Array<Promise<unknown>> = [listReminders()];
    if (canMissions) requests.push(listMissions());
    if (canAutomations) requests.push(listAutomations());
    if (canPhone) requests.push(listPhoneCalls());

    const results = await Promise.allSettled(requests);
    let index = 0;
    const reminderResult = results[index++];
    const missionResult = canMissions ? results[index++] : undefined;
    const automationResult = canAutomations ? results[index] : undefined;
    if (canAutomations) index += 1;
    const callsResult = canPhone ? results[index] : undefined;

    // React StrictMode y el botón Actualizar pueden solapar lecturas. Solo la
    // más nueva puede decidir lo que se muestra; una respuesta vieja o
    // cancelada no debe convertir un estado sano en una falsa advertencia.
    if (generation !== loadGenerationRef.current) return;

    if (reminderResult?.status === "fulfilled") setReminders(reminderResult.value as Reminder[]);
    if (missionResult?.status === "fulfilled") setMissions(missionResult.value as Mission[]);
    if (automationResult?.status === "fulfilled") setAutomations(automationResult.value as Automation[]);
    if (callsResult?.status === "fulfilled") setCalls(callsResult.value as PhoneCall[]);

    const failed = results.filter((result) => result.status === "rejected").length;
    setWarning(failed ? "Parte de la actividad no pudo actualizarse. Puedes volver a intentarlo sin perder lo ya cargado." : null);
    setLoading(false);
  }, [canAutomations, canMissions, canPhone]);

  useEffect(() => {
    void load();
  }, [load]);

  const overview = useMemo(
    () => buildActivityOverview({ reminders, missions, automations, calls }),
    [automations, calls, missions, reminders],
  );
  const isEmpty = overview.attention.length + overview.current.length + overview.recent.length === 0;

  async function handleConfirmCall(callId: string) {
    const call = calls.find((candidate) => candidate.id === callId);
    if (!call) return;
    setCallActionId(callId);
    setWarning(null);
    try {
      const updated = await confirmPhoneCall(call);
      setCalls((current) => current.map((item) => item.id === updated.id ? updated : item));
    } catch (error) {
      setWarning(error instanceof Error ? error.message : "No se pudo iniciar la llamada.");
    } finally {
      setCallActionId(null);
    }
  }

  async function handleCancelCall(callId: string) {
    setCallActionId(callId);
    setWarning(null);
    try {
      const updated = await cancelPhoneCall(callId);
      setCalls((current) => current.map((item) => item.id === updated.id ? updated : item));
    } catch (error) {
      setWarning(error instanceof Error ? error.message : "No se pudo cancelar la llamada.");
    } finally {
      setCallActionId(null);
    }
  }

  return (
    <div>
      <PageHeader
        title="Actividad"
        description="Lo que Edecan está haciendo, lo próximo y cualquier decisión que necesite de ti."
        actions={<Button variant="secondary" size="sm" onClick={() => void load()} loading={loading}>Actualizar</Button>}
      />

      {warning && <div className="mb-4"><Alert variant="info">{warning}</Alert></div>}

      {loading && isEmpty ? (
        <div className="flex justify-center py-16"><Spinner className="h-6 w-6 text-slate-400" /></div>
      ) : isEmpty ? (
        <Card>
          <CardBody>
            <EmptyState
              title="Todo está al día"
              description="Pídele algo a Edecan en lenguaje natural y su avance aparecerá aquí."
              action={
                <Link
                  href="/app"
                  className="inline-flex items-center justify-center rounded-lg bg-brand-600 px-3.5 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-700"
                >
                  Hablar con Edecan
                </Link>
              }
            />
          </CardBody>
        </Card>
      ) : (
        <div className="space-y-6">
          {overview.attention.length > 0 && (
            <ActivitySection
              title="Necesita tu atención"
              description="Edecan se detuvo antes de una decisión importante o encontró un problema."
              items={overview.attention}
              callActionId={callActionId}
              onConfirmCall={(id) => void handleConfirmCall(id)}
              onCancelCall={(id) => void handleCancelCall(id)}
            />
          )}
          <ActivitySection
            title="En curso y próximo"
            description="Trabajo activo, recordatorios y automatizaciones programadas."
            items={overview.current}
            empty="No hay nada en curso ahora."
          />
          {overview.recent.length > 0 && (
            <ActivitySection title="Completado recientemente" items={overview.recent} />
          )}
        </div>
      )}

      <div className="mt-6 grid grid-cols-1 gap-3 sm:grid-cols-3">
        {canMissions && <QuickLink href="/app/misiones" icon={<RocketIcon className="h-4 w-4" />} label="Trabajo delegado" />}
        <QuickLink href="/app/recordatorios" icon={<BellIcon className="h-4 w-4" />} label="Recordatorios" />
        {canAutomations && <QuickLink href="/app/automatizaciones" icon={<ZapIcon className="h-4 w-4" />} label="Rutinas" />}
      </div>
    </div>
  );
}

function ActivitySection({
  title,
  description,
  items,
  empty,
  callActionId,
  onConfirmCall,
  onCancelCall,
}: {
  title: string;
  description?: string;
  items: ActivityEntry[];
  empty?: string;
  callActionId?: string | null;
  onConfirmCall?: (id: string) => void;
  onCancelCall?: (id: string) => void;
}) {
  return (
    <Card>
      <CardHeader title={title} description={description} />
      <CardBody>
        {items.length === 0 ? (
          <p className="py-2 text-sm text-slate-500 dark:text-slate-400">{empty}</p>
        ) : (
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {items.map((item) => (
              <li key={item.id}>
                <div className="flex items-center gap-3 py-3 first:pt-0 last:pb-0">
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-300">
                    {item.tone === "complete" ? <CheckIcon className="h-4 w-4" /> : item.kind === "phone" ? <PhoneIcon className="h-4 w-4" /> : item.kind === "reminder" ? <BellIcon className="h-4 w-4" /> : item.kind === "automation" ? <ZapIcon className="h-4 w-4" /> : <RocketIcon className="h-4 w-4" />}
                  </span>
                  <span className="min-w-0 flex-1">
                    <Link href={item.href} className="block truncate text-sm font-medium text-slate-800 hover:text-brand-700 dark:text-slate-100">{item.title}</Link>
                    <span className="block truncate text-xs text-slate-500 dark:text-slate-400">
                      {item.detail}{item.timestamp ? ` · ${formatDateTime(item.timestamp)}` : ""}
                    </span>
                    {item.phoneConfirmation && (
                      <span className="mt-2 flex flex-wrap gap-2">
                        <Button
                          size="sm"
                          onClick={() => onConfirmCall?.(item.phoneConfirmation!.callId)}
                          loading={callActionId === item.phoneConfirmation.callId}
                        >
                          Llamar ahora
                        </Button>
                        <Button
                          size="sm"
                          variant="secondary"
                          onClick={() => onCancelCall?.(item.phoneConfirmation!.callId)}
                          disabled={callActionId === item.phoneConfirmation.callId}
                        >
                          Cancelar
                        </Button>
                      </span>
                    )}
                  </span>
                  <Badge variant={TONE_VARIANT[item.tone]}>{item.statusLabel}</Badge>
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}

function QuickLink({ href, icon, label }: { href: string; icon: React.ReactNode; label: string }) {
  return (
    <Link href={href} className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm font-medium text-slate-700 transition-colors hover:border-brand-200 hover:text-brand-700 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-200">
      {icon}{label}
    </Link>
  );
}
