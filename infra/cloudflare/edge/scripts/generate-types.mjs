#!/usr/bin/env node
// Reemplazo portable de generate-types.sh.
//
// El original era un script bash (shebang `#!/usr/bin/env bash`) invocado
// directamente desde package.json como `"cf:types": "./scripts/generate-types.sh"`.
// En Windows, `npm run cf:types` ejecuta el comando con cmd.exe por defecto:
// cmd no entiende shebangs ni tiene asociada la extensión .sh a un intérprete,
// así que el script fallaba con "no se reconoce como comando" salvo que la
// persona ya tuviera Git Bash forzado como script-shell de npm (configuración
// opcional, no el comportamiento por defecto). Node sí corre igual en
// Windows/macOS/Linux, así que reescribirlo aquí quita la dependencia de bash
// para este paso del flujo de desarrollo.
import { copyFile, rm } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const PROJECT_DIR = resolve(SCRIPT_DIR, "..");
const DEV_VARS = resolve(PROJECT_DIR, ".dev.vars");
const DEV_VARS_EXAMPLE = resolve(PROJECT_DIR, ".dev.vars.example");

function run(command, args, cwd) {
  return new Promise((resolvePromise, reject) => {
    // shell: true es necesario para resolver `npx`/`npx.cmd` según la
    // plataforma (en Windows los binarios de npm son .cmd, no ejecutables
    // directos). Los argumentos van en la lista, nunca concatenados a mano
    // en `command`, así que no hay inyección de shell posible aquí: no se
    // interpola texto externo ni de usuario en la línea de comando.
    const child = spawn(command, args, { cwd, shell: true, stdio: "inherit" });
    child.on("error", reject);
    child.on("exit", (code) => {
      if (code === 0) {
        resolvePromise(undefined);
      } else {
        reject(new Error(`${command} ${args.join(" ")} terminó con código ${code}`));
      }
    });
  });
}

async function main() {
  await copyFile(DEV_VARS_EXAMPLE, DEV_VARS);
  try {
    await run("npx", ["wrangler", "types"], PROJECT_DIR);
  } finally {
    await rm(DEV_VARS, { force: true });
  }
}

await main();
