import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

function source(path) {
  return readFileSync(new URL(path, import.meta.url), "utf8");
}

test("el chat es la superficie principal de /app, no un cockpit", () => {
  const page = source("./src/app/(app)/app/page.tsx");
  const nav = source("./src/components/layout/nav-items.ts");

  assert.match(page, /ChatHome/);
  assert.match(page, /MessageBubble/);
  assert.match(page, /ToolTimeline/);
  assert.match(page, /ConfirmationCard/);
  assert.doesNotMatch(page, /Create an AI Agent/i);
  assert.match(nav, /href: "\/app",\s*label: "Edecan"/);
});

test("empty state humano alineado con iOS", () => {
  const home = source("./src/components/chat/ChatHome.tsx");
  const page = source("./src/app/(app)/app/page.tsx");

  assert.match(home, /Escríbele a Edecán/);
  assert.match(home, /data-testid="chat-empty-state"/);
  assert.match(home, /puede escribirte primero/i);
  assert.match(page, /Escríbele a Edecán/);
  assert.match(page, /Empezar conversación/);
});

test("la respuesta final no se trocea en pasos: solo apertura usa el hilo narrativo en iOS", () => {
  const burbuja = source("../mobile/ios/EdecanApp/Componentes/BurbujaMensaje.swift");

  assert.match(burbuja, /campo == "apertura"/);
  assert.match(burbuja, /campo == "texto"/);
  assert.match(burbuja, /SegmentadorNarracion\.debeMostrarHilo/);
  assert.match(burbuja, /esRelatoDeTrabajo = campo == "apertura"/);
});

test("confirmaciones y trabajo pesado son tarjetas compactas in-thread", () => {
  const confirmation = source("./src/components/chat/ConfirmationCard.tsx");
  const timeline = source("./src/components/chat/ToolTimeline.tsx");
  const bubble = source("./src/components/chat/MessageBubble.tsx");

  assert.match(confirmation, /max-w-\[340px\]/);
  assert.match(confirmation, /Ver detalles/);
  assert.match(confirmation, /Ocultar detalles/);
  assert.match(confirmation, /Ver computadora/);
  assert.match(confirmation, /usar_computadora/);
  assert.match(timeline, /max-w-\[340px\]/);
  assert.match(timeline, /expandido/);
  assert.match(bubble, /rounded-br-\[4px\]/);
  assert.match(bubble, /rounded-bl-\[4px\]/);
});

test("composer web usa el mismo tono que iOS", () => {
  const composer = source("./src/components/chat/ChatComposer.tsx");
  assert.match(composer, /Escríbele a Edecán/);
});

test("deeplink de push respeta chat_id en iOS (no Actividad como destino primario)", () => {
  const root = source("../mobile/ios/EdecanApp/RootTabView.swift");
  assert.match(root, /abrirConversacionDesdeNotificacion/);
  assert.match(root, /conversacionPendiente/);
  assert.match(root, /seleccion = \.edecan/);
});
