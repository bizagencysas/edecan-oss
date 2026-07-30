"""Banco de tareas reales sobre el monorepo `edecan`.

Quince trabajos que este repositorio necesita de verdad, encontrados leyéndolo
—no inventados— y con criterio de aceptación EJECUTABLE. Ninguna tarea entra
aquí sin haber comprobado, sobre el repo intacto, que su criterio principal
FALLA hoy: una tarea cuyo criterio ya pasa no mide nada.

## Cómo se comprueba cada tarea

La mayoría de los criterios son un guion de `bench/checks/`. Se prefirió eso a
"escribe un test y que pase" porque un agente que redacta su propio criterio
puede aprobarse solo. Los guiones:

- corren con `uv run --all-packages` porque el `pyproject.toml` de la raíz es
  un contenedor sin dependencias propias: `uv run` a secas PODA los paquetes
  editables del workspace (ver la nota del `Makefile`). El flag no es adorno;
  sin él ni siquiera importa `edecan_llm`;
- no tocan la red: los adaptadores HTTP se ejercitan con
  `httpx.MockTransport` y AWS/Postgres con dobles en memoria;
- en las dos tareas de "falta un test" no se conforman con que el archivo
  exista: mutan el módulo bajo prueba, una función por vez, y exigen que la
  suite nueva se ponga roja. Un `assert True` no pasa de ahí. El módulo
  original se restaura siempre.

## Reparto

5 `trivial` (un archivo, sin dependencias), 8 `standard` (varios archivos con
pruebas existentes) y 2 `guarded` (tocan una migración o el despliegue). Los
presupuestos van con la clase: a 0,95 USD/MTok de entrada y 4,00 de salida
—precio medido de `@cf/moonshotai/kimi-k2.7-code`— 0,12 USD dan para un
arreglo de un archivo y 1,20 para una migración con su guardarraíl.

`archivos_pista` NO se le entrega al agente: sirve para medir si su búsqueda
encontró el sitio correcto, que es un fallo muy distinto de no saber
arreglarlo.
"""

from __future__ import annotations

from edecan_forge_probe.modelcard import BenchTask, Criterio

_CHECKS = "packages/forge-probe/bench/checks"

_UV = ("uv", "run", "--all-packages")


def _guion(nombre: str, descripcion: str, *, timeout_s: int = 300) -> Criterio:
    """Criterio principal: el guion de comprobación de `bench/checks/`."""
    return Criterio(
        kind="command",
        descripcion=descripcion,
        comando=[*_UV, "python", f"{_CHECKS}/{nombre}"],
        timeout_s=timeout_s,
        debe_fallar_antes=True,
    )


def _guion_node(nombre: str, descripcion: str, *, timeout_s: int = 300) -> Criterio:
    """Igual que `_guion`, para los guiones de la interfaz web (Node ≥22)."""
    return Criterio(
        kind="command",
        descripcion=descripcion,
        comando=["node", f"{_CHECKS}/{nombre}"],
        timeout_s=timeout_s,
        debe_fallar_antes=True,
    )


def _sin_regresion(ruta: str, *, timeout_s: int = 300, ya_falla: bool = False) -> Criterio:
    """Criterio de no-regresión: una suite existente que debe seguir verde."""
    return Criterio(
        kind="command",
        descripcion=f"la suite existente de `{ruta}` sigue pasando",
        comando=[*_UV, "pytest", ruta, "-q"],
        timeout_s=timeout_s,
        debe_fallar_antes=ya_falla,
    )


def _ruff(ruta: str) -> Criterio:
    return Criterio(
        kind="command",
        descripcion=f"`{ruta}` pasa ruff con las reglas del repo",
        comando=[*_UV, "ruff", "check", ruta],
        timeout_s=120,
        debe_fallar_antes=False,
    )


def _web_unit() -> Criterio:
    return Criterio(
        kind="command",
        descripcion="la suite de `apps/web` sigue pasando entera",
        comando=["npm", "--prefix", "apps/web", "run", "test:unit"],
        timeout_s=600,
        debe_fallar_antes=False,
    )


# --------------------------------------------------------------------------- #
# trivial — un archivo, sin dependencias
# --------------------------------------------------------------------------- #

_TRIVIAL: tuple[BenchTask, ...] = (
    BenchTask(
        id="edecan-toolkit-all-ordenado",
        repo="edecan",
        titulo="La lista de exportaciones del toolkit está desordenada",
        enunciado=(
            "El `__all__` de `edecan_toolkit` se fue llenando a mano y ya no está en "
            "orden alfabético: hay nombres metidos donde tocó. Déjalo ordenado, sin "
            "perder ni agregar exportaciones."
        ),
        clase="trivial",
        lenguajes=["python"],
        archivos_pista=["packages/toolkit/edecan_toolkit/__init__.py"],
        criterios=[
            _guion(
                "check_toolkit_all_ordenado.py",
                "`__all__` está ordenado, sin repetidos y todos sus nombres existen",
            ),
            _sin_regresion("packages/toolkit"),
            _ruff("packages/toolkit"),
        ],
        presupuesto_usd=0.12,
        max_turnos=12,
    ),
    BenchTask(
        id="edecan-finanzas-fecha-no-texto",
        repo="edecan",
        titulo="Registrar un gasto revienta si la fecha no viene como texto",
        enunciado=(
            "Si el modelo manda la fecha de una transacción como número (20260727) o "
            "como lista en vez de como texto 'YYYY-MM-DD', la herramienta de "
            "finanzas revienta con una excepción en lugar de contestar que la fecha "
            "no sirve, como ya hace cuando le mandas una fecha mal escrita. Que "
            "cualquier fecha inválida se responda igual, con un mensaje que hable de "
            "la fecha, y sin llegar a la base de datos."
        ),
        clase="trivial",
        lenguajes=["python"],
        archivos_pista=["packages/toolkit/edecan_toolkit/finanzas.py"],
        criterios=[
            _guion(
                "check_finanzas_fecha_no_texto.py",
                "una fecha no textual devuelve un ToolResult explicativo sin tocar la sesión",
            ),
            _sin_regresion("packages/toolkit/tests/test_finanzas.py"),
            _ruff("packages/toolkit"),
        ],
        presupuesto_usd=0.12,
        max_turnos=12,
    ),
    BenchTask(
        id="edecan-bedrock-stream-asynciterator",
        repo="edecan",
        titulo="El stub de Bedrock miente sobre su propio tipo",
        enunciado=(
            "`BedrockProvider.stream` promete devolver un iterador asíncrono, igual "
            "que los demás proveedores, pero lanza en el momento de la llamada. "
            "Quien escribe `try: ... async for chunk in provider.stream(req)` con el "
            "try alrededor del bucle no atrapa nada, y el tipo declarado es falso. "
            "Haz que se comporte como los otros adaptadores: llamar devuelve el "
            "iterador y el error sale al iterarlo. Bedrock sigue sin implementarse: "
            "no integres nada."
        ),
        clase="trivial",
        lenguajes=["python"],
        archivos_pista=["packages/llm/edecan_llm/bedrock.py"],
        criterios=[
            _guion(
                "check_bedrock_stream_asincrono.py",
                "stream() devuelve un iterador asíncrono y NotImplementedError sale al iterar",
            ),
            _sin_regresion("packages/llm", timeout_s=600),
            _ruff("packages/llm"),
        ],
        presupuesto_usd=0.12,
        max_turnos=12,
    ),
    BenchTask(
        id="edecan-web-format-vacio",
        repo="edecan",
        titulo="La web muestra 0,00 US$ donde no hay dato",
        enunciado=(
            'Cuando el API devuelve un campo numérico vacío (la cadena ""), la '
            "interfaz lo pinta como «0,00 US$» o «0». Eso es un dato inventado: "
            "debería mostrar el mismo guion que ya usa cuando el campo viene nulo. "
            "Un cero de verdad sí tiene que seguir viéndose como cero."
        ),
        clase="trivial",
        lenguajes=["typescript"],
        archivos_pista=["apps/web/src/lib/format.ts"],
        criterios=[
            _guion_node(
                "check_web_format_vacio.mjs",
                "las cadenas vacías o en blanco se muestran como «sin dato», y el cero no",
            ),
            _web_unit(),
        ],
        presupuesto_usd=0.12,
        max_turnos=12,
    ),
    BenchTask(
        id="edecan-detect-ollama-barra-final",
        repo="edecan",
        titulo="Ollama no se detecta si su URL termina en barra",
        enunciado=(
            "Un usuario configuró OLLAMA_BASE_URL copiándola del navegador, con la "
            "barra final, y la pantalla de Configuración dice que Ollama no está "
            "corriendo aunque sí lo está. Arréglalo para que la barra final (o "
            "varias) no cambien nada, ni en la URL que se consulta ni en la que se "
            "reporta de vuelta."
        ),
        clase="trivial",
        lenguajes=["python"],
        archivos_pista=["packages/llm/edecan_llm/detect.py"],
        criterios=[
            _guion(
                "check_detect_ollama_url.py",
                "la URL consultada no duplica la barra y la detección sigue funcionando",
            ),
            _sin_regresion("packages/llm/tests/test_detect.py"),
            _ruff("packages/llm"),
        ],
        presupuesto_usd=0.12,
        max_turnos=12,
    ),
)


# --------------------------------------------------------------------------- #
# standard — varios archivos, con pruebas existentes alrededor
# --------------------------------------------------------------------------- #

_STANDARD: tuple[BenchTask, ...] = (
    BenchTask(
        id="edecan-usage-tokens-cacheados",
        repo="edecan",
        titulo="No sabemos cuánto del prompt vino de caché, y lo cobramos caro",
        enunciado=(
            "Los proveedores compatibles con OpenAI reportan, dentro de `usage`, un "
            "`prompt_tokens_details.cached_tokens`: la parte del prompt que se "
            "resolvió desde la caché de prefijo y que cuesta bastante menos (en el "
            "modelo que estamos usando, 0,19 USD/MTok contra 0,95). Hoy lo tiramos: "
            "quiero que `Usage` lleve un campo `cached_input_tokens` (0 por "
            "omisión), que el adaptador OpenAI-compatible lo rellene tanto en "
            "respuesta normal como en streaming, y que la estimación de costo lo "
            "cobre a su precio: `estimate` debe aceptar un parámetro `costos_cache` "
            "con el precio por millón de tokens cacheados de cada modelo. "
            "`input_tokens` sigue siendo el total del prompt, cacheado incluido. Si "
            "no hay precio de caché declarado para ese modelo, esos tokens se cobran "
            "al precio de entrada normal, como hoy."
        ),
        clase="standard",
        lenguajes=["python"],
        archivos_pista=[
            "packages/llm/edecan_llm/base.py",
            "packages/llm/edecan_llm/openai_compat.py",
            "packages/llm/edecan_llm/costs.py",
            "packages/llm/tests/test_llm_costs.py",
            "packages/llm/tests/test_llm_openai_compat.py",
        ],
        criterios=[
            _guion(
                "check_usage_tokens_cacheados.py",
                "los tokens cacheados se leen del proveedor y abaratan la estimación",
            ),
            _sin_regresion("packages/llm", timeout_s=600),
            _sin_regresion("packages/core", timeout_s=600),
            _ruff("packages/llm"),
        ],
        presupuesto_usd=0.60,
        max_turnos=40,
    ),
    BenchTask(
        id="edecan-openai-compat-reasoning-content",
        repo="edecan",
        titulo="Los modelos que razonan nos devuelven una respuesta vacía",
        enunciado=(
            "Los modelos con razonamiento siempre activo mandan lo que piensan en "
            "`message.reasoning_content`, aparte de `message.content`. Cuando se les "
            "acaba el presupuesto de salida razonando, llega el contenido vacío y el "
            "razonamiento lleno, y desde nuestro lado eso es indistinguible de «el "
            "modelo no dijo nada». Quiero que el adaptador OpenAI-compatible lo "
            "conserve: un campo `reasoning` en la respuesta de completions, y en "
            "streaming un chunk propio de tipo `reasoning` con ese texto. El texto "
            "visible no se toca: el razonamiento no puede acabar mezclado con la "
            "respuesta al usuario."
        ),
        clase="standard",
        lenguajes=["python"],
        archivos_pista=[
            "packages/llm/edecan_llm/base.py",
            "packages/llm/edecan_llm/openai_compat.py",
            "packages/llm/tests/test_llm_openai_compat.py",
            "packages/core/edecan_core/agent.py",
        ],
        criterios=[
            _guion(
                "check_reasoning_content.py",
                "el razonamiento llega por su propio canal y no contamina el texto visible",
            ),
            _sin_regresion("packages/llm", timeout_s=600),
            _sin_regresion("packages/core", timeout_s=600),
            _ruff("packages/llm"),
        ],
        presupuesto_usd=0.60,
        max_turnos=40,
    ),
    BenchTask(
        id="edecan-prompted-tools-arguments-texto",
        repo="edecan",
        titulo="Las herramientas por prompt se ejecutan sin sus argumentos",
        enunciado=(
            "Con los proveedores que no tienen tool-calling nativo pedimos el JSON "
            '`{"tool_call": {...}}` por prompt. Los modelos escriben muy a menudo '
            "`arguments` como una cadena con JSON adentro —es lo que hace la propia "
            "API de OpenAI— y nosotros lo descartamos en silencio: la herramienta "
            "termina ejecutándose con los argumentos vacíos, que es la peor forma "
            "posible de fallar. Acepta ese caso, incluido cuando dentro del JSON hay "
            "un bloque de código con saltos de línea y llaves. Si la cadena no es "
            "JSON válido, se queda vacía como hoy, sin lanzar."
        ),
        clase="standard",
        lenguajes=["python"],
        archivos_pista=[
            "packages/llm/edecan_llm/prompted_tools.py",
            "packages/llm/tests/test_claude_cli.py",
        ],
        # `packages/llm/tests/test_codex_cli.py` estaba aquí y se ha ido del árbol
        # junto con `edecan_llm/codex_cli.py`. Una pista a un archivo inexistente
        # no mide recall: lo hunde sin que nada avise, porque el agente no puede
        # leer lo que no está.
        criterios=[
            _guion(
                "check_prompted_tools_arguments.py",
                "`arguments` en texto JSON se parsea sin perder argumentos",
            ),
            _sin_regresion("packages/llm", timeout_s=600),
            _ruff("packages/llm"),
        ],
        presupuesto_usd=0.55,
        max_turnos=40,
    ),
    BenchTask(
        id="edecan-calculadora-exponente-acotado",
        repo="edecan",
        titulo="La calculadora «segura» se cuelga con una potencia grande",
        enunciado=(
            "La calculadora del toolkit presume de evaluar aritmética de forma "
            "segura sin `eval`, pero `9**9**9` deja el proceso quemando CPU y "
            "memoria hasta que el sistema lo mata. Como la invoca el modelo con "
            "texto del usuario, es una caída del servicio a un turno de distancia. "
            "Rechaza las potencias desmedidas con el mismo error que ya usas para lo "
            "que no es aritmética pura, y hazlo antes de calcular nada. La "
            "aritmética normal, `2**64` incluido, tiene que seguir funcionando igual."
        ),
        clase="standard",
        lenguajes=["python"],
        archivos_pista=[
            "packages/toolkit/edecan_toolkit/utilidades.py",
            "packages/toolkit/tests/test_utilidades.py",
        ],
        criterios=[
            _guion(
                "check_calculadora_exponente.py",
                "las potencias desmedidas se rechazan rápido y la aritmética normal no cambia",
                timeout_s=180,
            ),
            _sin_regresion("packages/toolkit"),
            _ruff("packages/toolkit"),
        ],
        presupuesto_usd=0.55,
        max_turnos=40,
    ),
    BenchTask(
        id="edecan-test-output-safety",
        repo="edecan",
        titulo="Falta la prueba del filtro de autonarración",
        enunciado=(
            "`edecan_llm.output_safety` decide qué texto del modelo ve la persona y "
            "cuál se corta por ser autonarración («debo responder...»). No tiene "
            "módulo de pruebas propio: solo se ejercita de refilón desde las pruebas "
            "del CLI de Claude. Escribe "
            "`packages/llm/tests/test_output_safety.py` cubriendo de verdad sus dos "
            "funciones públicas, incluyendo el caso en que NO se debe recortar nada."
        ),
        clase="standard",
        lenguajes=["python"],
        archivos_pista=[
            "packages/llm/edecan_llm/output_safety.py",
            "packages/llm/tests/test_output_safety.py",
            "packages/llm/tests/test_claude_cli.py",
        ],
        criterios=[
            _guion(
                "check_test_output_safety.py",
                "las pruebas nuevas pasan y matan un mutante por cada función pública",
                timeout_s=900,
            ),
            Criterio(
                kind="file_exists",
                descripcion="existe el módulo de pruebas",
                ruta="packages/llm/tests/test_output_safety.py",
                debe_fallar_antes=True,
            ),
            _ruff("packages/llm"),
        ],
        presupuesto_usd=0.75,
        max_turnos=45,
    ),
    BenchTask(
        id="edecan-test-web-format",
        repo="edecan",
        titulo="Falta la prueba de los formateadores de la interfaz",
        enunciado=(
            "`apps/web/src/lib/format.ts` decide cómo ve el usuario cada fecha, cada "
            "monto y cada cifra de la aplicación, y no tiene ninguna prueba. Escribe "
            "`apps/web/format.test.mjs` (mismo estilo que las demás pruebas de "
            "`apps/web`, con `node:test`) cubriendo todos los helpers que exporta, "
            "cada uno con el caso normal y el caso sin dato."
        ),
        clase="standard",
        lenguajes=["typescript", "javascript"],
        archivos_pista=[
            "apps/web/src/lib/format.ts",
            "apps/web/format.test.mjs",
            "apps/web/package.json",
        ],
        criterios=[
            _guion_node(
                "check_test_web_format.mjs",
                "las pruebas nuevas pasan y matan un mutante por cada helper exportado",
                timeout_s=900,
            ),
            Criterio(
                kind="file_exists",
                descripcion="existe el módulo de pruebas",
                ruta="apps/web/format.test.mjs",
                debe_fallar_antes=True,
            ),
            _web_unit(),
        ],
        presupuesto_usd=0.75,
        max_turnos=45,
    ),
    BenchTask(
        id="edecan-api-usage-desglose",
        repo="edecan",
        titulo="El consumo del mes no se puede ver por día",
        enunciado=(
            "`GET /v1/usage` solo da el total del mes por tipo de consumo. Cuando a "
            "alguien se le dispara el gasto no hay forma de ver qué día pasó sin "
            "entrar a la base a mano. Agrega `GET /v1/usage/desglose`, con la misma "
            "autenticación que el resto, que devuelva `plan_key`, `period_start` y "
            "una lista `por_dia` ordenada de más viejo a más nuevo, donde cada "
            'elemento sea `{"fecha": "AAAA-MM-DD", "kinds": {tipo: total}}`. '
            "Solo el mes en curso y solo el tenant que pregunta. Los dobles en "
            "memoria de la suite de `apps/api` tienen que soportarlo igual que el "
            "repositorio real."
        ),
        clase="standard",
        lenguajes=["python"],
        archivos_pista=[
            "apps/api/edecan_api/routers/usage.py",
            "apps/api/edecan_api/repo.py",
            "apps/api/tests/api_fakes.py",
            "apps/api/tests/test_usage.py",
        ],
        criterios=[
            _guion(
                "check_api_usage_desglose.py",
                "el endpoint agrupa por día, respeta el mes en curso y aísla el tenant",
                timeout_s=600,
            ),
            _sin_regresion("apps/api/tests/test_usage.py"),
            _sin_regresion("apps/api/tests/test_v2_mounting.py"),
            _ruff("apps/api"),
        ],
        presupuesto_usd=0.80,
        max_turnos=50,
    ),
    BenchTask(
        id="edecan-rls-social-editorial-profiles",
        repo="edecan",
        titulo="El guardarraíl de aislamiento multi-tenant está en rojo",
        enunciado=(
            "La prueba que cruza las tablas con Row Level Security de las "
            "migraciones contra las del ORM está fallando en `main`. Ese cross-check "
            "existe para que ninguna tabla con `tenant_id` se quede sin su política "
            "`tenant_isolation`, que es el riesgo más serio del modelo multi-tenant. "
            "Averigua por qué falla y déjalo verde respetando la convención que el "
            "propio módulo documenta: cada migración es una foto fija de lo que ESA "
            "migración crea, así que no se toca lo que ya declararon las anteriores."
        ),
        clase="standard",
        lenguajes=["python", "sql"],
        archivos_pista=[
            "packages/db/tests/test_migration_rls_tables.py",
            "packages/db/alembic/versions/0025_social_editorial_profiles.py",
            "packages/db/edecan_db/models.py",
        ],
        criterios=[
            Criterio(
                kind="command",
                descripcion="el cross-check de RLS entre migraciones y ORM pasa",
                comando=[*_UV, "pytest", "packages/db/tests/test_migration_rls_tables.py", "-q"],
                timeout_s=300,
                debe_fallar_antes=True,
            ),
            _sin_regresion("packages/db", ya_falla=True),
            _ruff("packages/db"),
        ],
        presupuesto_usd=0.55,
        max_turnos=40,
    ),
)


# --------------------------------------------------------------------------- #
# guarded — migración y despliegue: exigen aprobación antes de aplicarse
# --------------------------------------------------------------------------- #

_GUARDED: tuple[BenchTask, ...] = (
    BenchTask(
        id="edecan-migracion-usage-costo-usd",
        repo="edecan",
        titulo="Los eventos de consumo no guardan cuánto costaron",
        enunciado=(
            "`usage_events` guarda la cantidad (tokens, segundos, bytes) pero no el "
            "dinero. Reconstruir el costo después exige saber qué precio regía ese "
            "día, y los precios cambian: hay que escribirlo en el momento. Agrega a "
            "esa tabla una columna `costo_usd` numérica, no nula y con valor por "
            "defecto 0 para las filas que ya existen, en el modelo y en una "
            "migración nueva encadenada después de la última. La migración tiene que "
            "poder revertirse."
        ),
        clase="guarded",
        lenguajes=["python", "sql"],
        archivos_pista=[
            "packages/db/edecan_db/models.py",
            "packages/db/alembic/versions/0025_social_editorial_profiles.py",
            "packages/db/tests/test_db_models.py",
        ],
        criterios=[
            _guion(
                "check_migracion_usage_costo.py",
                "la columna está en el ORM y en una migración reversible encadenada a 0025",
            ),
            _sin_regresion("packages/db/tests/test_db_models.py"),
            _sin_regresion("packages/db/tests/test_models_v2.py"),
            _ruff("packages/db"),
        ],
        presupuesto_usd=1.20,
        max_turnos=60,
    ),
    BenchTask(
        id="edecan-edge-visibility-timeout",
        repo="edecan",
        titulo="Un trabajo largo del nodo de continuidad se ejecuta dos veces",
        enunciado=(
            "La capa de continuidad en AWS reserva cada trabajo en la cola con cinco "
            "minutos de invisibilidad, escritos a mano en el código del Lambda. Una "
            "tarea del nodo residente que tarde más vuelve a la cola y se ejecuta dos "
            "veces, y no hay forma de ajustarlo sin volver a desplegar. Que el valor "
            "venga de una variable de entorno `CLAIM_VISIBILITY_TIMEOUT_SECONDS`, "
            "con los mismos cinco minutos por omisión, y que la plantilla de "
            "despliegue la declare."
        ),
        clase="guarded",
        lenguajes=["python", "yaml"],
        archivos_pista=[
            "infra/aws/edge/src/handler.py",
            "infra/aws/edge/template.yml",
            "infra/aws/edge/tests/test_handler.py",
            "infra/aws/edge/README.md",
        ],
        criterios=[
            _guion(
                "check_edge_visibility_timeout.py",
                "el timeout de reserva viene del entorno y está declarado en template.yml",
            ),
            _sin_regresion("infra/aws/edge/tests"),
            _ruff("infra/aws/edge"),
        ],
        presupuesto_usd=1.20,
        max_turnos=60,
    ),
)


TAREAS: tuple[BenchTask, ...] = _TRIVIAL + _STANDARD + _GUARDED


def tareas(clase: str | None = None) -> tuple[BenchTask, ...]:
    """Devuelve el banco entero, o solo las tareas de una clase."""
    if clase is None:
        return TAREAS
    return tuple(t for t in TAREAS if t.clase == clase)


def por_id(task_id: str) -> BenchTask:
    """Busca una tarea por su `id`. Lanza `KeyError` si no existe."""
    for tarea in TAREAS:
        if tarea.id == task_id:
            return tarea
    raise KeyError(f"tarea desconocida: {task_id!r}")
