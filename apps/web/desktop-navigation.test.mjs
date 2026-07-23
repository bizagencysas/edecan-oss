import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = (relativePath) =>
  readFileSync(new URL(relativePath, import.meta.url), "utf8");

test("desktop usa navegacion de documento y rutas con slash final", () => {
  const root = source("./src/app/page.tsx");
  const appLayout = source("./src/app/(app)/layout.tsx");
  const authLayout = source("./src/app/(auth)/layout.tsx");

  assert.match(root, /window\.location\.replace\("\/login\/"\)/);
  assert.match(root, /status\.onboarding_completed \? "\/app\/" : "\/app\/bienvenida\/"/);
  assert.match(root, /if \(loading\) return/);
  assert.match(appLayout, /window\.location\.replace\("\/login\/"\)/);
  assert.match(authLayout, /window\.location\.replace\("\/app\/"\)/);

  for (const file of [root, appLayout, authLayout]) {
    assert.doesNotMatch(file, /useRouter/);
    assert.doesNotMatch(file, /router\.replace/);
  }
});

test("la app instalada abre al dueño local sin formulario de credenciales", () => {
  const authContext = source("./src/lib/auth-context.tsx");
  const api = source("./src/lib/api.ts");
  const authLayout = source("./src/app/(auth)/layout.tsx");

  assert.match(authContext, /desktop && !getAccessToken\(\)/);
  assert.match(authContext, /await api\.openLocalDesktopSession\(\)/);
  assert.match(api, /apiJson<TokenPair>\("\/v1\/auth\/local"/);
  assert.match(api, /"X-Edecan-Desktop-Capability": capability/);
  assert.match(authLayout, /isLocalDesktop \?/);
  assert.match(authLayout, /No necesitas correo ni contraseña/);
});

test("las redirecciones por sesion vencida nunca solicitan una ruta RSC", () => {
  const apiFiles = [
    "api.ts",
    "api-ads.ts",
    "api-configuracion.ts",
    "api-mcp.ts",
    "api-mensajes.ts",
    "api-misiones.ts",
    "api-ordenes.ts",
    "api-skills.ts",
  ];

  for (const filename of apiFiles) {
    const file = source(`./src/lib/${filename}`);
    assert.doesNotMatch(file, /window\.location\.assign\("\/login"\)/);
    assert.match(file, /window\.location\.assign\("\/login\/"\)/);
  }
});
