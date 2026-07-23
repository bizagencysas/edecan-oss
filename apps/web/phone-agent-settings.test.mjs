import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

function source(path) {
  return readFileSync(new URL(path, import.meta.url), "utf8");
}

test("Ajustes ofrece agentes de llamadas reutilizables con lenguaje humano", () => {
  const settings = source("./src/components/configuracion/PhoneAgentTemplatesSettings.tsx");
  const page = source("./src/app/(app)/app/ajustes/page.tsx");

  assert.match(page, /<PhoneAgentTemplatesSettings \/>/);
  assert.match(settings, /Asistente personal/);
  assert.match(settings, /Ventas consultivas/);
  assert.match(settings, /Seguimiento y citas/);
  assert.match(settings, /hasta 20 identidades separadas/);
  assert.match(settings, /atender llamadas entrantes/);
  assert.match(settings, /Qué debe lograr normalmente/);
  assert.match(settings, /Información que este agente puede usar y decir/);
  assert.match(settings, /Edecan no comparte el resto de tu memoria/);
  assert.match(settings, /Información que debe obtener/);
  assert.match(settings, /ninguna plantilla puede saltarse el consentimiento ni la confirmación final/);
});

test("Llamadas tiene diagnóstico, configuración guiada y dos confirmaciones", () => {
  const calls = source("./src/app/(app)/app/llamadas/page.tsx");
  const nav = source("./src/components/layout/nav-items.ts");
  const connectors = source("./src/components/configuracion/ConnectorsSettings.tsx");

  assert.match(nav, /href: "\/app\/llamadas",\s*label: "Llamadas"/);
  assert.match(calls, /Qué falta para llamar/);
  assert.match(calls, /<PhoneAgentTemplatesSettings/);
  assert.match(calls, /Preparar llamada/);
  assert.match(calls, /Confirmación final/);
  assert.match(calls, /Llamar ahora/);
  assert.match(calls, /confirmDestination/);
  assert.match(calls, /confirmGoal/);
  assert.match(connectors, /id="connector-twilio"/);
});

test("el cliente web usa el CRUD tenant-scoped de plantillas", () => {
  const api = source("./src/lib/api.ts");

  assert.match(api, /listPhoneAgentTemplates/);
  assert.match(api, /createPhoneAgentTemplate/);
  assert.match(api, /updatePhoneAgentTemplate/);
  assert.match(api, /deletePhoneAgentTemplate/);
  assert.match(api, /preparePhoneCall/);
  assert.match(api, /"\/v1\/phone\/calls\/prepare"/);
  assert.match(api, /"\/v1\/phone\/agent-templates"/);
});
