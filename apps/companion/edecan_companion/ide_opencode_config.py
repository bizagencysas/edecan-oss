"""Genera y verifica el ``opencode.json`` de un workspace -- el puente entre
``config/modelos.yml`` (la autoridad de qué modelos tiene el dueño) y opencode
(el motor nuevo del IDE, ver ``ide_opencode.py``).

# POR QUÉ existe este archivo

``ide_opencode.py`` (el cimiento del adaptador) YA prueba que opencode escribe
de verdad en disco, pero para que vea los modelos Workers AI del dueño
necesita un ``opencode.json`` en la raíz del workspace con el proveedor
``workersai`` declarado -- eso se comprobó a mano antes de escribir código
(ver el ejemplo de ``opencode.json`` en MEMORY.md de la migración). Ese
archivo NO se escribe a mano: se genera solo, a partir de dos fuentes que ya
existen y ya son la autoridad en el resto de Edecán:

1. Las credenciales de Cloudflare (``CLOUDFLARE_ACCOUNT_ID``/
   ``CLOUDFLARE_API_TOKEN``), leídas igual que el resto del monorepo --
   variable de entorno primero, ``.env`` de la raíz como respaldo (se
   reutiliza ``edecan_llm.workers_ai.cargar_dotenv``, el mismo parser que usa
   ``WorkersAIProvider``; este archivo no reimplementa un segundo parser de
   ``.env``).
2. El catálogo ``modelos_ide`` de ``config/modelos.yml``, leído con
   ``edecan_llm.task_router.modelos_ide_disponibles()`` -- la MISMA función
   que ya usa ``ide_contexto.py`` para el selector de modelos del IDE. Si el
   dueño agrega un modelo mañana a ese YAML, aparece aquí sin tocar código,
   porque no se copia la lista: se lee en cada llamada.

# La regla más importante de este archivo: nunca destruir la config del dueño

Si el workspace ya tiene un ``opencode.json`` con su propio bloque
``provider.workersai``, este módulo NO LO TOCA -- ni lo sobrescribe ni lo
"mejora". Solo avisa. Cualquier otra clave del archivo (``agent``, ``mode``,
otros proveedores) se preserva intacta; lo único que este módulo puede
AGREGAR es la clave ``provider.workersai`` cuando no existe. Esto es al pie
de la letra lo que pidió el encargo: "si el workspace ya tiene un
opencode.json escrito por el dueño, no lo pises. Combina o avisa; nunca
destruyas su configuración."

# CORRECCIÓN (verificación adversarial, hallazgo crítico): también escribe ``permission``

Un verificador independiente probó los cuatro modos con el ``opencode.json``
que esta misma función generaba y encontró que NINGUNO frenaba nada: Manual
escribía de inmediato, Auto borraba una carpeta con ``rm -rf`` sin preguntar,
Plan tocaba archivos. La causa, diagnosticada en el propio reporte: esta
función nunca escribía la clave ``permission`` -- sin ella opencode resuelve
``edit``/``bash``/``webfetch`` por su cuenta y NUNCA emite
``permission.v2.asked``, así que ``ide_opencode_permisos.PuenteDePermisos``
(que sí funciona -- se probó aparte, con un ``opencode.json`` que a mano SÍ
tenía ``permission: {"edit": "ask", "bash": "ask", "webfetch": "ask"}``)
nunca se llega a invocar. Es decir: el puente estaba bien, pero nada lo
despertaba porque el generador de config nunca activaba el modo "pregúntame"
de opencode.

Por eso ``generar_opencode_json`` ahora también agrega (con la MISMA regla de
"nunca pisar lo del dueño" que ``provider.workersai``: si la clave
``permission`` ya existe en el archivo, no se toca, se avisa) el bloque
``{"edit": "ask", "bash": "ask", "webfetch": "ask"}`` -- exactamente los tres
nombres de acción que ``ide_opencode_permisos.py`` ya documenta como
confirmados en vivo (``PermissionV2Request`` real con ``edit``/``bash``/
``webfetch``). Sin este bloque, generar un ``opencode.json`` con esta función
y luego cablear ``PuenteDePermisos`` encima produce el síntoma original del
dueño otra vez: "los modos no frenan nada" -- ahora con el motor nuevo.

# TRAMPA -- la otra cara de esta misma corrección, verificada en vivo dos veces

Un segundo verificador independiente disparó una edición de 3 archivos a la
vez contra un workspace con este ``opencode.json`` (con ``permission: ask``
ya escrito) pero usando SOLO ``ServidorOpencode.enviar_prompt`` +
``ServidorOpencode.eventos`` de ``ide_opencode.py`` -- sin arrancar
``PuenteDePermisos`` de ``ide_opencode_permisos.py``. Resultado: los 3
``tool.called(edit)`` llegaron, y ahí el stream de la sesión se quedó MUDO
para siempre -- sin ``tool.success``, sin ``tool.error``, sin ningún evento
terminal, y los 3 archivos con el mismo hash de antes. Se reprodujo
literalmente ese escenario para este archivo (workspace temporal propio,
Workers AI real, agente sin especificar y agente ``build`` explícito, ambos
casos) y es EXACTAMENTE lo esperado, no un bug: ``ide_opencode_permisos.py``
documenta en su hallazgo 1 que el stream POR SESIÓN
(``GET /session/{id}/event``, el que envuelve ``ServidorOpencode.eventos``)
NUNCA trae ``permission.v2.asked`` -- esos eventos SOLO viven en el bus
GLOBAL (``GET /api/event``, ``PuenteDePermisos.escuchar``). Con este
``opencode.json``, opencode literalmente PAUSA la ejecución de la herramienta
a media llamada esperando un ``POST .../permission/{id}/reply`` que nadie
manda -- no hay timeout de su lado, no hay error, se queda pausado para
siempre (se comprobó: incluso 90s después seguía sin emitir nada).

Conclusión operativa, la que de verdad importa para quien cablee esto en
``ide_runtime.py``: **escribir este ``opencode.json`` con ``permission: ask``
es una promesa a medias sin ``PuenteDePermisos`` corriendo en paralelo desde
antes de mandar el primer prompt.** Cualquier integración que use
``ServidorOpencode`` solo (sin el puente escuchando el bus global, aunque sea
solo en modo Auto para que se autoconceda) sobre un workspace generado por
esta función se cuelga en el primer ``edit``/``bash``/``webfetch`` real, sin
excepción, sin aviso. Ver el test
``test_sin_puente_una_edicion_se_cuelga_para_siempre_y_se_resuelve_solo_
cuando_el_puente_escucha`` en ``test_ide_opencode_permisos.py`` para la
reproducción exacta, con timeout acotado (no un cuelgue real de la prueba) en
ambas direcciones: colgada sin puente, resuelta con puente.

# Cómo se verificó "de verdad" (regla 6 del encargo, nada simulado)

Antes de escribir la función de comprobación se arrancó un ``opencode serve``
real con un ``opencode.json`` de prueba y se leyó su respuesta real:

- ``GET /config`` (RAÍZ del servidor, NO bajo ``/api`` -- distinto del resto
  de ``ide_opencode.py``, que sí vive bajo ``/api``; se comprobó que
  ``/api/provider`` devuelve ``{"data": []}`` hasta que hay una sesión activa,
  mientras que ``/config`` refleja lo leído del ``opencode.json`` del
  workspace en cuanto el proceso arranca) devolvió, con dos modelos
  declarados, exactamente ``provider.workersai.models`` con esos dos modelos
  -- de ahí sale ``comprobar_proveedor_registrado``.
- Esa respuesta es estructural: opencode repite lo que LEYÓ del JSON, sin
  llamar a Cloudflare. Para saber si el token de verdad sirve hace falta
  preguntarle a Cloudflare, no a opencode -- se probó a mano contra
  ``GET /client/v4/accounts/{cuenta}/ai/models/search`` (el mismo endpoint
  que ``config/modelos.yml`` ya documenta como autoridad de precios) con un
  token real (``success: true``) y con uno inventado
  (``success: false``, ``errors: [{"code": 9106, "message": "Authentication
  failed (status: 400)"}]``) -- ese es el mensaje real que propaga
  ``comprobar_credencial_cloudflare``.

# TERCERA CORRECCIÓN -- e intento de CUARTA que se revirtió: por qué el
# token SIGUE en claro en el archivo, con evidencia en contra de la
# referencia {env:...}

El encargo de esta ronda pidió sustituir el ``apiKey`` en claro por
``"{env:CLOUDFLARE_API_TOKEN}"`` -- la persona que lo escribió dijo haberlo
probado y visto funcionar. Esa comprobación (repetida acá primero,
``GET /config`` con un ``opencode.json`` de prueba) SÍ mostró el proveedor
registrado con sus modelos, e incluso devolvió el ``apiKey`` YA RESUELTO al
valor real -- así que a primera vista parecía funcionar. Pero ``/config`` es
un endpoint de LECTURA/depuración -- no es lo que opencode usa para
construir la llamada real a Cloudflare. Se hizo la prueba que de verdad
importa: crear una sesión, mandarle un prompt real y ver si el modelo
respondía. Con la referencia, la respuesta fue inmediata (2 ms) y siempre la
misma:

    Provider request failed with HTTP 401: {"result":null,"success":false,
    "errors":[{"code":10000,"message":"Authentication error"}]}

Se descartaron, en orden, las explicaciones más obvias:

- ¿La variable no llegaba al subproceso de Python? Se repitió la prueba con
  un ``opencode serve`` arrancado a mano desde bash, con
  ``CLOUDFLARE_API_TOKEN`` exportada ANTES de arrancar -- mismo 401
  instantáneo. No es un problema de herencia de entorno de
  ``asyncio.create_subprocess_exec``.
- ¿El nombre de variable era otro? Se leyó el binario de opencode
  (``strings ~/.opencode/bin/opencode``) y apareció un caso especial
  integrado para Cloudflare: ``apiKey: process.env.CLOUDFLARE_API_KEY ??
  o.apiKey`` (nombre ``CLOUDFLARE_API_KEY``, sin "_TOKEN", y solo para el
  proveedor Cloudflare NATIVO de opencode, con un id interno propio, no
  para un proveedor custom con el id que usa este módulo,
  ``workersai``). Se probó exportando exactamente esa variable -- mismo 401.
- Control final, limpio: el MISMO ``opencode.json``, con TODO igual, solo
  cambiando ``apiKey`` de la referencia al token literal -- ahí SÍ respondió
  de verdad (razonamiento + texto, "¡Hola! 👋 ¿En qué puedo ayudarte hoy?").

Conclusión, con cuatro repeticiones controladas: en la versión de opencode
instalada en esta Mac (``1.17.18``), ``{env:VARIABLE}`` dentro de
``provider.<id>.options.apiKey`` de un proveedor CUSTOM (``npm:
@ai-sdk/openai-compatible``, que es justo lo que usa este módulo) SOLO se
resuelve para el endpoint de lectura ``GET /config`` -- no para la
construcción real del cliente SDK que hace la llamada a Cloudflare. Esa
ruta usa el valor CRUDO de ``options.apiKey`` sin sustituir nada, así que
Cloudflare recibe literalmente la cadena ``"{env:CLOUDFLARE_API_TOKEN}"``
como token y la rechaza al instante.

**Por eso este módulo NO escribe la referencia como valor operativo:
seguiría dejando al agente completamente mudo en el primer prompt real,
que es justo el síntoma que todo este proyecto lleva rondas enteras
tratando de eliminar.** Regla 6 del encargo, "nada simulado, lo que no se
pueda hacer se declara" -- esto es exactamente eso: se intentó, se probó
contra opencode de verdad (no contra ``/config``), y no funciona. Ver
``pendiente`` para la recomendación (probar con una versión más nueva de
opencode, o abrir un issue upstream) y para dónde queda documentado el
código de la referencia por si algún día SÍ funciona.

Lo que SÍ se queda de la idea original, porque es real y se verificó que
funciona:

1. **``chmod 0600`` de verdad, con ``stat`` en el test.** El hallazgo
   original (punto 1 del encargo) seguía siendo cierto pase lo que pase con
   la referencia: el archivo salía en ``0o644``, no ``0o600``. Eso sí se
   corrigió acá -- y también en ``ide_opencode_motor.py``, cinturón y
   tirantes (encargo, punto 3): cualquiera que llame a
   ``generar_opencode_json`` directamente (sin pasar por ``MotorOpencode``,
   como hacen varios tests de este mismo archivo) recibe el archivo ya
   restringido. Se aplica después de CADA escritura real, y también sobre
   un archivo preexistente que esta llamada decide no tocar.
2. **``CLOUDFLARE_API_TOKEN`` sigue quedando puesto en ``os.environ`` de
   este proceso** al resolver la credencial -- ya no es indispensable para
   que opencode arranque (el token sigue viajando en el JSON), pero es
   higiene sana y barata: dejarlo disponible en el entorno no puede doler
   y sí ayuda si algo más en el proceso lo necesita, o si una versión futura
   de opencode arregla la sustitución de ``{env:...}`` y este módulo puede
   volver a intentarlo sin tener que rediseñar nada de este mecanismo.
3. **``CredencialesCloudflareFaltantesError`` sigue siendo el mismo error
   claro de siempre** si no hay credencial en ningún lado -- eso nunca
   cambió, porque ya era claro.

# CUARTA CORRECCIÓN -- las variantes low/medium/high, y por qué no eran opcionales

``ide_opencode_motor.py`` ya documentaba, con evidencia (ver su sección
"referencia_para_modelo() y variante_de_esfuerzo()"), el hueco exacto: el
único canal real que opencode ofrece para mandarle ``reasoning_effort`` a
una sesión YA VIVA es ``variant`` dentro de un ``ModelRef`` -- y una
``variant`` no es algo que se pueda inventar en tiempo de request, tiene
que estar DECLARADA de antemano en el catálogo de modelos que opencode lee
de ``opencode.json`` (``ModelV2Info.variants`` en ``GET /model``). Ninguno
de los cinco modelos de Workers AI la traía, así que el control Bajo/Medio/
Alto del selector del IDE se guardaba pero nunca llegaba a Cloudflare: sin
variante declarada, ``variante_de_esfuerzo`` siempre devolvía ``None`` y no
había ninguna vía en vivo.

Verificado en esta Mac contra un ``opencode serve`` 1.17.18 real, con el
mismo ``opencode.json`` que genera esta función pero con ``variants``
agregado a mano primero (workspace temporal, Workers AI real):

- ``GET /config`` y ``GET /config/providers`` devuelven exactamente lo que
  se escribió: ``variants: {"low": {"reasoningEffort": "low"}, ...}`` sin
  transformar.
- ``GET /api/model`` (la superficie que consulta
  ``variante_de_esfuerzo``, vía ``servidor.base_url`` + ``/model``) muestra
  que opencode SÍ traduce eso solo: cada modelo aparece con
  ``variants: [{"id": "low", "headers": {}, "body": {"reasoning_effort":
  "low"}}, ...]`` -- confirma que basta declarar ``reasoningEffort``
  (camelCase) en el ``opencode.json``; opencode arma el ``body`` snake_case
  real que le manda a Cloudflare, este módulo no tiene que construirlo.
- ``POST /api/session/{id}/model`` con
  ``{"model": {"providerID": "workersai", "id":
  "@cf/moonshotai/kimi-k2.7-code", "variant": "high"}}`` devolvió
  **HTTP 204**, y el turno siguiente (``POST .../prompt`` + lectura de
  ``GET .../message``) trajo una respuesta real del modelo
  (``model.variant: "high"`` en el mensaje del asistente, texto "OK" a un
  prompt de prueba) -- la variante no solo se acepta, el modelo la usa.

Por eso ``generar_opencode_json`` ahora declara, para CADA modelo que
escribe (los mismos que ya venían de ``modelos_ide_disponibles()``, nunca
una lista aparte), el bloque:

    "variants": {
      "low":    {"reasoningEffort": "low"},
      "medium": {"reasoningEffort": "medium"},
      "high":   {"reasoningEffort": "high"}
    }

con esos tres nombres LITERALES -- es lo que ``ide_opencode_motor.
variante_de_esfuerzo`` busca por ``id`` exacto contra el catálogo en vivo,
y lo que ``ModeloRef.variant``/``ModelRef`` de la API aceptan. No se
inventan otros niveles ni se copian los nombres en español
(``bajo``/``medio``/``alto``) que usa ``NIVELES_ESFUERZO_MOTOR`` -- esos
son la interfaz humana de ``ide_opencode_motor.py``; los que van al JSON
de opencode son la traducción a inglés que ``_REASONING_EFFORT_POR_NIVEL``
ya hace en ese mismo módulo.

## Migración -- por qué esto no rompe un ``opencode.json`` que ya existía

Un workspace con un ``opencode.json`` generado por una corrida ANTERIOR de
esta misma función (antes de que existieran las variantes) ya tiene
``provider.workersai`` puesto -- y la regla de "nunca pisar lo del dueño"
(ver la sección de arriba del docstring) hacía que ``proveedor_escrito``
saliera ``False`` y el archivo se dejara exactamente igual, variantes
faltantes para siempre, sin que ninguna corrida futura lo arreglara.

La solución NO es tratar ese archivo como si fuera del dueño (regla 2 del
encargo prohíbe pisarlo si de verdad lo es) ni tratarlo como si fuera
siempre nuestro (arriesgaría pisar una config que el dueño sí escribió a
mano y que por coincidencia se parece a la nuestra). La distingue
``_migrar_variantes_si_esta_generado_por_este_modulo``: solo si el bloque
``provider.workersai`` existente tiene EXACTAMENTE el ``name``
(``_NOMBRE_PROVEEDOR_WORKERS_AI``) y el ``npm``
(``_NPM_PROVEEDOR_OPENAI_COMPATIBLE``) que esta función siempre escribe --
algo que ningún test de este archivo con "config a mano del dueño" usa, a
propósito, para no confundir los dos casos -- se entiende que es un
``opencode.json`` que este módulo generó antes, y se le agrega, modelo por
modelo, el bloque ``variants`` que le falte, SIN tocar ningún otro campo:
ni el ``apiKey``/``baseURL`` que ya tenía, ni el ``name`` de un modelo que
el dueño haya personalizado, ni ningún modelo con su propio ``variants``
ya puesto (podría ser una personalización real, no se pisa), ni ningún
modelo que el dueño haya agregado a mano y no esté en el catálogo actual
de ``config/modelos.yml``. El resultado queda en el nuevo campo
``ResultadoGeneracionOpencodeJson.variantes_migradas`` -- vacío si no
aplicó la migración o si ya no había nada que agregar.

# QUINTA CORRECCIÓN (ronda Windows) -- ``chmod 0600`` ya no es un no-op ahí

El encargo de esta ronda (punto 1) confirmó lo que ya se sospechaba:
``os.chmod(ruta, 0o600)`` es un no-op real en Windows -- ``os.name == "nt"``
hacía que ``_asegurar_permisos_restrictivos`` se saliera sin tocar nada, así
que el ``opencode.json`` con el token de Cloudflare en claro (ver "TERCERA
CORRECCIÓN" arriba) quedaba con los permisos que heredara de la carpeta
contenedora. Confirmado en vivo por una fase anterior contra la VM de
Windows real de este proyecto, sobre un ``opencode.json`` generado por esta
misma función con un token real: ``icacls`` mostró
``BUILTIN\\Users:(RX)`` -- cualquier cuenta local de esa máquina podía leer
el token.

``icacls`` con herencia deshabilitada sigue siendo, verificado en esta
ronda, el camino recomendado para restringir un archivo a una sola cuenta
en Windows (ver la documentación oficial de Microsoft Learn y guías
actualizadas de 2025 sobre ``icacls``) -- no hace falta ``pywin32``/
``win32security`` para esto. ``_asegurar_permisos_restrictivos`` ahora
llama, en Windows, a ``_restringir_windows``: ``icacls <ruta>
/inheritance:r /grant:r <cuenta>:F /grant:r SYSTEM:F``, donde ``<cuenta>``
es ``DOMINIO\\usuario`` de quien corre el proceso (``USERDOMAIN``/
``USERNAME``, con ``getpass.getuser()`` de respaldo -- ver
``_cuenta_windows_actual``) y ``SYSTEM`` queda con acceso por el mismo
motivo que "root" en POSIX no se excluye de un 0600: es el propio sistema
operativo, no "otro usuario". Nunca ``shell=True`` -- mismo criterio que
``edecan_mcp.transport._argv_ejecutable`` (ver su docstring, el precedente
que este encargo pidió explícitamente leer): ``icacls.exe`` es un binario
real de ``System32``, no un ``.cmd``/``.bat``, así que aquí no hace falta
el rodeo por ``COMSPEC`` que sí necesita ``npx``/``npm``.

PENDIENTE, sin adornos (regla 6, "nada simulado"): esta ronda probó la
LÓGICA de ``_restringir_windows``/``_cuenta_windows_actual`` (construcción
del comando, resolución de la cuenta, manejo de un código de salida
distinto de cero) contra los tests de este archivo en macOS -- que no
tiene ``icacls``, así que no puede reproducir el resultado REAL. La
sintaxis exacta del comando es la misma que SÍ se verificó en vivo en la
fase anterior, pero con una cuenta fija (``Administrator``) en vez de la
cuenta resuelta dinámicamente que usa esta versión. El encargo de esta
ronda fue explícito: "no te conectes a la VM" -- así que falta que una
fase con acceso a ella confirme, contra un ``opencode.json`` real generado
por esta función, que ``BUILTIN\\Users`` queda de verdad sin acceso con la
cuenta dinámica.
"""

from __future__ import annotations

import getpass
import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from edecan_llm.task_router import modelo_para_perfil, modelos_ide_disponibles
from edecan_llm.workers_ai import RUTA_ENV_POR_DEFECTO, cargar_dotenv

from .ide_opencode import ModeloRef, ServidorOpencode

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Constantes -- el nombre de proveedor que este módulo escribe y espera
# encontrar. OJO: NO es lo mismo que ``proveedor_por_defecto: workers_ai`` de
# ``config/modelos.yml`` (con guion bajo) -- ese es un identificador interno
# de Edecán; ``workersai`` (sin guion bajo, sin espacio) es la CLAVE que
# opencode exige para el objeto ``provider`` de su propio ``opencode.json``
# (ver ejemplo verificado en MEMORY.md de la migración). Mezclarlos sería un
# bug silencioso -- por eso quedan como constantes separadas y documentadas.
# --------------------------------------------------------------------------- #

PROVEEDOR_WORKERS_AI_ID = "workersai"
_NOMBRE_PROVEEDOR_WORKERS_AI = "Cloudflare Workers AI"
_NPM_PROVEEDOR_OPENAI_COMPATIBLE = "@ai-sdk/openai-compatible"
_MODELO_MUESTRA_VALIDACION = "@cf/meta/llama-4-scout-17b-16e-instruct"
"""Modelo usado solo para el ping de validación de credencial -- barato
(es una búsqueda en el catálogo, no una inferencia) y siempre presente en
``modelos_chat``/``modelos_ide``, así que sirve de sonda estable."""

_REFERENCIA_ENV_TOKEN = "{env:CLOUDFLARE_API_TOKEN}"
"""La sintaxis de sustitución de variables de opencode -- NO es lo que este
módulo escribe en ``options.apiKey`` (ver "TERCERA CORRECCIÓN" del docstring
del módulo: se probó contra un ``opencode serve`` real haciendo una llamada
de verdad, no solo ``GET /config``, y para un proveedor custom
``@ai-sdk/openai-compatible`` esta referencia NO se resuelve al construir el
cliente que llama a Cloudflare -- solo al leer ``/config``). Queda la
constante documentada por si una versión futura de opencode la soporta de
verdad y vale la pena retomar el intento -- no la usa ningún camino activo
de este módulo hoy."""

NIVELES_ESFUERZO_OPENCODE: tuple[str, ...] = ("low", "medium", "high")
"""Los tres nombres LITERALES de variante que este módulo declara para cada
modelo -- ver la sección "CUARTA CORRECCIÓN" del docstring del módulo.
Tienen que ser exactamente estos: son los mismos que busca
``ide_opencode_motor.variante_de_esfuerzo`` (por ``id`` exacto contra el
catálogo en vivo de opencode) y los mismos valores que
``ide_opencode_motor._REASONING_EFFORT_POR_NIVEL`` ya produce para
``reasoning_effort`` -- inventar otro nombre aquí (o traducirlos al
español) dejaría a ``variante_de_esfuerzo`` sin encontrar nada, otra vez."""


def _bloque_variantes_esfuerzo() -> dict[str, dict[str, str]]:
    """El bloque ``variants`` que se declara para CADA modelo -- una copia
    nueva por llamada (nunca un dict compartido entre modelos: cada entrada
    de ``bloque_modelos`` necesita su propio objeto, para que mutar el de un
    modelo -- p. ej. durante la migración de más abajo -- no toque por
    accidente el de otro). Verificado contra un ``opencode serve`` real que
    esta forma (``reasoningEffort`` en camelCase) es la que opencode traduce
    solo, al leer ``GET /api/model``, a
    ``{"body": {"reasoning_effort": "<nivel>"}}`` -- ver "CUARTA CORRECCIÓN"
    en el docstring del módulo para la evidencia completa."""

    return {nivel: {"reasoningEffort": nivel} for nivel in NIVELES_ESFUERZO_OPENCODE}


def _migrar_variantes_si_esta_generado_por_este_modulo(
    bloque_existente: Any, bloque_modelos_actual: dict[str, Any]
) -> tuple[str, ...]:
    """Ver la sección "Migración" de "CUARTA CORRECCIÓN" en el docstring del
    módulo para la explicación completa de por qué esta heurística (mismo
    ``name`` + mismo ``npm`` que escribe este módulo) es la única forma
    segura de distinguir "esto lo generó Edecán antes" de "esto lo escribió
    el dueño a mano" sin una marca explícita en el archivo.

    Muta ``bloque_existente`` IN PLACE (es el mismo objeto que vive dentro
    de ``contenido["provider"][PROVEEDOR_WORKERS_AI_ID]``, así que la
    mutación ya queda lista para cuando ``generar_opencode_json`` escriba
    ``contenido`` a disco) agregando ``variants`` solo a los modelos que:
    están en el catálogo actual (``bloque_modelos_actual``, lo que
    ``modelos_ide_disponibles()`` devuelve hoy), YA estaban declarados en el
    archivo, y todavía NO tienen su propia clave ``variants``. Devuelve los
    ids realmente modificados -- vacío si la heurística no aplica (no es un
    archivo generado por este módulo) o si no había nada que agregar."""

    if not isinstance(bloque_existente, dict):
        return ()
    if bloque_existente.get("name") != _NOMBRE_PROVEEDOR_WORKERS_AI:
        return ()
    if bloque_existente.get("npm") != _NPM_PROVEEDOR_OPENAI_COMPATIBLE:
        return ()
    modelos_existentes = bloque_existente.get("models")
    if not isinstance(modelos_existentes, dict):
        return ()

    migrados: list[str] = []
    for modelo_id, bloque_nuevo in bloque_modelos_actual.items():
        entrada = modelos_existentes.get(modelo_id)
        if not isinstance(entrada, dict):
            continue  # el dueño lo quitó, o nunca llegó a escribirse -- no se inventa
        if "variants" in entrada:
            continue  # ya trae algo ahí -- puede ser una personalización del dueño, no se pisa
        entrada["variants"] = bloque_nuevo["variants"]
        migrados.append(modelo_id)
    return tuple(migrados)


PERMISOS_POR_DEFECTO: dict[str, str] = {"edit": "ask", "bash": "ask", "webfetch": "ask"}
"""El bloque ``permission`` que este módulo escribe por defecto -- los MISMOS
tres nombres de acción que ``ide_opencode_permisos.py`` confirmó en vivo
(``PermissionV2Request`` real para ``edit``/``bash``/``webfetch``, ver su
docstring, hallazgo 5). Sin este bloque en ``opencode.json``, opencode
resuelve esas tres acciones por su cuenta y jamás pide permiso -- el
``PuenteDePermisos`` de ese módulo, por correcto que esté, nunca se activa.
Ver la sección "CORRECCIÓN" del docstring de este archivo."""

_AVISO_PUENTE_REQUERIDO = (
    "Este workspace queda con 'permission: ask' para edit/bash/webfetch (lo haya escrito "
    "esta llamada o ya estuviera en el archivo). Ver 'ResultadoGeneracionOpencodeJson."
    "aviso_puente_requerido' antes de mandar un solo prompt: sin PuenteDePermisos.escuchar() "
    "(ide_opencode_permisos.py) corriendo en paralelo, el primer edit/bash/webfetch real se "
    "cuelga para siempre, sin error -- verificado en vivo (ver sección 'TRAMPA' del docstring "
    "de este módulo)."
)
"""Texto fijo de ``aviso_puente_requerido`` -- una sola fuente de verdad para
el mensaje, en vez de repetirlo en cada punto de retorno de
``generar_opencode_json``."""


# --------------------------------------------------------------------------- #
# Errores -- honestos a propósito, mismo criterio que ``ide_opencode.py``:
# nunca se inventa un mensaje cuando hay uno real disponible.
# --------------------------------------------------------------------------- #


class ErrorConfiguracionOpencode(RuntimeError):
    """El ``opencode.json`` de un workspace no se pudo generar o leer, o
    opencode no registró lo que este módulo esperaba encontrar."""


class CredencialesCloudflareFaltantesError(RuntimeError):
    """Ni el entorno ni el ``.env`` de la raíz traen
    ``CLOUDFLARE_ACCOUNT_ID``/``CLOUDFLARE_API_TOKEN``."""


class CredencialCloudflareInvalidaError(RuntimeError):
    """Cloudflare rechazó la credencial -- ``codigo``/``mensaje`` son los que
    Cloudflare mandó de verdad, nunca un texto inventado por este módulo."""

    def __init__(self, mensaje: str, *, codigo: int | None = None) -> None:
        super().__init__(mensaje)
        self.codigo = codigo


# --------------------------------------------------------------------------- #
# Resultados -- dataclasses, no dicts sueltos: que quien llame sepa qué campos
# esperar sin tener que leer este archivo entero.
# --------------------------------------------------------------------------- #


@dataclass(slots=True, frozen=True)
class ResultadoGeneracionOpencodeJson:
    """Lo que pasó al pedir que se genere/actualice un ``opencode.json``.

    ``provider`` y ``permission`` son dos bloques INDEPENDIENTES -- cada uno
    se combina o se preserva por su cuenta (ver la sección "CORRECCIÓN" del
    docstring del módulo sobre por qué ``permission`` importa tanto como
    ``provider``: sin él, los cuatro modos del IDE no frenan nada)."""

    ruta: Path
    creado: bool
    """``True`` si el archivo no existía antes de esta llamada."""
    proveedor_escrito: bool
    """``True`` si se agregó/actualizó la clave ``provider.workersai``. Si es
    ``False``, el archivo (si existía) quedó exactamente igual en esa clave
    -- ver ``advertencia_proveedor``."""
    modelos: tuple[str, ...]
    """Ids de modelo incluidos en el bloque escrito. Vacío si
    ``proveedor_escrito`` es ``False``. Cada uno de estos modelos, cuando
    ``proveedor_escrito`` es ``True``, ya trae su bloque ``variants``
    ``low``/``medium``/``high`` desde el primer momento -- ver "CUARTA
    CORRECCIÓN" en el docstring del módulo."""
    advertencia_proveedor: str | None
    """Por qué NO se escribió el proveedor, cuando aplica (p. ej. el dueño ya
    tiene su propio ``provider.workersai``)."""
    variantes_migradas: tuple[str, ...]
    """Ids de modelo a los que se les agregó ``variants`` esta llamada
    porque el archivo ya existía con un ``provider.workersai`` generado por
    una corrida ANTERIOR de este módulo (antes de que declarara variantes) --
    ver la sección "Migración" de "CUARTA CORRECCIÓN" en el docstring del
    módulo. Vacío tanto si ``proveedor_escrito`` es ``True`` (se escribió
    todo de cero, ya con variantes, no hizo falta migrar nada) como si el
    bloque existente no se pudo identificar como generado por este módulo
    (se asume del dueño, no se toca) o ya tenía sus variantes puestas."""
    aviso_credencial: str | None
    """Presente solo cuando ``proveedor_escrito`` es ``True``: recuerda que
    el archivo generado contiene la API key de Cloudflare EN CLARO -- ver la
    sección "TERCERA CORRECCIÓN" del docstring del módulo sobre por qué NO
    es una referencia ``{env:...}`` (se probó, y no funciona para un
    proveedor custom con la versión de opencode instalada)."""
    permiso_escrito: bool
    """``True`` si se agregó la clave ``permission`` (ver
    ``PERMISOS_POR_DEFECTO``). Si es ``False``, el archivo ya traía su propia
    clave ``permission`` y no se tocó -- ver ``advertencia_permiso``. SIN esta
    clave, opencode nunca pide permiso para editar/ejecutar/salir a la red, y
    ``PuenteDePermisos`` (``ide_opencode_permisos.py``) no tiene nada que
    interceptar -- los cuatro modos del IDE no frenan nada."""
    advertencia_permiso: str | None
    """Por qué NO se escribió el bloque ``permission``, cuando aplica (el
    dueño ya tiene su propia clave ``permission`` en el archivo)."""
    aviso_puente_requerido: str
    """SIEMPRE presente (no es opcional, a diferencia de los avisos de
    arriba): recuerda la trampa verificada en vivo (ver sección "TRAMPA" del
    docstring del módulo) -- con un bloque ``permission: ask`` en el archivo
    (lo haya escrito esta llamada o ya estuviera ahí), cualquier
    ``ServidorOpencode`` que mande un prompt SIN ``PuenteDePermisos.escuchar``
    corriendo en paralelo desde antes se cuelga para siempre en el primer
    ``edit``/``bash``/``webfetch`` real -- sin error, sin timeout de opencode.
    Este campo no depende de si el bloque se escribió ahora o ya existía: el
    riesgo es el mismo en ambos casos."""


@dataclass(slots=True, frozen=True)
class InfoProveedorRegistrado:
    """Lo que opencode, arrancado de verdad, dice que tiene registrado."""

    proveedor_id: str
    modelos: tuple[str, ...]
    puerto: int


# --------------------------------------------------------------------------- #
# Credenciales -- mismo orden de resolución que ``WorkersAIProvider``:
# parámetro explícito > variable de entorno > ``.env`` de la raíz.
# --------------------------------------------------------------------------- #


def _resolver_credenciales_cloudflare(
    cuenta_id: str | None,
    api_token: str | None,
    ruta_env: Path | str | None,
) -> tuple[str, str]:
    ruta_efectiva = Path(ruta_env) if ruta_env is not None else RUTA_ENV_POR_DEFECTO
    respaldo = cargar_dotenv(ruta_efectiva)
    cuenta = (
        cuenta_id
        or os.environ.get("CLOUDFLARE_ACCOUNT_ID")
        or respaldo.get("CLOUDFLARE_ACCOUNT_ID")
        or ""
    ).strip()
    token = (
        api_token
        or os.environ.get("CLOUDFLARE_API_TOKEN")
        or respaldo.get("CLOUDFLARE_API_TOKEN")
        or ""
    ).strip()
    faltan = [
        nombre
        for nombre, valor in (("CLOUDFLARE_ACCOUNT_ID", cuenta), ("CLOUDFLARE_API_TOKEN", token))
        if not valor
    ]
    if faltan:
        raise CredencialesCloudflareFaltantesError(
            "Faltan credenciales de Cloudflare para generar opencode.json: "
            f"{', '.join(faltan)}. Ponlas en el entorno o en {ruta_efectiva}."
        )
    return cuenta, token


def _cuenta_windows_actual() -> str | None:
    """La cuenta ``DOMINIO\\usuario`` (o ``equipo\\usuario`` para una cuenta
    local) de quien corre este proceso -- la forma que ``icacls`` acepta
    directamente por nombre, sin tener que resolver un SID a mano.
    ``USERDOMAIN``/``USERNAME`` los pone SIEMPRE el propio Windows en
    cualquier sesión con perfil de usuario cargado (interactiva o de
    servicio) -- no dependen de que este proceso las haya heredado de un
    shell en particular; es el mismo par de variables que cualquier script
    ``.bat``/PowerShell usa para referirse a "el usuario actual"
    (``%USERDOMAIN%\\%USERNAME%``). Si algún día faltan (proceso lanzado en
    un contexto sin perfil), se intenta ``getpass.getuser()`` como
    respaldo -- y si tampoco resuelve nada, se devuelve ``None`` para que
    quien llama pueda seguir el mismo criterio best-effort del resto de
    esta función: loguear y seguir, nunca reventar la escritura del
    archivo por esto.

    NOTA (se mantiene tal cual, ver ``_cuentas_windows_candidatas`` para el
    arreglo real): esta función sola YA NO es lo que usa
    ``_restringir_windows`` -- se descubrió en vivo contra la VM de este
    proyecto que ``USERDOMAIN`` vale literalmente ``WORKGROUP`` en una
    máquina standalone (no unida a un dominio), y ``WORKGROUP`` no es un
    principal de seguridad que ``icacls`` pueda resolver (código de salida
    1332, "No mapping between account names and security IDs was done").
    Se deja esta función intacta (la usan tests existentes y el camino de
    respaldo de ``_cuentas_windows_candidatas`` cuando no hay ni
    ``USERNAME`` ni ``USERDOMAIN``) pero ``_restringir_windows`` ya no
    confía en un único candidato."""

    dominio = os.environ.get("USERDOMAIN")
    usuario = os.environ.get("USERNAME")
    if dominio and usuario:
        return f"{dominio}\\{usuario}"
    try:
        usuario = getpass.getuser()
    except Exception:  # noqa: BLE001 -- best-effort, nunca revienta por esto
        return None
    return usuario or None


_DOMINIO_WORKGROUP_POR_DEFECTO = "WORKGROUP"
"""Nombre de grupo de trabajo que Windows asigna por defecto a una máquina
standalone (no unida a un dominio Active Directory). Confirmado en vivo
contra la VM de este proyecto (EC2, Windows Server 2022): ahí
``USERDOMAIN`` vale literalmente ``WORKGROUP`` y ``icacls`` rechaza
``WORKGROUP\\usuario`` con el código 1332 -- no es un dominio real, es solo
la etiqueta que Windows usa cuando no hay dominio. Comparar en mayúsculas:
es el valor que Windows pone, pero por si acaso alguien lo cambia con
distintas mayúsculas."""


def _sid_windows_actual() -> str | None:
    """SID del usuario actual, en la forma ``*S-1-5-21-...`` que acepta icacls.

    **Los nombres de cuenta no sirven.** Medido en vivo en un Windows Server
    2022 real (VM de pruebas, cuenta local ``Administrator``,
    ``USERDOMAIN=WORKGROUP``): tanto ``.\\administrator`` como
    ``WORKGROUP\\administrator`` fallan con **error 1332** -- *"No mapping
    between account names and security IDs was done"* -- y el archivo se queda
    con los permisos heredados de la carpeta, es decir, sin restringir.

    El SID no tiene ese problema: no depende del dominio, ni del idioma del
    sistema, ni de que la cuenta sea local o de dominio. Con él, la misma
    llamada a icacls devuelve 0 y la lista final queda exactamente en
    ``SYSTEM`` + el usuario -- verificado en la misma VM.
    """

    try:
        salida = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "[Security.Principal.WindowsIdentity]::GetCurrent().User.Value",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    # ``salida`` puede no traer ``stdout`` si algo devolvió otra cosa (un doble
    # de prueba, una versión distinta de subprocess): se trata como "no hay SID"
    # en vez de reventar el arranque por un atributo ausente.
    sid = (getattr(salida, "stdout", None) or "").strip()
    # Un SID siempre empieza por "S-1-"; cualquier otra cosa es ruido de
    # PowerShell y no se le pasa a icacls.
    return sid if sid.startswith("S-1-") else None


def _cuentas_windows_candidatas() -> list[str]:
    """Identidades a probar con icacls, **en orden de fiabilidad medida**.

    Primero el SID del usuario actual (ver ``_sid_windows_actual``: es el
    único que se comprobó funcionando en la VM real). Los nombres quedan
    detrás solo como respaldo por si PowerShell no está disponible -- en la
    máquina donde se midió, los dos fallaban con error 1332.
    """

    candidatos: list[str] = []
    sid = _sid_windows_actual()
    if sid:
        candidatos.append(f"*{sid}")

    dominio = os.environ.get("USERDOMAIN")
    usuario = os.environ.get("USERNAME")
    if usuario:
        es_workgroup = bool(dominio) and dominio.upper() == _DOMINIO_WORKGROUP_POR_DEFECTO
        if dominio and not es_workgroup:
            candidatos.append(f"{dominio}\\{usuario}")
        candidatos.append(f".\\{usuario}")
        if dominio and es_workgroup:
            candidatos.append(f"{dominio}\\{usuario}")
    else:
        cuenta_respaldo = _cuenta_windows_actual()
        if cuenta_respaldo:
            candidatos.append(cuenta_respaldo)

    vistos: set[str] = set()
    unicos: list[str] = []
    for candidato in candidatos:
        if candidato not in vistos:
            vistos.add(candidato)
            unicos.append(candidato)
    return unicos


def _restringir_windows(ruta: Path) -> None:
    """``icacls`` real -- el equivalente Windows de ``chmod 0600`` que pide
    el encargo (punto 1): deshabilita la herencia de permisos de la carpeta
    contenedora (``/inheritance:r``) y deja un Full Control EXPLÍCITO solo
    para dos cuentas: quien corre este proceso (ver
    ``_cuenta_windows_actual``) y ``SYSTEM`` (el propio sistema operativo --
    mismo criterio que "root sigue pudiendo leer un 0600 en POSIX": esta
    función protege de OTROS USUARIOS de la máquina, no del sistema mismo).
    Cualquier otra cuenta que la carpeta contenedora le hubiera heredado a
    este archivo (típicamente ``BUILTIN\\Users:(RX)``, confirmado en vivo
    contra la VM de este proyecto -- ver MEMORY.md de la fase que investigó
    esto) queda fuera.

    Verificado que ``/inheritance:r`` sigue siendo el camino recomendado hoy
    para restringir un archivo a una sola cuenta en Windows (ver
    https://learn.microsoft.com/windows-server/administration/windows-commands/icacls
    y la guía de icacls de 2025 de adamtheautomator.com), y que la sintaxis
    exacta usada aquí (``icacls <ruta> /inheritance:r /grant:r
    <cuenta>:F /grant:r SYSTEM:F``) es la misma que se probó a mano contra
    esa VM sobre un ``opencode.json`` real con un token real: dejó
    ``BUILTIN\\Users`` sin ninguna entrada.

    Nunca ``shell=True`` -- mismo criterio que
    ``edecan_mcp.transport._argv_ejecutable`` (ver su docstring): un
    ``opencode.json`` puede vivir bajo una ruta de workspace con espacios o
    caracteres raros, y una cadena de shell los interpretaría. A diferencia
    de ``npx``/``npm`` ahí, ``icacls.exe`` es un binario real de
    ``System32`` (no un ``.cmd``/``.bat``), así que no hace falta el rodeo
    por ``COMSPEC`` que ese módulo sí necesita -- ``subprocess.run`` con la
    lista de argumentos ya separada alcanza, y además cita correctamente
    cualquier espacio dentro de ``<cuenta>:F`` sin que este código tenga que
    hacerlo a mano.

    Best-effort, igual que el lado POSIX: si ``icacls`` no está en el PATH,
    tarda demasiado, o devuelve un código de salida distinto de cero, se
    loguea con el detalle real (comando, código, stdout/stderr) y se sigue
    -- no vale la pena tumbar la generación de un ``opencode.json`` por
    esto, pero tampoco se traga el fallo en silencio.

    CORRECCIÓN (ronda de cierre de Windows, tras la validación en vivo):
    esta función probaba UNA sola cuenta (``_cuenta_windows_actual()``,
    ``DOMINIO\\usuario``) y se dio por buena porque la sintaxis se había
    verificado a mano con una cuenta fija (``Administrator``, sin dominio
    real de por medio). La validación completa reveló el hueco: contra la
    VM real (standalone, ``USERDOMAIN=WORKGROUP``) ese único intento falla
    con el código 1332 ("No mapping between account names and security IDs
    was done") y el archivo queda EXACTAMENTE como antes -- ``BUILTIN\\Users``
    sigue pudiendo leer el token de Cloudflare, silenciosamente, porque el
    manejo best-effort no distingue "protegí el archivo" de "lo intenté y
    fallé". Ahora se prueban los candidatos de ``_cuentas_windows_candidatas``
    en orden y se para en el primero que ``icacls`` acepta (código 0) --
    ver esa función para la explicación completa de por qué ``.\\usuario``
    (cuenta local, sin nombre de dominio) es el candidato que de verdad
    cubre el caso medido.

    PENDIENTE (regla 6 del encargo, "nada simulado"): esta función se
    escribió y se probó su LÓGICA (construcción del comando, manejo de
    error, resolución de la cuenta, orden de los candidatos) contra los
    tests de este archivo en macOS -- pero macOS no tiene ``icacls``, así
    que el resultado REAL (que ``.\\usuario`` de verdad resuelva donde
    ``DOMINIO\\usuario`` falló) no se pudo re-verificar en esta ronda contra
    la VM de Windows real (regla explícita de esta ronda: "no te conectes a
    la VM"). Falta que una fase con acceso a la VM confirme el comando
    completo, tal cual queda aquí, contra un ``opencode.json`` real en esa
    misma máquina workgroup donde se midió el error 1332.
    """

    candidatos = _cuentas_windows_candidatas()
    if not candidatos:
        logger.warning(
            "No pude determinar ninguna cuenta de Windows para este proceso (USERDOMAIN/"
            "USERNAME ausentes y getpass.getuser() tampoco resolvió nada) -- %s queda con "
            "los permisos que haya heredado de la carpeta contenedora, sin restringir.",
            ruta,
        )
        return
    for cuenta in candidatos:
        comando = [
            "icacls",
            str(ruta),
            "/inheritance:r",
            "/grant:r",
            f"{cuenta}:F",
            "/grant:r",
            "SYSTEM:F",
        ]
        try:
            resultado = subprocess.run(  # noqa: S603 -- argv en lista, nunca shell=True
                comando,
                capture_output=True,
                text=True,
                timeout=10.0,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            # Un OSError aquí típicamente significa que icacls.exe no está
            # en el PATH -- probar otra cuenta con el mismo binario ausente
            # no va a arreglar nada, así que no tiene sentido seguir la lista.
            logger.warning(
                "No pude ejecutar icacls sobre %s con la cuenta %s: %s", ruta, cuenta, exc
            )
            return
        if resultado.returncode == 0:
            return
        logger.warning(
            "icacls sobre %s con la cuenta %s terminó con código %d -- probando el "
            "siguiente candidato si queda alguno. comando=%r stdout=%r stderr=%r",
            ruta,
            cuenta,
            resultado.returncode,
            comando,
            resultado.stdout,
            resultado.stderr,
        )
    logger.warning(
        "icacls falló con las %d cuenta(s) candidata(s) probadas (%s) -- %s puede seguir "
        "con permisos heredados de la carpeta contenedora, sin restringir.",
        len(candidatos),
        ", ".join(candidatos),
        ruta,
    )


def _asegurar_permisos_restrictivos(ruta: Path) -> None:
    """Restringe ``opencode.json`` a un solo usuario -- ``chmod 0600`` en
    POSIX, ``icacls`` real en Windows (ver ``_restringir_windows``). Ya no
    lleva el token en claro (ver "TERCERA CORRECCIÓN" del docstring del
    módulo)... salvo que sí lo lleva -- esa corrección se revirtió, ver
    arriba: SIGUE llevando el token en claro, y esta función es la mitigación
    real. También lleva la ruta de la cuenta de Cloudflare y es el archivo
    que le dice a opencode qué acciones puede tomar sin preguntar. Mismo
    criterio que ``ide_opencode_binario._asegurar_bit_ejecutable`` y que la
    función homónima de ``ide_opencode_motor.py`` (se aplica en AMBOS lados a
    propósito, cinturón y tirantes -- pedido explícito del encargo, punto 3):
    si la restricción falla (POSIX o Windows), se loguea y se sigue -- no
    vale la pena tumbar la generación de un archivo por esto, pero tampoco
    se traga el fallo en silencio."""

    if os.name == "nt":
        _restringir_windows(ruta)
        return
    try:
        os.chmod(ruta, 0o600)
    except OSError as exc:
        logger.warning("No pude restringir los permisos de %s a 0600: %s", ruta, exc)


# --------------------------------------------------------------------------- #
# Perfiles -- traduce config/modelos.yml a lo que opencode espera
# --------------------------------------------------------------------------- #


def perfil_a_referencia_modelo(
    perfil: str, *, ruta_yaml_modelos: Path | str | None = None
) -> ModeloRef:
    """Dado un perfil de ``config/modelos.yml`` (p. ej. ``ingenieria_software``),
    devuelve el ``ModelRef`` (``providerID`` + ``id``) que opencode espera en
    ``crear_sesion``/``cambiar_modelo`` de ``ServidorOpencode``.

    La autoridad del mapeo perfil -> modelo sigue siendo
    ``edecan_llm.task_router.modelo_para_perfil`` -- este módulo no
    reimplementa esa lectura, solo la envuelve con el ``providerID`` fijo que
    usa el ``opencode.json`` que genera ``generar_opencode_json`` (ver la nota
    de ``PROVEEDOR_WORKERS_AI_ID`` sobre por qué NO es el mismo texto que
    ``proveedor_por_defecto`` del YAML).
    """

    modelo = modelo_para_perfil(perfil, ruta_yaml_modelos)
    return ModeloRef(id=modelo, providerID=PROVEEDOR_WORKERS_AI_ID)


# --------------------------------------------------------------------------- #
# Generar/actualizar opencode.json
# --------------------------------------------------------------------------- #


def generar_opencode_json(
    directorio_workspace: str | Path,
    *,
    ruta_yaml_modelos: Path | str | None = None,
    cuenta_id: str | None = None,
    api_token: str | None = None,
    ruta_env: Path | str | None = None,
    permisos: dict[str, str] | None = None,
) -> ResultadoGeneracionOpencodeJson:
    """Genera o actualiza ``<workspace>/opencode.json`` con el proveedor
    ``workersai``, el catálogo real de ``config/modelos.yml`` Y el bloque
    ``permission`` que hace que opencode de verdad PIDA permiso -- SIN pisar
    nada que el dueño ya haya escrito ahí.

    El ``apiKey`` se escribe EN CLARO (ver ``aviso_credencial`` en el
    resultado) -- NO como referencia ``{env:CLOUDFLARE_API_TOKEN}``. Se
    intentó la referencia en esta misma ronda y se comprobó, contra un
    ``opencode serve`` real haciendo una llamada de verdad (no solo
    ``GET /config``), que NO se resuelve al construir el cliente que llama a
    Cloudflare para un proveedor custom como este -- ver la sección
    "TERCERA CORRECCIÓN" del docstring del módulo para la evidencia
    completa. Escribirla igual dejaría al agente sin poder completar ni un
    solo prompt real.

    ``permission`` no es opcional en la práctica: sin él, opencode resuelve
    ``edit``/``bash``/``webfetch`` por su cuenta y ``PuenteDePermisos``
    (``ide_opencode_permisos.py``) nunca recibe nada que interceptar -- ver
    la sección "CORRECCIÓN" del docstring del módulo. ``permisos=`` existe
    solo para quien quiera un bloque distinto al de ``PERMISOS_POR_DEFECTO``
    (p. ej. agregar más acciones); no pasarlo usa el default ya probado
    contra opencode real.

    Efecto colateral, antes de tocar el disco: esta función deja
    ``CLOUDFLARE_API_TOKEN`` puesto en ``os.environ`` de este proceso (con el
    valor que acaba de resolver -- parámetro explícito > entorno > ``.env``).
    Ya no es indispensable para que opencode funcione (el token va en el
    JSON), pero es higiene barata y deja el camino listo si una versión
    futura de opencode arregla la resolución de ``{env:...}`` para
    proveedores custom. Ver "TERCERA CORRECCIÓN" del docstring del módulo.

    Reglas de combinación (regla 2 del encargo, "nunca destruyas su
    configuración") -- se aplican POR SEPARADO a ``provider`` y a
    ``permission``, cada bloque con su propio resultado:

    - Si el archivo no existe, se crea con ``$schema`` + ``provider.workersai``
      + ``permission``.
    - Si existe pero le falta ``provider.workersai`` y/o ``permission``, se le
      AGREGA lo que falte sin tocar ninguna otra clave (``agent``, ``mode``,
      otros proveedores, un ``permission`` ya puesto por el dueño, etc.).
    - Si ya tiene ``provider.workersai`` -- del dueño o de una corrida
      anterior de este mismo módulo -- esa clave NO se toca (ni su
      ``apiKey``/``baseURL``, ni el ``name`` de un modelo que el dueño haya
      personalizado, ni modelos agregados a mano); ``proveedor_escrito``
      viene en ``False`` y ``advertencia_proveedor`` explica por qué.
      EXCEPCIÓN acotada (ver "Migración" en "CUARTA CORRECCIÓN" del
      docstring del módulo): si ese bloque se puede identificar como
      generado por ESTE módulo (mismo ``name``/``npm`` que siempre escribe,
      nunca por coincidencia con algo que el dueño haya escrito a mano), se
      le agrega -- modelo por modelo, solo a los que ya estaban declarados y
      todavía no tienen ``variants`` -- el bloque de variantes que le falte.
      Esos ids quedan en ``variantes_migradas``; ``proveedor_escrito`` sigue
      en ``False`` porque el bloque en sí no se reescribió de cero. Lo
      mismo, de forma independiente y sin ninguna excepción, para
      ``permission`` (``permiso_escrito``/``advertencia_permiso``).
      Regenerar el proveedor desde cero es una decisión humana explícita
      (borrar la clave a mano), nunca automática.
    - Si el archivo existe pero no es JSON válido, o su contenido no es un
      objeto, o ``provider``/``permission`` no son objetos: se declara el
      error (ver regla 6, "nada simulado") y no se toca nada -- sobrescribir
      un archivo roto podría estar pisando un cambio a medio guardar del
      dueño.
    """

    workspace = Path(directorio_workspace)
    if not workspace.is_dir():
        raise ValueError(f"El workspace no existe o no es una carpeta: {workspace}")
    ruta_json = workspace / "opencode.json"

    cuenta, token = _resolver_credenciales_cloudflare(cuenta_id, api_token, ruta_env)
    # Ver la nota de efecto colateral en el docstring de esta función y la
    # sección "TERCERA CORRECCIÓN" del docstring del módulo: el apiKey que
    # se escribe más abajo sigue siendo el token en claro (la referencia
    # {env:...} no funciona para este proveedor), pero dejarlo también en
    # os.environ es higiene barata y no depende de esa referencia.
    os.environ["CLOUDFLARE_API_TOKEN"] = token

    modelos = modelos_ide_disponibles(ruta_yaml_modelos)
    if not modelos:
        # No debería pasar nunca -- modelos_ide_disponibles() siempre cae a un
        # fallback no vacío -- pero si algún día lo hace, mejor un error claro
        # que un opencode.json con "models": {} que deja al motor sin nada
        # que ofrecer y sin ninguna pista de por qué.
        raise ErrorConfiguracionOpencode(
            "config/modelos.yml no declaró ningún modelo utilizable en 'modelos_ide' "
            "-- no hay nada que registrar en opencode.json."
        )
    bloque_modelos: dict[str, Any] = {
        str(fila["id"]): {
            "name": str(fila.get("nombre") or fila["id"]),
            # Encargo de esta ronda: declarar las tres variantes de esfuerzo
            # POR MODELO -- ver "CUARTA CORRECCIÓN" en el docstring del
            # módulo para la evidencia contra un opencode serve real. Sin
            # esto, el control Bajo/Medio/Alto del selector se guarda pero
            # nunca llega a Cloudflare (ide_opencode_motor.variante_de_esfuerzo
            # siempre devuelve None).
            "variants": _bloque_variantes_esfuerzo(),
        }
        for fila in modelos
    }
    bloque_proveedor: dict[str, Any] = {
        "npm": _NPM_PROVEEDOR_OPENAI_COMPATIBLE,
        "name": _NOMBRE_PROVEEDOR_WORKERS_AI,
        "options": {
            "baseURL": f"https://api.cloudflare.com/client/v4/accounts/{cuenta}/ai/v1",
            # EN CLARO a propósito -- ver "TERCERA CORRECCIÓN" en el
            # docstring del módulo: la referencia {env:CLOUDFLARE_API_TOKEN}
            # se probó contra opencode real y NO se resuelve para un
            # proveedor custom al hacer la llamada real (solo al leer
            # /config). El chmod 0600 de más abajo es la mitigación real.
            "apiKey": token,
        },
        "models": bloque_modelos,
    }
    bloque_permisos: dict[str, str] = (
        dict(permisos) if permisos is not None else dict(PERMISOS_POR_DEFECTO)
    )

    existia = ruta_json.is_file()
    if existia:
        texto_original = ruta_json.read_text(encoding="utf-8")
        try:
            contenido: Any = json.loads(texto_original) if texto_original.strip() else {}
        except json.JSONDecodeError as exc:
            raise ErrorConfiguracionOpencode(
                f"{ruta_json} ya existe pero no es JSON válido -- no lo toco para no "
                f"arriesgar una configuración a medio escribir del dueño. Error real: {exc}"
            ) from exc
        if not isinstance(contenido, dict):
            raise ErrorConfiguracionOpencode(
                f"{ruta_json} existe pero su contenido no es un objeto JSON "
                f"(es {type(contenido).__name__}) -- no lo toco."
            )
    else:
        contenido = {"$schema": "https://opencode.ai/config.json"}

    proveedores = contenido.get("provider")
    if proveedores is None:
        proveedores = {}
        contenido["provider"] = proveedores
    elif not isinstance(proveedores, dict):
        raise ErrorConfiguracionOpencode(
            f"{ruta_json} tiene una clave 'provider' que no es un objeto "
            f"(es {type(proveedores).__name__}) -- no lo toco."
        )

    permiso_existente = contenido.get("permission")
    if permiso_existente is not None and not isinstance(permiso_existente, dict):
        raise ErrorConfiguracionOpencode(
            f"{ruta_json} tiene una clave 'permission' que no es un objeto "
            f"(es {type(permiso_existente).__name__}) -- no lo toco."
        )

    # -- provider.workersai: agregar solo si falta; si ya existe y se puede
    # identificar como generado por este módulo, migrar las variantes que le
    # falten -- ver la sección "Migración" de "CUARTA CORRECCIÓN" del
    # docstring del módulo. ------------------------------------------------
    if PROVEEDOR_WORKERS_AI_ID in proveedores:
        proveedor_escrito = False
        modelos_escritos: tuple[str, ...] = ()
        variantes_migradas = _migrar_variantes_si_esta_generado_por_este_modulo(
            proveedores[PROVEEDOR_WORKERS_AI_ID], bloque_modelos
        )
        if variantes_migradas:
            advertencia_proveedor: str | None = (
                f"{ruta_json} ya declaraba 'provider.{PROVEEDOR_WORKERS_AI_ID}' -- generado "
                "por Edecán en una corrida anterior, antes de que este módulo declarara "
                "variantes de esfuerzo. Se dejó igual (nombre, apiKey, baseURL, modelos "
                "agregados a mano), pero se le agregó el bloque 'variants' que le faltaba "
                f"a: {', '.join(variantes_migradas)}."
            )
        else:
            advertencia_proveedor = (
                f"{ruta_json} ya declara 'provider.{PROVEEDOR_WORKERS_AI_ID}' -- se deja "
                "tal cual porque puede ser configuración propia del dueño. Si quieres que "
                "Edecán la regenere con el catálogo actual de config/modelos.yml, borra esa "
                "clave a mano primero y vuelve a llamar a esta función."
            )
    else:
        variantes_migradas = ()
        proveedores[PROVEEDOR_WORKERS_AI_ID] = bloque_proveedor
        proveedor_escrito = True
        modelos_escritos = tuple(bloque_modelos.keys())
        advertencia_proveedor = None

    # -- permission: agregar solo si la clave no existe en absoluto --------
    if permiso_existente is not None:
        permiso_escrito = False
        advertencia_permiso: str | None = (
            f"{ruta_json} ya declara 'permission' -- se deja tal cual porque puede ser "
            "configuración propia del dueño. Sin revisar que cubra 'edit'/'bash'/'webfetch' "
            "con 'ask', los modos del IDE pueden no frenar nada (ver PuenteDePermisos en "
            "ide_opencode_permisos.py). Si quieres que Edecán la regenere, borra esa clave "
            "a mano primero y vuelve a llamar a esta función."
        )
    else:
        contenido["permission"] = bloque_permisos
        permiso_escrito = True
        advertencia_permiso = None

    if not (proveedor_escrito or permiso_escrito or variantes_migradas):
        # Ninguno de los tres bloques cambió -- no reescribas el archivo en
        # disco (mismo criterio que antes: "nunca destruyas su
        # configuración" incluye no tocar ni la fecha de modificación si no
        # hay nada real que agregar). Se blinda igual el archivo existente --
        # cinturón y tirantes (encargo, punto 3): lleva el token de
        # Cloudflare, sea cual sea la razón por la que esta llamada no
        # escribió nada nuevo.
        _asegurar_permisos_restrictivos(ruta_json)
        return ResultadoGeneracionOpencodeJson(
            ruta=ruta_json,
            creado=False,
            proveedor_escrito=False,
            modelos=(),
            advertencia_proveedor=advertencia_proveedor,
            variantes_migradas=(),
            aviso_credencial=None,
            permiso_escrito=False,
            advertencia_permiso=advertencia_permiso,
            aviso_puente_requerido=_AVISO_PUENTE_REQUERIDO,
        )

    ruta_json.write_text(
        json.dumps(contenido, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    # Encargo, punto 3: "permisos 0600" -- este es el hallazgo real que SÍ se
    # corrigió esta ronda (ver "TERCERA CORRECCIÓN" del docstring del
    # módulo). Se aplica aquí, DENTRO del propio generador (además del
    # chmod redundante en ide_opencode_motor.py), para que también quede
    # blindado quien llame a esta función directamente, sin pasar por
    # MotorOpencode -- como hacen varios tests de este mismo archivo.
    _asegurar_permisos_restrictivos(ruta_json)

    return ResultadoGeneracionOpencodeJson(
        ruta=ruta_json,
        creado=not existia,
        proveedor_escrito=proveedor_escrito,
        modelos=modelos_escritos,
        advertencia_proveedor=advertencia_proveedor,
        variantes_migradas=variantes_migradas,
        aviso_credencial=(
            f"{ruta_json} ahora tiene CLOUDFLARE_API_TOKEN en texto claro "
            "(provider.workersai.options.apiKey). Es el workspace del dueño, no un sitio "
            "versionado -- pero es un secreto real: no lo copies, no lo pegues en ningún "
            "chat, no lo subas a git. Queda en claro a propósito -- ver 'TERCERA CORRECCIÓN' "
            "en el docstring del módulo sobre por qué la referencia {env:...} no sirve hoy -- "
            "y el archivo queda en 0600 justo después de esta llamada."
        )
        if proveedor_escrito
        else None,
        permiso_escrito=permiso_escrito,
        advertencia_permiso=advertencia_permiso,
        aviso_puente_requerido=_AVISO_PUENTE_REQUERIDO,
    )


# --------------------------------------------------------------------------- #
# Comprobar de verdad -- dos preguntas DISTINTAS, dos funciones DISTINTAS.
# Combinarlas en una sola escondería cuál de las dos falló.
# --------------------------------------------------------------------------- #


async def comprobar_proveedor_registrado(
    directorio_trabajo: str | Path,
    *,
    ruta_binario: str | Path | None = None,
    timeout_arranque: float = 20.0,
) -> InfoProveedorRegistrado:
    """Arranca opencode DE VERDAD sobre ``directorio_trabajo`` (que debe tener
    ya un ``opencode.json``, p. ej. el que escribe ``generar_opencode_json``)
    y confirma, contra el propio servidor -- no contra el JSON que nosotros
    escribimos --, que ``provider.workersai`` quedó registrado.

    Esto es una comprobación ESTRUCTURAL: opencode responde con lo que LEYÓ
    de ``opencode.json`` en ``GET /config`` sin llamar a Cloudflare, así que
    un token inventado igual pasa esta comprobación. Para la credencial en
    sí, usa ``comprobar_credencial_cloudflare``.
    """

    ruta_config = Path(directorio_trabajo) / "opencode.json"
    if not ruta_config.is_file():
        raise ErrorConfiguracionOpencode(
            f"El workspace no tiene registrado un opencode.json: {ruta_config}. "
            "Genera uno primero con generar_opencode_json()."
        )

    servidor = await ServidorOpencode.iniciar(
        directorio_trabajo, ruta_binario=ruta_binario, timeout_arranque=timeout_arranque
    )
    try:
        # /config vive en la RAÍZ del servidor, no bajo /api -- a diferencia
        # de todo lo demás que usa este módulo (ver docstring, y el de
        # ide_opencode.py sobre por qué /api es la superficie v2). Por eso se
        # arma la URL a mano con `servidor.puerto` en vez de reusar el
        # `base_url` (que ya trae el prefijo /api) o el cliente interno del
        # adaptador (privado a propósito -- no es parte de su contrato
        # público).
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as cliente:
            try:
                resp = await cliente.get(f"http://127.0.0.1:{servidor.puerto}/config")
            except httpx.TransportError as exc:
                raise ErrorConfiguracionOpencode(
                    f"No se pudo consultar /config de opencode en el puerto "
                    f"{servidor.puerto}: {exc}"
                ) from exc
        if resp.status_code >= 400:
            raise ErrorConfiguracionOpencode(
                f"opencode devolvió {resp.status_code} en GET /config: {resp.text[:500]}"
            )
        cuerpo = resp.json()
        proveedores = cuerpo.get("provider") or {}
        bloque = proveedores.get(PROVEEDOR_WORKERS_AI_ID)
        if not isinstance(bloque, dict):
            raise ErrorConfiguracionOpencode(
                f"opencode no tiene registrado el proveedor '{PROVEEDOR_WORKERS_AI_ID}' en "
                f"/config (directorio: {servidor.directorio_trabajo}). ¿Existe opencode.json "
                f"con 'provider.{PROVEEDOR_WORKERS_AI_ID}' ahí? Genera uno primero con "
                "generar_opencode_json()."
            )
        modelos = bloque.get("models") or {}
        if not isinstance(modelos, dict) or not modelos:
            raise ErrorConfiguracionOpencode(
                f"opencode registró el proveedor '{PROVEEDOR_WORKERS_AI_ID}' pero sin ningún "
                "modelo -- revisa 'provider.workersai.models' en el opencode.json del "
                f"directorio {servidor.directorio_trabajo}."
            )
        return InfoProveedorRegistrado(
            proveedor_id=PROVEEDOR_WORKERS_AI_ID,
            modelos=tuple(sorted(modelos.keys())),
            puerto=servidor.puerto,
        )
    finally:
        await servidor.detener()


async def comprobar_credencial_cloudflare(
    cuenta_id: str,
    api_token: str,
    *,
    modelo_muestra: str = _MODELO_MUESTRA_VALIDACION,
    timeout: float = 10.0,
) -> None:
    """Valida la credencial CONTRA CLOUDFLARE (no contra opencode).

    Usa el mismo endpoint que ``config/modelos.yml`` ya documenta como
    autoridad de precios (``GET .../ai/models/search?search=...``): es una
    búsqueda en el catálogo, no una inferencia, así que no gasta presupuesto
    de tokens -- solo confirma que la cuenta y el token de verdad autentican.

    No devuelve nada en éxito (``None``); en fallo lanza
    ``CredencialCloudflareInvalidaError`` con el ``code``/``message`` REALES
    que mandó Cloudflare (regla 6 del encargo: nada se finge). Verificado a
    mano: con un token inventado, Cloudflare responde
    ``{"success": false, "errors": [{"code": 9106, "message": "Authentication
    failed (status: 400)"}]}`` -- ese es exactamente el texto que llega aquí.
    """

    url = f"https://api.cloudflare.com/client/v4/accounts/{cuenta_id}/ai/models/search"
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as cliente:
        try:
            resp = await cliente.get(
                url,
                params={"search": modelo_muestra},
                headers={"Authorization": f"Bearer {api_token}"},
            )
        except httpx.TransportError as exc:
            raise CredencialCloudflareInvalidaError(
                f"No se pudo contactar a Cloudflare para validar la credencial: {exc}"
            ) from exc

    try:
        cuerpo = resp.json()
    except json.JSONDecodeError as exc:
        raise CredencialCloudflareInvalidaError(
            f"Cloudflare respondió {resp.status_code} pero no en JSON al validar la "
            f"credencial: {resp.text[:300]!r}"
        ) from exc

    if resp.status_code >= 400 or not cuerpo.get("success", False):
        errores = cuerpo.get("errors") or []
        primero = errores[0] if errores else {}
        codigo = primero.get("code")
        mensaje = (
            primero.get("message")
            or f"Cloudflare respondió {resp.status_code} sin detalle de error."
        )
        raise CredencialCloudflareInvalidaError(
            f"Cloudflare rechazó la credencial (cuenta {cuenta_id}): {mensaje}", codigo=codigo
        )
