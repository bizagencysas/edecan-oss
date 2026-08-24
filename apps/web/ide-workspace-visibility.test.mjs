import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

/**
 * Encargo "El repositorio activo NO se ve" + "Conectar ProjectPicker".
 *
 * Antes, la única forma de saber sobre qué repo trabaja el agente era leer
 * un párrafo de bienvenida y una línea diminuta en el pie de la barra
 * lateral -- y `ProjectPicker.tsx` (312 líneas, completo y probado) no lo
 * importaba nadie. `WorkspacePill.tsx`/`ScopePill` y sus tres puntos de
 * conexión (`Reposo.tsx::Composer` -- compartido por Reposo y Trabajando --,
 * `Editor.tsx` y el formulario de crear proyecto de `ProjectSidebar.tsx`)
 * tienen JSX, así que -- igual que el resto de `*.test.mjs` de este paquete
 * (ver `studio-ui.test.mjs`, `ide-terminal-ansi.test.mjs`) -- no se importan
 * como módulo: se lee su código fuente y se comprueba el patrón.
 */
const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

const pill = read("./src/components/ide/WorkspacePill.tsx");
const projectPicker = read("./src/components/ide/ProjectPicker.tsx");
const reposo = read("./src/components/ide/estado/Reposo.tsx");
const editor = read("./src/components/ide/estado/Editor.tsx");
const sidebar = read("./src/components/ide/ProjectSidebar.tsx");
const page = read("./src/app/(app)/app/ide/page.tsx");
const estadoIde = read("./src/components/ide/estado-ide.ts");

test("ProjectPicker ya no está huérfano: algo más que su propio archivo lo importa", () => {
  const importers = [pill, sidebar].map((source) =>
    /from ["']@\/components\/ide\/ProjectPicker["']/.test(source),
  );
  assert.ok(importers.every(Boolean), "WorkspacePill y ProjectSidebar deben importar ProjectPicker");
});

test("ProjectPicker soporta modo oscuro (no lo tenía antes de conectarlo)", () => {
  assert.match(projectPicker, /dark:/);
});

test("la pastilla de proyecto es un desplegable real; la de alcance es solo texto informativo", () => {
  const [workspacePillBody, scopePillBody] = pill.split("export function ScopePill");
  assert.match(workspacePillBody, /<button/, "WorkspacePill debe ser un botón que abre un menú");
  assert.match(workspacePillBody, /ProjectPicker/, "el menú de WorkspacePill debe embeber ProjectPicker");
  assert.doesNotMatch(
    scopePillBody,
    /<button/,
    "ScopePill no debe ser un botón: hoy no hay más de un alcance posible",
  );
});

test("el repositorio activo es visible en los tres estados de §3.1", () => {
  assert.match(reposo, /<WorkspacePill/, "Reposo/Trabajando comparten el Composer, que debe montar la pastilla");
  assert.match(reposo, /<ScopePill/);
  assert.match(editor, /<WorkspacePill/, "el estado Editor también debe mostrar el proyecto activo");
  assert.match(editor, /<ScopePill/);
});

test("cambiar de workspace desde la pastilla reusa selectWorkspace, no un fetch aparte", () => {
  assert.match(pill, /onChange=\{onSelectWorkspace\}/);
  assert.match(reposo, /onSelectWorkspace=\{\(next\) => void estado\.selectWorkspace\(next\)\}/);
  assert.match(editor, /onSelectWorkspace=\{\(next\) => void estado\.selectWorkspace\(next\)\}/);
});

test("selectWorkspace se banca una carpeta que `workspaces` todavía no conoce (autorizada desde la pastilla)", () => {
  const body = estadoIde.split("async function selectWorkspace")[1]?.split("\n\n")[0] ?? "";
  assert.match(body, /workspaces\.some/);
  assert.doesNotMatch(body, /await activateIdeWorkspace\(next\.id\);\s*$/m);
});

test("el formulario de crear proyecto usa ProjectPicker en vez de un <select> suelto", () => {
  assert.match(sidebar, /<ProjectPicker/);
  assert.doesNotMatch(sidebar, /<Select\b/);
});

test("cero literales de color [#rrggbb] en components/ide (regla dura del encargo)", () => {
  for (const source of [pill, projectPicker, reposo, editor, sidebar]) {
    assert.doesNotMatch(source, /\[#[0-9a-fA-F]{3,8}\]/);
  }
});

test("page.tsx mantiene sincronizada la lista de workspaces entre pastilla, sidebar y estado", () => {
  assert.match(page, /onWorkspacesChange=\{estado\.syncWorkspaces\}/);
});
