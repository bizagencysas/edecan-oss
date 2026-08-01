import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = (relativePath) => readFileSync(new URL(relativePath, import.meta.url), "utf8");

test("el shell de escritorio mantiene navegacion y contenido con scroll independiente", () => {
  const shell = source("./src/components/layout/AppShell.tsx");
  const sidebar = source("./src/components/layout/Sidebar.tsx");

  assert.match(shell, /h-dvh min-h-0/);
  assert.match(shell, /overflow-hidden/);
  assert.match(sidebar, /data-testid="primary-navigation-scroll"/);
  assert.match(sidebar, /min-h-0 flex-1 flex-col overflow-y-auto overscroll-contain/);
});

test("el historial de conversaciones se contrae horizontalmente y persiste la preferencia", () => {
  const page = source("./src/app/(app)/app/page.tsx");
  const list = source("./src/components/chat/ConversationList.tsx");

  assert.match(page, /edecan:conversation-panel-collapsed/);
  assert.match(page, /historyCollapsed \? "w-16" : "w-72"/);
  assert.match(page, /data-testid="desktop-conversation-panel"/);
  assert.match(list, /aria-label="Contraer historial"/);
  assert.match(list, /aria-label="Expandir historial"/);
});

test("el chat usa ancho legible, compositor centrado y textarea autoajustable", () => {
  const page = source("./src/app/(app)/app/page.tsx");
  const composer = source("./src/components/chat/ChatComposer.tsx");

  assert.match(page, /max-w-4xl/);
  assert.match(composer, /textarea\.scrollHeight/);
  assert.match(composer, /mx-auto w-full max-w-4xl/);
  assert.match(composer, /Enter para enviar/);
});
