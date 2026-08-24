import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const selector = readFileSync(
  new URL("./src/components/configuracion/SelectorLLM.tsx", import.meta.url),
  "utf8",
);
const connections = readFileSync(
  new URL("./src/components/configuracion/ConexionesSection.tsx", import.meta.url),
  "utf8",
);
const welcome = readFileSync(
  new URL("./src/app/(app)/app/bienvenida/page.tsx", import.meta.url),
  "utf8",
);
const tauri = readFileSync(
  new URL("../desktop/src-tauri/tauri.conf.json", import.meta.url),
  "utf8",
);
const macLinuxBuild = readFileSync(
  new URL("../desktop/scripts/build-backend.sh", import.meta.url),
  "utf8",
);
const windowsBuild = readFileSync(
  new URL("../desktop/scripts/build-backend.ps1", import.meta.url),
  "utf8",
);

test("la app desktop ofrece proveedores locales y APIs configurables", () => {
  for (const provider of [
    "workers_ai",
    "claude_cli",
    "codex_cli",
    "ollama",
    "anthropic",
    "openai_compat",
    "vertex",
  ]) {
    assert.match(selector, new RegExp(`\\b${provider}\\b`));
  }
  assert.match(connections, /<SelectorLLM/);
  assert.match(welcome, /<SelectorLLM simplified/);
});

test("macOS, Windows y Linux empaquetan la misma UI que contiene el selector", () => {
  assert.match(tauri, /frontendDist/);
  assert.match(tauri, /productName.*Edec/);
  assert.match(macLinuxBuild, /NEXT_OUTPUT=export/);
  assert.match(macLinuxBuild, /apps\/web\/out/);
  assert.match(windowsBuild, /NEXT_OUTPUT/);
  assert.match(windowsBuild, /apps\/web\/out/);
  assert.match(connections, /Cómo piensa Edecan/);
});
