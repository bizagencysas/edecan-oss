import assert from "node:assert/strict";
import test from "node:test";

import { buildActivityOverview } from "./src/lib/activity.ts";

const baseMission = {
  tenant_id: "tenant",
  user_id: "user",
  plan: null,
  resultado: null,
  presupuesto: {},
  error: null,
  created_at: "2026-07-20T10:00:00Z",
  updated_at: "2026-07-20T11:00:00Z",
};

test("reúne aprobaciones y recordatorios vencidos en atención", () => {
  const overview = buildActivityOverview({
    now: new Date("2026-07-20T12:00:00Z"),
    missions: [
      {
        ...baseMission,
        id: "mission-1",
        objetivo: "Enviar el informe",
        status: "waiting_confirmation",
      },
    ],
    reminders: [
      {
        id: "reminder-1",
        tenant_id: "tenant",
        user_id: "user",
        due_at: "2026-07-20T09:00:00Z",
        rrule: null,
        message: "Pagar el servicio",
        channel: "web",
        status: "pending",
        created_at: "2026-07-19T10:00:00Z",
        updated_at: "2026-07-19T10:00:00Z",
      },
    ],
    automations: [],
  });

  assert.equal(overview.attention.length, 2);
  assert.deepEqual(
    overview.attention.map((item) => item.statusLabel),
    ["Vencido", "Necesita aprobación"],
  );
});

test("muestra trabajo activo, próximos recordatorios y rutinas habilitadas", () => {
  const overview = buildActivityOverview({
    now: new Date("2026-07-20T12:00:00Z"),
    missions: [
      { ...baseMission, id: "mission-1", objetivo: "Investigar opciones", status: "running" },
    ],
    reminders: [
      {
        id: "reminder-1",
        tenant_id: "tenant",
        user_id: "user",
        due_at: "2026-07-20T14:00:00Z",
        rrule: null,
        message: "Llamar a Ana",
        channel: "web",
        status: "pending",
        created_at: "2026-07-20T10:00:00Z",
        updated_at: "2026-07-20T10:00:00Z",
      },
    ],
    automations: [
      {
        id: "automation-1",
        nombre: "Resumen diario",
        descripcion: "",
        trigger: { kind: "schedule", rrule: "FREQ=DAILY" },
        accion: { kind: "agent_instruction", instruccion: "Preparar el resumen" },
        enabled: true,
        next_run_at: "2026-07-21T08:00:00Z",
        last_run_at: null,
        created_at: "2026-07-20T10:00:00Z",
        updated_at: "2026-07-20T10:00:00Z",
      },
      {
        id: "automation-disabled",
        nombre: "No debe aparecer",
        descripcion: "",
        trigger: { kind: "schedule", rrule: "FREQ=DAILY" },
        accion: { kind: "agent_instruction", instruccion: "Nada" },
        enabled: false,
        next_run_at: null,
        last_run_at: null,
        created_at: "2026-07-20T10:00:00Z",
        updated_at: "2026-07-20T10:00:00Z",
      },
    ],
  });

  assert.equal(overview.current.length, 3);
  assert.deepEqual(overview.current.map((item) => item.kind), ["mission", "reminder", "automation"]);
  assert.equal(overview.attention.length, 0);
});

test("omite recordatorios cancelados y limita el historial reciente", () => {
  const missions = Array.from({ length: 12 }, (_, index) => ({
    ...baseMission,
    id: `mission-${index}`,
    objetivo: `Misión ${index}`,
    status: "done",
    updated_at: `2026-07-${String(index + 1).padStart(2, "0")}T11:00:00Z`,
  }));

  const overview = buildActivityOverview({
    missions,
    automations: [],
    reminders: [
      {
        id: "cancelled",
        tenant_id: "tenant",
        user_id: "user",
        due_at: "2026-07-20T14:00:00Z",
        rrule: null,
        message: "Cancelado",
        channel: "web",
        status: "cancelled",
        created_at: "2026-07-20T10:00:00Z",
        updated_at: "2026-07-20T10:00:00Z",
      },
    ],
  });

  assert.equal(overview.recent.length, 8);
  assert.equal(overview.recent[0].title, "Misión 11");
  assert.equal(overview.recent.some((item) => item.title === "Cancelado"), false);
});

test("integra llamadas pendientes, activas y terminadas en la misma actividad", () => {
  const common = {
    conversation_id: "conversation",
    direction: "outgoing",
    from_e164: "+573001111111",
    to_e164: "+573002222222",
    goal: "Confirmar la cita",
    confirmed_at: null,
    started_at: null,
    ended_at: null,
    duration_seconds: null,
    error: null,
    agent: null,
    summary: null,
    summary_generated_at: null,
    created_at: "2026-07-20T10:00:00Z",
    updated_at: "2026-07-20T10:00:00Z",
  };
  const overview = buildActivityOverview({
    missions: [],
    reminders: [],
    automations: [],
    calls: [
      { ...common, id: "draft", status: "draft" },
      { ...common, id: "active", status: "in_progress" },
      {
        ...common,
        id: "done",
        status: "completed",
        summary: { key_points: ["La cita quedó confirmada"] },
      },
    ],
  });
  assert.equal(overview.attention[0].statusLabel, "Confirma la llamada");
  assert.equal(overview.current[0].statusLabel, "En llamada");
  assert.equal(overview.recent[0].statusLabel, "Finalizada");
  assert.equal(overview.recent[0].detail, "La cita quedó confirmada");
});
