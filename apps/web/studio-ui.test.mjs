import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");
const workspace = read("./src/components/studio/StudioWorkspace.tsx");
const canvas = read("./src/components/studio/MasterCanvas.tsx");
const api = read("./src/lib/api.ts");
const navigation = read("./src/components/layout/nav-items.ts");

test("Studio usa el único contrato autenticado y no hereda APIs ni almacenamiento del SaaS original", () => {
  assert.match(api, /\/v1\/content\/studio\/actions/);
  assert.match(workspace, /runStudioAction/);
  assert.doesNotMatch(workspace, /\/api\/templates|browser-db|allow-same-origin|allow-scripts/);
});

test("el workspace cubre creación, apertura, canvas, inspector, ajustes, historial, variantes, plantillas y entrega", () => {
  for (const token of ["WorkspacePanel", "MasterCanvas", "InlineInspector", "TweaksPanel", "HistoryNav", "TemplateGallery", "artifacts.length > 1", "downloadCurrent", "createProject", "selectProject"]) {
    assert.match(workspace, new RegExp(token));
  }
  assert.match(workspace, /Imagen/);
  assert.match(workspace, /Video/);
  assert.match(workspace, /Campaña/);
  assert.match(workspace, /Producto/);
  assert.match(workspace, /Personas/);
  assert.match(workspace, /Analizar/);
});

test("la edición destructiva se reemplaza por archivo reversible con confirmación explícita", () => {
  assert.match(workspace, /confirmed: true/);
  assert.match(workspace, /sus revisiones no se eliminan/);
  assert.doesNotMatch(workspace, /deleteProject|action: "delete"/);
});

test("el canvas es interactivo pero mantiene aislamiento fuerte", () => {
  assert.match(canvas, /CanvasAnnotations/);
  assert.match(canvas, /Señalar/);
  assert.match(canvas, /Pantalla completa/);
  assert.match(canvas, /sandbox=""/);
  assert.match(canvas, /referrerPolicy="no-referrer"/);
  assert.doesNotMatch(canvas, /allow-scripts|allow-same-origin|srcDoc/);
});

test("Studio no altera las tres entradas principales y queda en modo avanzado", () => {
  const primary = navigation.split("export const PRIMARY_NAV_ITEMS", 2)[1].split("];", 1)[0];
  assert.doesNotMatch(primary, /studio/i);
  assert.match(navigation, /href: "\/app\/studio"/);
  assert.match(navigation, /label: "Studio visual"/);
});

test("el layout contempla paneles contextuales en móvil y escritorio", () => {
  assert.match(workspace, /lg:static lg:block/);
  assert.match(workspace, /xl:static xl:block/);
  assert.match(workspace, /fixed inset-x-3 bottom-3 top-20/);
  assert.match(workspace, /aria-live="polite"/);
});
