import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

/**
 * Encargo "Hecho 2": `SessionManager.set_modelo_agente` ya existía y
 * aplicaba el cambio de modelo EN VIVO del lado del companion, pero no
 * tenía cable -- ni acción en `IDE_ACTIONS`, ni endpoint REST, ni cliente
 * web. Estos tests cubren la parte de este paquete que vive en
 * `apps/web`: el cliente HTTP (`api-ide.ts::setIdeAgentModel`) y que el
 * `<select>` de modelo (`estado-ide.ts::setIdeModel`), con una sesión viva,
 * lo llame de inmediato en vez de solo guardar la preferencia local.
 *
 * Sigue el mismo patrón de "leer el código fuente y comprobar por regex" que
 * ya usan `ide-cola.test.mjs`/`selector-modo.test.mjs` para este mismo
 * paquete de trabajo (IDE): no hay entorno de React/DOM en esta suite.
 */

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

const apiIde = read("./src/lib/api-ide.ts");
const estadoIde = read("./src/components/ide/estado-ide.ts");

// --- api-ide.ts::setIdeAgentModel -------------------------------------------

test("setIdeAgentModel manda POST a /v1/ide/agents/{id}/model con el modelo en el body", () => {
  const bloque = apiIde.split("export async function setIdeAgentModel")[1];
  assert.ok(bloque, "no se encontró setIdeAgentModel en api-ide.ts");
  const cuerpo = bloque.split("\n}")[0];
  assert.match(cuerpo, /\/v1\/ide\/agents\/\$\{encodeURIComponent\(sessionId\)\}\/model/);
  assert.match(cuerpo, /method:\s*"POST"/);
  assert.match(cuerpo, /jsonBody\(\{\s*model\s*\}\)/);
});

// --- estado-ide.ts::setIdeModel ---------------------------------------------

test("setIdeModel envuelve el setState crudo -- el nombre público ya no es el useState directo", () => {
  assert.match(estadoIde, /const \[ideModel, setIdeModelInterno\] = useState<string>\(""\);/);
  assert.match(estadoIde, /const setIdeModel = useCallback\(/);
});

test("con sesión viva, setIdeModel llama a setIdeAgentModel de inmediato (no espera al próximo mensaje)", () => {
  const cuerpo = estadoIde.split("const setIdeModel = useCallback(")[1].split("\n  );")[0];
  assert.match(cuerpo, /setIdeModelInterno\(model\)/);
  assert.match(cuerpo, /agent && isLive\(agent\)/);
  assert.match(cuerpo, /void setIdeAgentModel\(agent\.id, model\)\.catch\(\(\) => undefined\)/);
});

test("setIdeAgentModel está importado desde api-ide.ts", () => {
  assert.match(estadoIde, /setIdeAgentModel,?\s*\n?\s*\} from "@\/lib\/api-ide";/);
});

test("la selección de modelo por defecto en el arranque usa el setter interno, no dispara una llamada en vivo sin sesión", () => {
  const bootstrap = estadoIde.split("const bootstrap = useCallback")[1].split("}, [loadWorkspaceData]);")[0];
  assert.match(bootstrap, /setIdeModelInterno\(\(current\) =>/);
  assert.doesNotMatch(bootstrap, /\bsetIdeModel\(\(current\)/, "el default de arranque no debe pasar por el wrapper que empuja al companion");
});

test("el modelo elegido por el comando /model también se aplica con el wrapper (encargo: /model tampoco esperaba al próximo turno hasta ahora)", () => {
  assert.match(estadoIde, /if \(result\.set_model\) setIdeModel\(result\.set_model\);/);
});

test("el modelo se sigue exponiendo con el mismo nombre público de siempre (Reposo.tsx no cambia)", () => {
  const exportado = estadoIde.split("return {")[1];
  assert.ok(exportado, "no se encontró el objeto devuelto por useIdeEstado");
  assert.match(estadoIde, /\bsetIdeModel,\n/);
});
