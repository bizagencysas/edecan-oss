import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

function source(path) {
  return readFileSync(new URL(path, import.meta.url), "utf8");
}

test("Contenido hace visible creación, imagen, publicación y plan diario", () => {
  const studio = source("./src/components/content/SocialContentStudio.tsx");
  const nav = source("./src/components/layout/nav-items.ts");
  const api = source("./src/lib/api.ts");

  assert.match(nav, /href: "\/app\/contenido", label: "Contenido"/);
  assert.match(studio, /Crear post/);
  assert.match(studio, /Publicar en LinkedIn/);
  assert.match(studio, /2 al día/);
  assert.match(studio, /3 al día/);
  assert.match(studio, /No publiques automáticamente/);
  assert.match(api, /\/v1\/content\/social\/publish/);
  assert.match(api, /confirmed: true/);
});

test("LinkedIn aparece como conector oficial y enlaza al estudio", () => {
  const connectors = source("./src/components/configuracion/ConnectorsSettings.tsx");
  const guides = source("./src/lib/connector-guides.ts");

  assert.match(guides, /linkedin:/);
  assert.match(guides, /https:\/\/www\.linkedin\.com\/developers\/apps/);
  assert.match(connectors, /connector\.key === "linkedin"/);
  assert.match(connectors, /Crear posts, imágenes y plan diario/);
});
