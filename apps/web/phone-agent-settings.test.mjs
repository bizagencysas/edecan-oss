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
  assert.match(settings, /Puede recibir llamadas/);
  assert.match(settings, /Cómo piensa y conversa/);
  assert.match(settings, /Problemas que sí puede resolver/);
  assert.match(settings, /Problemas que no puede resolver/);
  assert.match(settings, /Acciones que sí puede realizar/);
  assert.match(settings, /Lo que nunca debe hacer/);
  assert.match(settings, /Cuándo debe pedir ayuda/);
  assert.match(settings, /Cómo sabe que terminó bien/);
  assert.match(settings, /Qué debe lograr normalmente/);
  assert.match(settings, /Información que este agente puede usar y decir/);
  assert.match(settings, /Edecan no comparte el resto de tu memoria/);
  assert.match(settings, /Información que debe obtener/);
  assert.match(settings, /Voz de este agente/);
  assert.match(settings, /listVoces/);
  assert.match(settings, /ninguna plantilla puede saltarse el consentimiento ni la confirmación final/);
});

test("Llamadas tiene diagnóstico, configuración guiada y preflight completo", () => {
  const calls = source("./src/app/(app)/app/llamadas/page.tsx");
  const nav = source("./src/components/layout/nav-items.ts");
  const connectors = source("./src/components/configuracion/ConnectorsSettings.tsx");
  const confirmation = source("./src/components/chat/ConfirmationCard.tsx");

  assert.match(nav, /href: "\/app\/llamadas",\s*label: "Llamadas"/);
  assert.match(calls, /Qué falta para llamar/);
  assert.match(calls, /<PhoneAgentTemplatesSettings/);
  assert.match(calls, /Preparar llamada/);
  assert.match(calls, /Confirmación final/);
  assert.match(calls, /Llamar ahora/);
  assert.match(calls, /Configurar o reparar recepción/);
  assert.match(calls, /setupIncomingCalls/);
  assert.match(calls, /confirmDestination/);
  assert.match(calls, /confirmRecipient/);
  assert.match(calls, /confirmGoal/);
  assert.match(calls, /confirmAgent/);
  assert.match(calls, /Agente exacto/);
  assert.match(confirmation, /name === "llamar_contacto"/);
  assert.match(confirmation, /args\.destinatario/);
  assert.match(confirmation, /args\.telefono_e164/);
  assert.match(confirmation, /args\.agente/);
  assert.match(confirmation, /args\.objetivo/);
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
