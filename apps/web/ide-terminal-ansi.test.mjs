import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

/**
 * SS3.5 ("salida completa, ANSI incluido") + el encargo de `TerminalPanel.tsx`:
 * el defecto que este archivo reemplaza vivia en `page.tsx:352-369` (hoy
 * `estado-ide.ts::cleanTerminalOutput`) y borraba activamente los codigos
 * ANSI antes de pintar. `TerminalPanel.tsx` tiene JSX, asi que -- igual que el
 * resto de `*.test.mjs` de este paquete (ver `chat-layout.test.mjs`,
 * `desktop-navigation.test.mjs`) -- no se importa como modulo (el "type
 * stripping" nativo de Node no compila JSX); se lee su codigo fuente y se
 * comprueba el patron, no se renderiza.
 */
const SOURCE = readFileSync(
  new URL("./src/components/ide/paneles/TerminalPanel.tsx", import.meta.url),
  "utf8",
);

const ESC = String.fromCharCode(27);
const BEL = String.fromCharCode(7);
const NUL = String.fromCharCode(0);

/**
 * Reproduce (sin importarla) la cadena de `.replace(...)` de
 * `estado-ide.ts::cleanTerminalOutput`. Sirve para dos cosas con la misma
 * prueba: confirmar que esta SI es capaz de destruir un "clear screen + home"
 * (si deja de serlo, el defecto documentado ya no existe y hay que revisar la
 * prueba), y, por contraste, confirmar que `TerminalPanel.tsx` no reproduce
 * ese mismo patron en su propio camino de datos. Los códigos de control se
 * arman con `String.fromCharCode` (no como literales embebidos en el archivo)
 * para no dejar bytes de control sueltos en el código fuente del test.
 */
function limpiadorViejo(texto) {
  return texto
    .replace(new RegExp(`${ESC}\\][^${BEL}]*(?:${BEL}|${ESC}\\\\)`, "g"), "")
    .replace(new RegExp(`${ESC}\\[[0-?]*[ -/]*[@-~]`, "g"), "")
    .replace(new RegExp(`${ESC}[()][0-2A-Z]`, "g"), "")
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "")
    .replace(new RegExp(NUL, "g"), "");
}

test("control: el limpiador viejo de veras se come un clear screen + home (ESC[2J ESC[H)", () => {
  const sucio = `antes${ESC}[2J${ESC}[Hdespues`;
  assert.equal(limpiadorViejo(sucio), "antesdespues");
  assert.notEqual(limpiadorViejo(sucio), sucio);
});

test("TerminalPanel.tsx no importa el limpiador de ANSI (solo lo nombra en un comentario para explicar el reemplazo)", () => {
  assert.doesNotMatch(SOURCE, /import\s*\{[^}]*cleanTerminalOutput[^}]*\}/s);
});

test("la salida cruda del evento se escribe en el terminal sin pasar por .replace(...)", () => {
  // Tiene que existir una escritura directa `algo.write(event.text)` -- si
  // alguien reintroduce una limpieza entre medio, este patron deja de calzar.
  assert.match(SOURCE, /\.write\(\s*event\.text\s*\)/);
});

test("el panel monta un PTY real (@xterm/xterm) con entrada tecla-por-tecla, no un <input> de linea completa", () => {
  assert.match(SOURCE, /@xterm\/xterm/);
  assert.match(SOURCE, /term\.onData/);
  assert.match(SOURCE, /sendIdeTerminalInput/);
  assert.doesNotMatch(SOURCE, /<textarea[\s/]/);
  // Nota: el patrón exige un espacio o cierre después de "<input" para no
  // chocar con la prosa del comentario del propio archivo, que menciona
  // "<input>" en prosa al explicar qué se reemplazó.
  assert.doesNotMatch(SOURCE, /<input[\s/]/);
});

test("carga xterm.js diferida: solo detras de import() dinamico, nunca importado en el cuerpo del modulo", () => {
  assert.doesNotMatch(SOURCE, /^import\s+(?:\{[^}]*\}|\*\s+as\s+\w+|\w+)\s+from\s+"@xterm\/xterm";?$/m);
  assert.match(SOURCE, /import\("@xterm\/xterm"\)/);
});

test("el gap de resize del PTY queda declarado en la interfaz, no simulado", () => {
  assert.match(SOURCE, /TIOCSWINSZ/);
  assert.match(SOURCE, /tama.o no sincronizado/);
});
