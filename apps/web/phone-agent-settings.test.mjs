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
  assert.match(settings, /Qué debe lograr normalmente/);
  assert.match(settings, /ninguna plantilla puede saltarse el consentimiento ni la confirmación final/);
});

test("el cliente web usa el CRUD tenant-scoped de plantillas", () => {
  const api = source("./src/lib/api.ts");

  assert.match(api, /listPhoneAgentTemplates/);
  assert.match(api, /createPhoneAgentTemplate/);
  assert.match(api, /updatePhoneAgentTemplate/);
  assert.match(api, /deletePhoneAgentTemplate/);
  assert.match(api, /"\/v1\/phone\/agent-templates"/);
});
