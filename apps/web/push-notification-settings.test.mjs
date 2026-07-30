import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

function source(path) {
  return readFileSync(new URL(path, import.meta.url), "utf8");
}

test("Ajustes configura APNs y FCM sin convertir el repositorio en almacén de secretos", () => {
  const component = source("./src/components/configuracion/PushNotificationSettings.tsx");
  const page = source("./src/app/(app)/app/ajustes/page.tsx");

  assert.match(page, /<PushNotificationSettings \/>/);
  assert.match(component, /Elegir archivo \.p8/);
  assert.match(component, /Elegir service account JSON/);
  assert.match(component, /Guardar APNs cifrado/);
  assert.match(component, /Guardar FCM cifrado/);
  assert.match(component, /getPushStatus/);
  assert.match(component, /getPushPreferences/);
  assert.match(component, /updatePushPreferences/);
  assert.match(component, /disconnectPushCredentials/);
  assert.match(component, /Apple Developer/);
  assert.match(component, /Firebase/);
  assert.match(component, /No subas este JSON al repositorio/);
});

test("el cliente web usa únicamente el contrato push autenticado", () => {
  const api = source("./src/lib/api.ts");

  assert.match(api, /getPushStatus/);
  assert.match(api, /getPushPreferences/);
  assert.match(api, /updatePushPreferences/);
  assert.match(api, /savePushCredentials/);
  assert.match(api, /disconnectPushCredentials/);
  assert.match(api, /"\/v1\/devices\/push\/status"/);
  assert.match(api, /"\/v1\/devices\/push\/preferences"/);
  assert.match(api, /"\/v1\/devices\/push\/credentials"/);
});
