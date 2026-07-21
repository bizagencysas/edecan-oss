import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = (relativePath) =>
  readFileSync(new URL(relativePath, import.meta.url), "utf8");

test("desktop usa navegacion de documento y rutas con slash final", () => {
  const root = source("./src/app/page.tsx");
  const appLayout = source("./src/app/(app)/layout.tsx");
  const authLayout = source("./src/app/(auth)/layout.tsx");

  assert.match(root, /window\.location\.replace\(hasSession\(\) \? "\/app\/" : "\/login\/"\)/);
  assert.match(appLayout, /window\.location\.replace\("\/login\/"\)/);
  assert.match(authLayout, /window\.location\.replace\("\/app\/"\)/);

  for (const file of [root, appLayout, authLayout]) {
    assert.doesNotMatch(file, /useRouter/);
    assert.doesNotMatch(file, /router\.replace/);
  }
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
