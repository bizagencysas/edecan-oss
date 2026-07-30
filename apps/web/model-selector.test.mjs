import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { turnoTraeImagen } from "./src/lib/chat-attachments.ts";

const source = (relativePath) => readFileSync(new URL(relativePath, import.meta.url), "utf8");

function attachment(overrides = {}) {
  return {
    localId: "local-1",
    filename: "captura.png",
    sizeBytes: 120,
    status: "ready",
    fileId: "11111111-1111-4111-a111-111111111111",
    mime: "image/png",
    error: null,
    ...overrides,
  };
}

test("el aviso de ceguera solo se dispara con los mime que el backend mira de verdad", () => {
  assert.equal(turnoTraeImagen([attachment()]), true);
  assert.equal(turnoTraeImagen([attachment({ mime: "image/JPEG" })]), true);
  assert.equal(turnoTraeImagen([attachment({ mime: "image/webp; charset=binary" })]), true);
  // Se avisa aunque la subida no haya terminado: la decisión se toma antes de enviar.
  assert.equal(turnoTraeImagen([attachment({ status: "uploading", fileId: null })]), true);
  // Un PDF o un HEIC no entran al turno como imagen, van por `leer_archivo`.
  assert.equal(turnoTraeImagen([attachment({ mime: "application/pdf" })]), false);
  assert.equal(turnoTraeImagen([attachment({ mime: "image/heic" })]), false);
  assert.equal(turnoTraeImagen([attachment({ mime: null })]), false);
  assert.equal(turnoTraeImagen([]), false);
});

test("la hoja de modelos no hardcodea el catálogo: todo sale de GET /v1/models/chat", () => {
  const selector = source("./src/components/chat/ModelSelector.tsx");

  assert.match(selector, /Seleccionar modelo/);
  assert.match(selector, /Más modelos/);
  // Ni un id de modelo escrito a mano: las filas se pintan del catálogo.
  assert.doesNotMatch(selector, /@cf\//);
  assert.match(selector, /titulo=\{modelo\.nombre\}/);
  assert.match(selector, /modelo\.ve_imagenes \? modelo\.descripcion/);
  // Los niveles de esfuerzo también salen del catálogo, no de un enum local.
  assert.match(selector, /catalogo\.esfuerzos\.map/);
  assert.doesNotMatch(selector, /"bajo"|"medio"|"alto"/);
});

test("la fila Esfuerzo solo existe cuando el modelo activo la usa", () => {
  const selector = source("./src/components/chat/ModelSelector.tsx");

  assert.match(selector, /const esfuerzoVisible = activo\?\.soporta_esfuerzo \?\? false;/);
  assert.match(selector, /\{esfuerzoVisible && \(/);
});

test("se puede volver a automático y los ciegos quedan etiquetados", () => {
  const selector = source("./src/components/chat/ModelSelector.tsx");

  assert.match(selector, /titulo="Automático"/);
  assert.match(selector, /onSelect\(null, effort\)/);
  assert.match(selector, /modelo\.ve_imagenes \? null : "No ve imágenes"/);
  // La etiqueta es la señal; la descripción no repite lo mismo en la misma fila.
  assert.match(selector, /no ve im\[áa\]genes/);
  // Un secundario activo se anuncia en la fila que lo esconde, o la portada
  // quedaría sin ningún check.
  assert.match(selector, /valor=\{activo && !activo\.principal \? activo\.nombre : undefined\}/);
});

test("la pastilla del composer muestra modelo y esfuerzo, y abre la hoja", () => {
  const composer = source("./src/components/chat/ChatComposer.tsx");
  const page = source("./src/app/(app)/app/page.tsx");

  assert.match(composer, /onOpenModelSelector/);
  assert.match(composer, /aria-label=\{`Modelo: \$\{modelLabel\}\. Cambiar`\}/);
  assert.match(composer, /visionDegradationNote/);
  assert.match(page, /etiquetaSeleccion\(modelCatalog, chatModel, chatEffort\)/);
  assert.match(page, /no ve imágenes/);
});

test("elegir modelo persiste en la conversación con rollback si el PUT falla", () => {
  const page = source("./src/app/(app)/app/page.tsx");
  const api = source("./src/lib/api.ts");

  assert.match(api, /\/v1\/models\/chat/);
  assert.match(api, /\/v1\/conversations\/\$\{conversationId\}\/model/);
  assert.match(api, /method: "PUT", body: \{ model, effort \}/);
  assert.match(page, /setConversationModel\(conversationId, model, effort\)/);
  assert.match(page, /setChatModel\(modeloPrevio\)/);
  assert.match(page, /setChatEffort\(esfuerzoPrevio\)/);
  // La selección de la conversación abierta manda sobre la de la anterior.
  assert.match(page, /setChatModel\(conv\.model \?\? null\)/);
});
