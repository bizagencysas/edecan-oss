import assert from "node:assert/strict";
import test from "node:test";

import {
  buildStudioEditInstruction,
  createTweakControlFromPrompt,
  isolatedStudioHtmlBlob,
  pickStudioPreviewArtifact,
  studioProjectsFromResponse,
  studioRevisionsFromResponse,
  studioTemplatesFromResponse,
} from "./src/lib/studio.ts";

const response = (result, artifacts = []) => ({
  status: "ready",
  action: "test",
  message: "ok",
  result,
  artifacts,
  presentation: [],
});

test("Studio endurece proyectos, revisiones y plantillas antes de renderizarlos", () => {
  assert.deepEqual(studioProjectsFromResponse(response({ projects: [null, { id: "p1", name: "Campaña", mode: "ad", revisions: 2 }] })), [{
    id: "p1", name: "Campaña", mode: "ad", revisions: 2, updatedAt: "", brandName: "", archivedAt: null,
  }]);
  assert.equal(studioRevisionsFromResponse(response({ revisions: [{ id: "r1", width: 10, height: 20 }] }))[0].width, 320);
  assert.equal(studioTemplatesFromResponse(response({ templates: [{ id: "t1", mode: "unknown" }] }))[0].mode, "general");
});

test("elige HTML aislable primero y conserva imágenes como alternativa", () => {
  const picked = pickStudioPreviewArtifact([
    { file_id: "png", filename: "preview.png", mime: "image/png" },
    { file_id: "html", filename: "board.html", mime: "text/html" },
  ]);
  assert.equal(picked?.file_id, "html");
  assert.equal(picked?.kind, "html");
});

test("la previsualización HTML inserta CSP sin red, scripts, formularios ni objetos", async () => {
  const isolated = await isolatedStudioHtmlBlob(new Blob(["<html><head></head><body>Diseño</body></html>"]));
  const html = await isolated.text();
  assert.match(html, /default-src 'none'/);
  assert.match(html, /script-src 'none'/);
  assert.match(html, /connect-src 'none'/);
  assert.match(html, /form-action 'none'/);
  assert.match(html, /object-src 'none'/);
});

test("convierte selección, anotaciones y controles en una instrucción de edición reversible", () => {
  const control = createTweakControlFromPrompt("Color de acento");
  assert.equal(control?.type, "color");
  const instruction = buildStudioEditInstruction({
    instruction: "Acorta el titular",
    selection: "x 320, y 90",
    annotations: "1. flecha roja",
    tweaks: control ? [control] : [],
  });
  assert.match(instruction, /Acorta el titular/);
  assert.match(instruction, /Zona indicada/);
  assert.match(instruction, /Anotaciones visibles/);
  assert.match(instruction, /Color de acento/);
});
