"use client";

/**
 * Formulario de creación de una automatización (`ROADMAP_V2.md` §7.10,
 * WP-V2-07). Presets de `rrule` (diario/semanal/mensual) para no exigirle
 * RFC 5545 al usuario, más una opción "Personalizada" para quien sepa
 * escribir la suya. El disparador webhook no trae formulario propio de
 * `rrule` — al crearlo, el backend genera el secreto y lo devuelve UNA sola
 * vez (ver `AutomationDetail`/`page.tsx`, que lo muestra tras crear).
 */

import { useState } from "react";

import { Button, Field, Input, Select, Textarea } from "@/components/ui";
import type { CreateAutomationInput } from "@/lib/api-automatizaciones";

type PresetKey = "diario" | "semanal" | "mensual" | "personalizada";

const PRESETS: Record<Exclude<PresetKey, "personalizada">, { label: string; rrule: string }> = {
  diario: { label: "Diario a las 9:00", rrule: "FREQ=DAILY;BYHOUR=9;BYMINUTE=0" },
  semanal: { label: "Semanal (lunes) a las 9:00", rrule: "FREQ=WEEKLY;BYDAY=MO;BYHOUR=9;BYMINUTE=0" },
  mensual: { label: "Mensual (día 1) a las 9:00", rrule: "FREQ=MONTHLY;BYMONTHDAY=1;BYHOUR=9;BYMINUTE=0" },
};

export function AutomationForm({
  onCreate,
  creating,
}: {
  onCreate: (input: CreateAutomationInput) => Promise<void>;
  creating: boolean;
}) {
  const [nombre, setNombre] = useState("");
  const [tipoDisparador, setTipoDisparador] = useState<"schedule" | "webhook">("schedule");
  const [preset, setPreset] = useState<PresetKey>("diario");
  const [rruleManual, setRruleManual] = useState("FREQ=DAILY;BYHOUR=9;BYMINUTE=0");
  const [instruccion, setInstruccion] = useState("");

  const rrule = preset === "personalizada" ? rruleManual : PRESETS[preset].rrule;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!nombre.trim() || !instruccion.trim()) return;
    if (tipoDisparador === "schedule" && !rrule.trim()) return;

    await onCreate({
      nombre: nombre.trim(),
      trigger:
        tipoDisparador === "schedule" ? { kind: "schedule", rrule: rrule.trim() } : { kind: "webhook" },
      accion: { instruccion: instruccion.trim() },
    });
    setNombre("");
    setInstruccion("");
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <Field label="Nombre" htmlFor="automation-nombre">
        <Input
          id="automation-nombre"
          value={nombre}
          onChange={(e) => setNombre(e.target.value)}
          placeholder="Reporte de ventas diario"
        />
      </Field>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field label="Disparador" htmlFor="automation-tipo">
          <Select
            id="automation-tipo"
            value={tipoDisparador}
            onChange={(e) => setTipoDisparador(e.target.value as "schedule" | "webhook")}
          >
            <option value="schedule">Agenda (rrule)</option>
            <option value="webhook">Webhook entrante</option>
          </Select>
        </Field>

        {tipoDisparador === "schedule" && (
          <Field label="Frecuencia" htmlFor="automation-preset">
            <Select
              id="automation-preset"
              value={preset}
              onChange={(e) => setPreset(e.target.value as PresetKey)}
            >
              <option value="diario">{PRESETS.diario.label}</option>
              <option value="semanal">{PRESETS.semanal.label}</option>
              <option value="mensual">{PRESETS.mensual.label}</option>
              <option value="personalizada">Personalizada (rrule RFC 5545)</option>
            </Select>
          </Field>
        )}
      </div>

      {tipoDisparador === "schedule" && preset === "personalizada" && (
        <Field
          label="Regla de recurrencia (rrule)"
          htmlFor="automation-rrule"
          hint="RFC 5545, ej. 'FREQ=WEEKLY;BYDAY=MO,WE,FR;BYHOUR=8'."
        >
          <Input
            id="automation-rrule"
            value={rruleManual}
            onChange={(e) => setRruleManual(e.target.value)}
            placeholder="FREQ=DAILY;BYHOUR=9"
          />
        </Field>
      )}

      {tipoDisparador === "webhook" && (
        <p className="text-xs text-slate-500 dark:text-slate-400">
          Al crearla se genera una URL y un secreto de un solo uso — se muestran una vez, justo
          después de crear.
        </p>
      )}

      <Field
        label="Instrucción para el agente"
        htmlFor="automation-instruccion"
        hint="Lo que el agente debe hacer en cada corrida, en modo headless (sin herramientas peligrosas)."
      >
        <Textarea
          id="automation-instruccion"
          value={instruccion}
          onChange={(e) => setInstruccion(e.target.value)}
          placeholder="Resume las ventas de hoy y guárdalas como una nota."
        />
      </Field>

      <div className="flex justify-end">
        <Button type="submit" loading={creating}>
          Crear automatización
        </Button>
      </div>
    </form>
  );
}
