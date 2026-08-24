import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { ESFUERZO_NIVELES, nivelesValidos } from "./src/lib/api-modo.ts";

const source = (relativePath) => readFileSync(new URL(relativePath, import.meta.url), "utf8");

// --- nivelesValidos ----------------------------------------------------------

test("sin dato del backend, se cae a los tres niveles de siempre (nada inventado, es el único catálogo que existe)", () => {
  assert.deepEqual(nivelesValidos(undefined), ["bajo", "medio", "alto"]);
  assert.deepEqual(nivelesValidos(null), ["bajo", "medio", "alto"]);
  assert.deepEqual(nivelesValidos([]), ["bajo", "medio", "alto"]);
});

test("filtra a lo que el backend realmente ofrece, preservando el orden rápido -> inteligente", () => {
  assert.deepEqual(nivelesValidos(["alto", "bajo"]), ["bajo", "alto"]);
  assert.deepEqual(nivelesValidos(["medio"]), ["medio"]);
});

test("descarta valores que el backend no reconoce y no rompe si vienen mezclados", () => {
  assert.deepEqual(nivelesValidos(["bajo", "turbo", "medio"]), ["bajo", "medio"]);
});

test("si el backend manda solo basura, no deja el control sin niveles: vuelve al catálogo completo", () => {
  assert.deepEqual(nivelesValidos(["turbo", "ultra"]), [...ESFUERZO_NIVELES]);
});

// --- SelectorModo.tsx: honestidad sobre qué modo tiene respaldo real -------

const componente = source("./src/components/ide/SelectorModo.tsx");

test("ningún literal de color [#rrggbb] -- solo tokens forja.* y paleta de Tailwind", () => {
  assert.doesNotMatch(componente, /\[#[0-9a-fA-F]{3,8}\]/);
});

// Hallazgo verificado leyendo el companion (ide_workers_agent.py,
// ide_modos.py, ide_opencode_permisos.py) Y el endpoint web
// (`apps/api/edecan_api/routers/ide.py::ModoIn`, que YA declara `modo`): los
// CUATRO modos gatean de verdad las herramientas nativas, bajo opencode
// además cambian el agente en vivo, y ahora también se pueden FIJAR desde
// acá -- ninguno de los cuatro queda con un candado fijo y una nota que ya
// no describe el repo.

test("los cuatro modos se listan con su descripción real -- ninguno queda fuera ni con 'disponible' fantasma", () => {
  assert.doesNotMatch(componente, /disponible:\s*(true|false)/, "el campo `disponible` por opción ya no existe: los cuatro comparten el mismo hueco");
  for (const modo of ["manual", "aceptar_ediciones", "plan", "auto"]) {
    assert.match(componente, new RegExp(`id:\\s*"${modo}"`));
  }
});

test("el menú ya permite FIJAR el modo: cada opción llama a elegirModo, sin candado incondicional ni nota de 'no conectado'", () => {
  const menu = componente.split("{menuAbierto && (")[1];
  assert.doesNotMatch(menu, /disabled\s*\n/, "ninguna opción debe llevar `disabled` incondicional -- ModoIn ya acepta el campo");
  assert.match(menu, /onClick=\{\(\) => void elegirModo\(opcion\.id\)\}/, "cada opción del menú debe disparar elegirModo");
  assert.doesNotMatch(componente, /todavía no conectado al backend/, "la nota vieja, imprecisa, ya no debe aparecer");
  assert.doesNotMatch(componente, /no acepta ese campo aún/, "la nota que decía que ModoIn no tenía el campo `modo` ya no debe aparecer -- describía un estado del repo que cambió");
});

test("elegirModo exige sessionId (no hay preferencia local para el modo, a diferencia del esfuerzo) y llama a putIdeModo con { modo }", () => {
  assert.match(componente, /async function elegirModo\(nuevo: IdeModoAgente\)/);
  const cuerpo = componente.split("async function elegirModo")[1].split("\n  const deshabilitado = ")[0];
  assert.match(cuerpo, /if \(!sessionId \|\| !modo \|\| savingModo \|\| nuevo === modo\.modo\)/);
  assert.match(cuerpo, /putIdeModo\(sessionId, \{ modo: nuevo \}\)/);
});

test("el botón y el menú muestran el modo VIGENTE de verdad (GET .../modo), no el viejo modo_plan", () => {
  assert.match(componente, /modo\?\.modo \?\? "manual"/);
  assert.match(componente, /NOMBRE_DE_MODO\[modoVigente\]/);
});

// Este test decía antes que "el control" entero se deshabilita sin sesión, y
// afirmaba `disabled={deshabilitado}` a secas. Eso NO era una salvaguarda: era
// el bug escrito como requisito. El dueño lo reportó dos veces ("Bajo Medio y
// alto no sirve", "los 3 están deshabilitados") mientras la suite seguía verde,
// porque el test bloqueaba justo la conducta rota. Ahora distingue los dos
// controles, que no tienen el mismo criterio.

test("el MENÚ DE MODO sí depende de la sesión: sin ella no hay a qué agente cambiarle el modo", () => {
  // El botón que abre el menú -- y solo ese -- se ata a `deshabilitado`.
  assert.match(componente, /disabled=\{deshabilitado\}/);
});

test("el ESFUERZO se puede elegir SIEMPRE, con sesión o sin ella", () => {
  // La regresión concreta: `deshabilitado ||` colado en el disabled de los tres
  // botones de nivel. `elegirEsfuerzo` ya resuelve el caso sin sesión guardando
  // la preferencia local, así que atarlos a `sessionId` los apagaba justo en
  // reposo -- el momento en que uno decide si la tarea va rápida o pensada.
  const botonNivel = componente.match(/disabled=\{[^}]*\}\s*\n\s*onClick=\{\(\) => void elegirEsfuerzo/);
  assert.ok(botonNivel, "no se encontró el botón de nivel de esfuerzo");
  assert.doesNotMatch(botonNivel[0], /deshabilitado/);
  // `saving` sí frena: evita mandar dos cambios pisándose.
  assert.match(botonNivel[0], /saving/);
});

test("sin sesión, el esfuerzo no miente: dice que se aplicará al arrancar, no que esté bloqueado", () => {
  // El menú de MODO sí conserva su aviso de "disponible con sesión" (línea
  // aparte, ver el test de arriba): ahí es cierto. Lo que no puede seguir
  // diciendo eso es el nivel de esfuerzo, que ahora se elige siempre.
  assert.match(componente, /Se aplicará en cuanto arranque la sesión/);
  const tituloNivel = componente.match(/title=\{deshabilitado \?[^}]*\}\s*\n\s*className=\{cx\(\s*\n\s*"px-2 py-1/);
  assert.ok(tituloNivel, "no se encontró el title del botón de nivel");
  assert.doesNotMatch(tituloNivel[0], /Disponible en cuanto arranque/);
});

// Este test clavó dos veces la frase equivocada, en direcciones opuestas.
// Primero decía "sube el presupuesto de tokens" (verdad del motor viejo).
// Después, cuando `_cambiar_modelo_en_vivo`/`ide_sessions.py` empezaron a
// elegir el MODELO por nivel de esfuerzo (`modelo_para(perfil, nivel)`), el
// test se reescribió para exigir justo esa frase ("cambia a un modelo más
// capaz") -- pero esa conducta era el bug que el dueño reportó ("la otra IA
// dañó Workers AI"): el selector de modelo elegía un modelo y el nivel de
// esfuerzo lo pisaba sin avisar. El diseño correcto, decidido por el dueño,
// es el contrario: el SELECTOR DE MODELO manda qué modelo corre, siempre;
// el ESFUERZO solo decide cuánto piensa ese modelo (`reasoning_effort`), sin
// tocar cuál es. Este test ya no puede exigir la frase vieja sin volver a
// clavar el mismo contrato equivocado por tercera vez.
test("explica en la interfaz que el esfuerzo hace pensar más al modelo elegido, sin cambiarlo por otro", () => {
  assert.match(componente, /piense más/);
  assert.doesNotMatch(componente, /cambia a un modelo más capaz/, "esa frase describía el bug (el nivel pisaba el modelo elegido), no el diseño correcto");
  assert.doesNotMatch(componente, /presupuesto de tokens/);
});

// --- Reposo.tsx: el selector vive en la fila de controles del compositor ---

test("Composer monta SelectorModo con la sesión del agente activo", () => {
  const composer = source("./src/components/ide/estado/Reposo.tsx");
  // Sin exigir el cierre `/>` justo después: la fila de controles le pasa
  // `className="shrink-0"` (degradación por prioridad de la fila, encargo
  // "que se adapte a cualquier ancho") -- lo que importa acá es que siga
  // recibiendo la sesión del agente activo, no la forma exacta del JSX.
  assert.match(composer, /<SelectorModo\s+sessionId=\{estado\.agent\?\.id \?\? null\}/);
});

// --- Encargo "la fila de controles no se contrae, se sale y desaparece" ----
//
// Sin `@tailwindcss/container-queries` instalado (Tailwind 3.4 con
// `plugins: []` -- ver tailwind.config.ts), el arreglo usa `flex-wrap` +
// `min-w-0` + `truncate` como red de seguridad: la fila cae a una segunda
// línea en vez de salirse del contenedor. Ver el comentario de
// `Composer` en Reposo.tsx para el porqué completo.

test("la fila de controles puede caer a una segunda línea (flex-wrap) en vez de desbordarse", () => {
  const composer = source("./src/components/ide/estado/Reposo.tsx");
  assert.match(
    composer,
    /className="flex flex-wrap items-center gap-x-2 gap-y-2 border-t border-slate-200\/70 pt-2/,
    "la fila exterior de controles (\"+\" ... Enviar) debe admitir flex-wrap",
  );
  // El grupo de la derecha (Modo/Esfuerzo, selector de modelo, ayuda, Enviar)
  // también debe poder envolver y encogerse -- si no, sigue desbordando aunque
  // la fila exterior ya lo admita.
  assert.match(composer, /className="flex min-w-0 flex-1 flex-wrap items-center justify-end gap-x-2 gap-y-2"/);
});

test("Enviar y \"+\" nunca dependen de un breakpoint que los esconda", () => {
  const composer = source("./src/components/ide/estado/Reposo.tsx");
  // El className de cada botón es lo que precede a su propio `aria-label`
  // dentro de la misma etiqueta -- ninguno de los dos debe traer `hidden`.
  const claseAntesDe = (marcador) => composer.split(marcador)[0].split('className="').at(-1);
  assert.doesNotMatch(claseAntesDe('aria-label="Añadir contexto"'), /\bhidden\b/);
  assert.doesNotMatch(
    claseAntesDe('aria-label={estado.agent && isLive(estado.agent) ? "Mandar a la cola del agente" : "Iniciar trabajo"}'),
    /\bhidden\b/,
  );
});

test("el selector de modelo ya NO se esconde entero bajo un breakpoint -- se acorta con elipsis", () => {
  const composer = source("./src/components/ide/estado/Reposo.tsx");
  const etiqueta = composer.split('aria-label="Modelo de Forge Studio"')[0].split("<label")
    .at(-1);
  // El bug original: `hidden min-w-[13rem] sm:block` -- el selector entero
  // desaparecía por debajo de `sm` y no cabía por encima porque no encogía.
  assert.doesNotMatch(etiqueta, /\bhidden\b/, "el selector de modelo debe estar siempre montado, nunca `hidden`");
  assert.doesNotMatch(etiqueta, /min-w-\[13rem\]/, "el ancho mínimo fijo de 13rem era lo que forzaba el desborde");
  assert.match(etiqueta, /truncate/, "sin espacio para el nombre completo, debe acortarse con elipsis");
});

// Segunda vuelta sobre el mismo selector: `truncate` sin espacio de sobra no
// alcanza. `w-20` (80px) truncaba SIEMPRE con nombres reales de Workers AI
// ("Kimi K2.7 Code", "Nemotron 3 120B") -- "de un vistazo" no se cumplía
// porque no había vistazo que mostrar. Estos anchos son intencionalmente más
// grandes que antes, y `title` es la red de seguridad para cuando aun así no
// alcanza (panel lateral angosto).

test("el selector de modelo arranca con ancho suficiente para nombres reales, no el w-20 que truncaba siempre", () => {
  const composer = source("./src/components/ide/estado/Reposo.tsx");
  const etiqueta = composer.split('aria-label="Modelo de Forge Studio"')[0].split("<label")
    .at(-1);
  assert.doesNotMatch(etiqueta, /\bw-20\b/, "80px truncaba nombres como \"Kimi K2.7 Code\" en el primer breakpoint");
  assert.match(etiqueta, /\bw-32\b/, "el ancho base debe crecer para dar lugar al nombre completo");
});

test("el selector de modelo repite el nombre completo en `title` -- red de seguridad si igual se corta", () => {
  const composer = source("./src/components/ide/estado/Reposo.tsx");
  const etiqueta = composer.split('aria-label="Modelo de Forge Studio"')[0].split("<label")
    .at(-1);
  assert.match(etiqueta, /title=\{estado\.ideModels\.find/, "sin `title`, un nombre truncado no se puede leer completo de ninguna forma");
});

test("Esfuerzo se vuelve compacto (sin los rótulos descriptivos) antes de desaparecer del todo", () => {
  const selector = source("./src/components/ide/SelectorModo.tsx");
  // "más rápido"/"más inteligente" ceden en `md`; los botones B/M/A siguen
  // visibles desde `sm` -- degradación en dos pasos, no todo-o-nada.
  assert.match(selector, /"hidden text-\[10px\][^"]*\bmd:inline"[^>]*>más rápido</);
  assert.match(selector, /"hidden text-\[10px\][^"]*\bmd:inline"[^>]*>más inteligente</);
});

// --- Encargo "uno puede cambiar el effort siempre que se quiera o se pueda,
// ... no simplemente una vez al día ni una vez en la conversación" ----------

test("el grupo de esfuerzo ya NO se esconde entero bajo un breakpoint -- antes desaparecía por debajo de sm", () => {
  const selector = source("./src/components/ide/SelectorModo.tsx");
  assert.doesNotMatch(
    selector,
    /hidden items-center gap-1 sm:flex/,
    "ese breakpoint escondía TODO el control de esfuerzo por debajo de 640px, justo lo contrario de que sobreviva a una ventana pequeña",
  );
});

test("deshabilitado solo mira si hay sesión -- nunca si el agente está ocupado trabajando", () => {
  const selector = source("./src/components/ide/SelectorModo.tsx");
  assert.match(selector, /const deshabilitado = !sessionId;/);
  // Ni `ocupado` ni `saving` (que solo dura lo que tarda el PUT) entran en el
  // cálculo de `deshabilitado`: cambiar a mitad de trabajo es el caso de uso
  // que pidió el dueño, no una excepción a bloquear.
  assert.doesNotMatch(selector, /const deshabilitado = [^;]*ocupado/);
});

test("SelectorModo recibe `ocupado` y no lo usa para deshabilitar los botones de nivel", () => {
  const selector = source("./src/components/ide/SelectorModo.tsx");
  assert.match(selector, /ocupado\?:\s*boolean/);
  // Antes esto fijaba la cadena EXACTA del `disabled`, incluido el
  // `deshabilitado ||` que era el bug. Un test que clava el texto literal deja
  // de comprobar la intención y pasa a impedir el arreglo. Ahora se comprueba
  // lo único que de verdad importa aquí: que `ocupado` no apague el botón.
  const botonNivel = selector.match(/disabled=\{[^}]*\}\s*\n\s*onClick=\{\(\) => void elegirEsfuerzo/);
  assert.ok(botonNivel, "no se encontró el botón de nivel de esfuerzo");
  assert.doesNotMatch(botonNivel[0], /ocupado/, "el botón de cada nivel no debe depender de `ocupado`");
});

test("Composer manda `ocupado` con isLive(estado.agent) -- la misma señal que ya usa para el botón Detener", () => {
  const composer = source("./src/components/ide/estado/Reposo.tsx");
  assert.match(composer, /ocupado=\{Boolean\(estado\.agent && isLive\(estado\.agent\)\)\}/);
});

test("un cambio de esfuerzo mandado mientras el agente trabaja enciende el aviso de 'próxima vuelta', no antes", () => {
  const selector = source("./src/components/ide/SelectorModo.tsx");
  // Se congela `ocupado` al momento de la petición (el prop puede cambiar
  // mientras el PUT viaja) y solo entonces se enciende el aviso.
  assert.match(selector, /const enVueloAlPedir = ocupado;/);
  assert.match(selector, /if \(enVueloAlPedir\) setAvisoProximaVuelta\(true\);/);
  assert.match(selector, />\s*Aplica en la próxima vuelta\s*</);
});

test("el aviso de 'próxima vuelta' se apaga solo cuando la vuelta en curso termina, nunca antes de tiempo", () => {
  const selector = source("./src/components/ide/SelectorModo.tsx");
  assert.match(selector, /if \(!ocupado\) setAvisoProximaVuelta\(false\);/);
  // También se limpia al cambiar de sesión -- un aviso de OTRA sesión no debe
  // seguir colgado.
  assert.match(selector, /setAvisoProximaVuelta\(false\);\s*\n\s*\}, \[sessionId\]\);/);
});

test("si el PUT falla, el aviso no se enciende (ya hay un error visible; encender los dos sería mentir dos veces)", () => {
  const selector = source("./src/components/ide/SelectorModo.tsx");
  const cuerpo = selector.split("async function elegirEsfuerzo")[1].split("\n  const deshabilitado = ")[0];
  const indiceCatch = cuerpo.indexOf("} catch (err) {");
  const indiceAviso = cuerpo.indexOf("setAvisoProximaVuelta(true)");
  assert.ok(indiceCatch > -1 && indiceAviso > -1 && indiceAviso < indiceCatch);
});
