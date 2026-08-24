"""``MotorOpencode`` -- el ciclo de vida completo, a prueba de cuelgues.

# POR QUÉ existe este archivo

Los cuatro módulos del cimiento (``ide_opencode.py``, ``ide_opencode_permisos.py``,
``ide_opencode_config.py``, ``ide_opencode_binario.py``) ya saben, cada uno por
separado, arrancar un ``opencode serve``, hablar su API, aplicar los cuatro modos
de Edecán y resolver el binario dentro de la app empaquetada. Ninguno decide QUIÉN
los orquesta: quién arranca qué, en qué orden, cuántas veces, y qué pasa si algo se
muere a medio camino. Ese es el trabajo de este archivo, y es el que cierra la
brecha más peligrosa que dejó la ronda anterior (ver ``docs/opencode-motor.md``
§3): **con la configuración que este mismo cimiento genera, cualquier sesión que
hable con opencode sin tener ``PuenteDePermisos.escuchar()`` corriendo en paralelo
se cuelga para siempre en el primer edit/bash/webfetch real -- sin error, sin
timeout de opencode, silencio total.** Verificado en vivo dos veces en la ronda
anterior. La regla dura de este módulo, la que gobierna cada decisión de diseño de
abajo, es la que ya deja escrita el encargo: el puente arranca ANTES del primer
prompt, siempre, y si esa tarea se muere, el motor tiene que darse cuenta y
decirlo -- nunca quedarse mudo.

# Las cinco decisiones de este módulo, y por qué cada una es así

1. **Un ``ServidorOpencode`` por workspace, reutilizado, con candado por
   workspace.** Dos sesiones que piden el mismo directorio al mismo tiempo no
   pueden arrancar dos procesos ``opencode serve`` compitiendo por el mismo
   ``opencode.json`` -- ``MotorOpencode`` guarda un ``asyncio.Lock`` por
   workspace (creado bajo un candado propio, para que crear EL candado tampoco
   sea una carrera) y todo el que pide ``servidor_para()`` para ese directorio
   espera su turno; el primero arranca, el resto reutiliza.

2. **El puente arranca DENTRO de ``servidor_para()``, antes de devolver nada, y
   si muere en el primer instante, la función entera falla.** No basta con
   lanzar la tarea de fondo y confiar en que esté viva: justo después de
   crearla, este módulo le cede una vuelta del loop (``await asyncio.sleep(0)``)
   y comprueba si ya terminó -- una credencial mala, un ``opencode.json`` roto,
   o cualquier fallo inmediato de ``PuenteDePermisos.escuchar()`` se detecta ANTES
   de que quien llamó a este motor crea que tiene una sesión lista para recibir
   prompts. Después de ese instante, la tarea puede morir más tarde (red,
   proceso), y para ESO existe la salud (decisión 4).

3. **Antes de arrancar el servidor, este módulo llama a
   ``ide_opencode_config.generar_opencode_json()``.** Sin esa llamada, el
   ``opencode.json`` del workspace puede no tener ``permission: ask`` -- y sin
   eso, opencode nunca emite ``permission.v2.asked``, así que el puente que
   este motor tan cuidadosamente mantiene vivo no tendría NADA que
   interceptar (la app volvería al síntoma original del dueño: "dice que lo
   hizo y no lo hizo", ahora con el motor nuevo). Este módulo no reescribe esa
   función -- la llama, con las mismas reglas de "nunca pisar lo del dueño"
   que ya trae.

4. **Salud con reposición acotada, nunca un bucle infinito.** ``salud()`` es de
   solo lectura (un GET real a ``/health``, más el estado de la tarea del
   puente). ``comprobar_y_reponer()`` es la única función que actúa: si el
   proceso murió, reinicia TODO el stack de ese workspace (servidor + puente);
   si solo murió el puente (el servidor sigue respondiendo), reinicia solo el
   puente y llama a ``recuperar_pendientes()`` antes de seguir escuchando --
   para atrapar cualquier solicitud que haya quedado huérfana durante el hueco
   sin puente (mismo mecanismo que ya prueba
   ``test_ide_opencode_permisos.py::test_sin_puente_...``). Cada mitad
   (servidor/puente) tiene su PROPIO contador de reintentos, tope
   ``max_reintentos`` (3 por defecto): agotado el tope, ``comprobar_y_reponer``
   dEJA de intentar y lo dice en el resultado (``motivo_puente``) -- nunca
   reintenta para siempre en silencio.

5. **El secreto en ``opencode.json``: 0600 tras cada escritura, cinturón y
   tirantes.** Una ronda posterior (ver "TERCERA CORRECCIÓN" en el docstring
   de ``ide_opencode_config.py``) intentó además quitarle a
   ``generar_opencode_json()`` el token en claro -- probó
   ``"apiKey": "{env:CLOUDFLARE_API_TOKEN}"`` contra un ``opencode serve``
   real haciendo una llamada de verdad (no solo ``GET /config``) y
   comprobó que NO se resuelve para un proveedor custom: el modelo nunca
   llega a responder, HTTP 401 instantáneo. Por eso el token SIGUE en claro
   en el JSON -- lo único que sí se corrigió es el permiso del archivo, y
   ese módulo YA se blinda solo con su propio ``chmod 0600``. Este motor
   sigue haciendo el suyo de todas formas, best-effort (igual que
   ``ide_opencode_binario._asegurar_bit_ejecutable``: si falla, se loguea,
   no revienta el arranque) -- doble blindaje a propósito, no una
   redundancia por descuido: si algún día ``ide_opencode_config.py`` cambia
   y deja de aplicar el suyo, el archivo que arranca este motor sigue
   quedando en 0600 igual.

# ``modelo_para(perfil, esfuerzo)`` -- de dónde sale el mapeo

El encargo pide: "Bajo -> el modelo rápido; Alto -> el fuerte", leído de
``config/modelos.yml``, nunca escrito a mano. Ese YAML no declara un tercer
modelo "medio" por perfil -- solo ``perfiles.<perfil>.modelo`` (el elegido,
"el fuerte") y ``perfiles.<perfil>.modelo_alternativo`` (declarado
explícitamente con ese nombre, y para ``ingenieria_software`` es
``gpt-oss-120b``, medido en el propio YAML como el más rápido del perfil:
0.58s por vuelta contra 1.25s de ``kimi-k2.7-code``). La correspondencia es
directa y no se inventa nada:

- **bajo** -> ``modelo_alternativo`` (o ``modelo`` si el perfil no declaró uno
  alternativo) + ``reasoning_effort="low"``.
- **alto** -> ``modelo`` (el fuerte, el elegido) + ``reasoning_effort="high"``.
- **medio** -> decisión propia, documentada aquí porque el YAML no tiene un
  tercer nivel: se queda con el MISMO modelo fuerte de ``alto`` pero pide
  ``reasoning_effort="medium"``. La razón está en la propia memoria de esta
  migración (medido en esta Mac): subir el ``reasoning_effort`` no sube el
  costo de forma proporcional, pero SÍ tiende a subir el acierto -- así que
  degradar el MODELO en el nivel medio (en vez de solo bajar el esfuerzo)
  tiraría acierto sin necesidad. Solo "bajo" paga con un modelo más débil a
  cambio de velocidad; "medio" y "alto" comparten el modelo fuerte y solo se
  diferencian en cuánto se le pide razonar.

``providerID`` siempre es ``ide_opencode_config.PROVEEDOR_WORKERS_AI_ID``
("workersai") -- la MISMA constante que ``ide_opencode_config.py`` ya usa
para escribir ``opencode.json`` (no la cadena ``proveedor_por_defecto`` del
YAML, que es un identificador interno distinto, ver el docstring de ese
módulo). Reutilizar la constante en vez de escribir "workersai" de nuevo acá
evita que las dos cadenas se desincronicen si algún día cambian.

# Cómo se comprueba "el servidor sigue vivo"

Igual que ``ServidorOpencode.iniciar()`` (mismo criterio, no uno nuevo): un
``GET`` real a ``{servidor.base_url}/health`` con un timeout corto. No se
toca ningún atributo privado de ``ServidorOpencode`` (``_proceso``, etc.) --
``base_url`` ya es parte de su superficie pública y ``/health`` es el mismo
endpoint que ese módulo usa para decidir "ya puede atender una petición
real" al arrancar.

# Pendiente, sin adornos (encargo punto 6, "avisa si crees que hace falta más")

- QUINTA CORRECCIÓN (ronda Windows) -- ``chmod 0600`` es POSIX puro, así que
  en Windows (``os.name == "nt"``) SÍ había quedado como no-op explícito:
  el ``opencode.json`` con el token de Cloudflare en claro quedaba con los
  permisos que heredara de la carpeta contenedora (confirmado en vivo contra
  la VM de Windows de este proyecto: ``BUILTIN\\Users:(RX)`` -- cualquier
  cuenta local podía leerlo). Ya NO es un no-op: ``_restringir_windows``
  corre ``icacls <ruta> /inheritance:r /grant:r <cuenta>:F /grant:r
  SYSTEM:F`` (``<cuenta>`` es quien corre este proceso, ver
  ``_cuenta_windows_actual``), la misma sintaxis que se verificó en vivo en
  la fase anterior (con una cuenta fija) contra esa misma VM. Sigue
  pendiente que una fase con acceso a la VM confirme, contra un
  ``opencode.json`` real generado por ESTE motor, que la cuenta resuelta
  dinámicamente también deja a ``BUILTIN\\Users`` sin acceso -- esta ronda
  solo pudo probar la lógica (macOS no tiene ``icacls``; "no te conectes a
  la VM" era una regla explícita del encargo de esta ronda).
- 0600/icacls protegen contra OTROS USUARIOS del mismo sistema de archivos,
  no contra: (a) alguien con acceso físico/root/Administrador a esta
  máquina (en Windows, un Administrador puede tomar posesión del archivo y
  reescribir su ACL pase lo que diga ``icacls`` -- mismo límite que "root
  siempre puede leer un 0600" en POSIX, ninguna de las dos rutas lo
  resuelve), (b) el propio
  proceso de Edecán leyéndolo y filtrándolo por accidente (p. ej. un log que
  vuelque el archivo entero), (c) el workspace compartiéndose completo hacia
  otro lado (zip, backup, sync a la nube) sin que nadie recuerde que ese
  ``opencode.json`` va adentro. Nada de esto lo resuelve un ``chmod``. La
  referencia indirecta (``{env:CLOUDFLARE_API_TOKEN}`` en vez del token en
  claro) SE INTENTÓ en esta misma ronda -- ver ``ide_opencode_config.py``,
  "TERCERA CORRECCIÓN" -- y se descartó: probada contra un ``opencode
  serve`` real haciendo una llamada de verdad (no solo ``GET /config``), la
  referencia NO se resuelve para un proveedor custom -- el modelo nunca
  responde, HTTP 401 instantáneo, en las cuatro repeticiones controladas que
  se hicieron. El token sigue en claro en el archivo; (a)/(b)/(c) siguen sin
  cubrirse. Queda documentado como pendiente real, no una omisión: si una
  versión más nueva de opencode arregla esa resolución, retomar el intento
  (la constante ``_REFERENCIA_ENV_TOKEN`` de ``ide_opencode_config.py`` ya
  queda ahí, documentada, esperando).
- ``modelo_para()`` depende de que ``perfiles.<perfil>.modelo_alternativo``
  siga siendo el modelo más rápido de ese perfil -- es una convención de
  ``config/modelos.yml``, no algo que este módulo pueda verificar en tiempo
  de ejecución (no hay una medición de latencia embebida en el YAML, solo en
  sus comentarios). Si algún día se agrega un modelo "medio" explícito al
  YAML, este módulo debería empezar a leerlo en vez de reusar el fuerte --
  ver la sección de arriba.

# ``referencia_para_modelo()`` y ``variante_de_esfuerzo()`` -- selector vs. esfuerzo

Encargo de esta ronda (el dueño lo dijo literal: "Workers AI... el nivel de
esfuerzo decide el modelo e ignora por completo lo que la persona eligió en
el selector"): **el selector manda QUÉ modelo; el esfuerzo (Bajo/Medio/Alto)
solo manda CUÁNTO piensa ESE modelo, nunca lo cambia.** ``modelo_para()``
sigue existiendo tal cual -- es el DEFECTO cuando la persona no eligió nada
-- pero ya no es lo único que decide el modelo de una sesión:

- ``referencia_para_modelo(modelo_id, esfuerzo)`` construye la referencia
  para un modelo YA elegido por la persona: el ``providerID`` sigue siendo
  Workers AI (mismo criterio que ``modelo_para``, ver arriba), pero
  ``model_id`` es el que ella escogió, no el que decida el perfil. Quien
  llama (``ide_sessions.py``) es responsable de validar ese id contra
  ``edecan_llm.task_router.modelo_ide_permitido`` ANTES de llamar acá --
  este módulo no importa ``modelos_ide_disponibles`` porque no le hace
  falta: solo arma la referencia, no decide si el modelo es válido.

- ``variante_de_esfuerzo()`` es la respuesta comprobada (no inventada) a
  "¿cómo se manda ``reasoning_effort`` a un modelo ya elegido, con la sesión
  YA viva?". Se leyó ``GET /doc`` de un ``opencode serve`` real (mismo
  binario 1.17.18 de esta Mac) para las tres superficies candidatas:
  - ``POST /session`` (crear) y ``POST /session/{id}/model`` (cambiar en
    vivo): las dos solo aceptan un ``ModelRef`` -- ``{id, providerID,
    variant}`` (``#/components/schemas/ModelRef``). Nada de "opciones" ni
    "reasoningEffort" sueltos.
  - ``POST /session/{id}/prompt``: su cuerpo (``PromptInput`` + ``id``/
    ``delivery``/``resume``) tampoco acepta ninguna opción de generación.
  - La ÚNICA superficie real es ``variant``: cada modelo, en el catálogo en
    vivo (``GET /model``, ``ModelV2Info.variants``), puede declarar un
    array de variantes ``{id, headers, body}`` -- overlays de config,
    declarados en el ``opencode.json``/config de providers del workspace,
    no algo que se pueda inventar en tiempo de request. ``variante_de_esfuerzo``
    consulta ESE catálogo en vivo y busca una variante cuyo ``id`` sea
    EXACTAMENTE el nombre del ``reasoning_effort`` pedido ("low"/"medium"/
    "high" -- la misma convención que ya usa ``_REASONING_EFFORT_POR_NIVEL``
    y el resto de Edecán, nunca un nombre inventado para la ocasión).

  Comprobado contra los cinco modelos de Workers AI que ofrece hoy el
  selector del IDE (``config/modelos.yml`` -> ``modelos_ide``): NINGUNO
  declara ``variants`` -- ni en ese YAML ni en el ``opencode.json`` que
  genera ``ide_opencode_config.generar_opencode_json`` (no declara ninguna
  sección ``variants``/``options`` por modelo). Así que hoy
  ``variante_de_esfuerzo`` siempre devuelve ``None`` para esos cinco, y ESE
  es el pendiente real (no una omisión): **no existe, hoy, ninguna vía para
  mandarle ``reasoning_effort`` a una sesión de opencode YA viva cuyo modelo
  no declare variantes.** ``ide_sessions.fijar_esfuerzo`` lo declara así en
  vez de fingir que lo mandó -- ver su docstring. Si mañana
  ``ide_opencode_config.py`` (u otro módulo) empieza a declarar variantes
  ``low``/``medium``/``high`` para estos modelos, esta función las detecta
  sola, sin cambiar una línea.
"""

from __future__ import annotations

import asyncio
import getpass
import logging
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
from edecan_llm.task_router import cargar_configuracion_modelos

from .ide_opencode import ServidorOpencode
from .ide_opencode_config import (
    PROVEEDOR_WORKERS_AI_ID,
    ResultadoGeneracionOpencodeJson,
    generar_opencode_json,
)
from .ide_opencode_permisos import ModoAgente, PuenteDePermisos

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Errores -- honestos a propósito, mismo criterio que el resto de ide_opencode*
# --------------------------------------------------------------------------- #


class MotorOpencodeError(RuntimeError):
    """Un fallo del propio motor (no de opencode ni de Cloudflare): un perfil
    que no existe, un workspace que no existe, o pedir salud de un workspace
    que nunca se arrancó."""


class PuenteDePermisosCaidoError(MotorOpencodeError):
    """``PuenteDePermisos.escuchar()`` murió (con excepción propia, o
    terminó solo sin ninguna) y este motor no pudo dejarlo funcionando --
    ya sea al arrancar, ya sea tras agotar los reintentos de
    ``comprobar_y_reponer``. Ver docs/opencode-motor.md §3: sin el puente
    vivo, el próximo edit/bash/webfetch real de ese workspace se cuelga
    para siempre, sin ningún otro aviso de opencode."""


# --------------------------------------------------------------------------- #
# modelo_para(perfil, esfuerzo) -- ver la sección del docstring del módulo
# --------------------------------------------------------------------------- #

#: Los tres niveles que expone este motor -- deliberadamente NO se importan
#: de ``ide_modos.NIVELES_ESFUERZO`` (ese archivo está bajo edición de otro
#: workflow en paralelo, mismo criterio que ``ide_opencode_permisos.py`` usa
#: para no importar ``ide_modos.ModoAgente``, ver su docstring). Mismos
#: nombres y mismos valores de ``reasoning_effort`` que ese módulo por
#: convención compartida, no por identidad de código.
NIVELES_ESFUERZO_MOTOR: tuple[str, ...] = ("bajo", "medio", "alto")

_REASONING_EFFORT_POR_NIVEL: dict[str, str] = {"bajo": "low", "medio": "medium", "alto": "high"}


@dataclass(slots=True, frozen=True)
class ReferenciaModeloEsfuerzo:
    """Lo que ``modelo_para``/``referencia_para_modelo`` devuelven: listo
    para pasar a ``ServidorOpencode.crear_sesion``/``cambiar_modelo``
    (``provider_id`` + ``model_id`` [+ ``variante``]) y para lo que sea que
    mande ``reasoning_effort`` al proveedor.

    ``variante`` empieza en ``None`` -- ninguna de las dos funciones que
    construyen esto sabe, por sí sola, si el modelo declara una variante
    para ``reasoning_effort`` (eso requiere una consulta EN VIVO al
    catálogo de opencode, ver ``variante_de_esfuerzo``). Quien la aplica
    de verdad contra una sesión (``ide_sessions.py``) es quien rellena este
    campo con lo que esa consulta haya encontrado, vía
    ``dataclasses.replace``."""

    provider_id: str
    model_id: str
    reasoning_effort: str
    nivel: str
    variante: str | None = None


def _normalizar_nivel(esfuerzo: str) -> str:
    if not isinstance(esfuerzo, str) or not esfuerzo.strip():
        raise MotorOpencodeError("El nivel de esfuerzo no puede estar vacío.")
    clave = esfuerzo.strip().casefold()
    if clave not in NIVELES_ESFUERZO_MOTOR:
        disponibles = ", ".join(NIVELES_ESFUERZO_MOTOR)
        raise MotorOpencodeError(
            f"Nivel de esfuerzo desconocido: «{esfuerzo}». Usa uno de: {disponibles}."
        )
    return clave


def modelo_para(
    perfil: str, esfuerzo: str, *, ruta_yaml_modelos: Path | str | None = None
) -> ReferenciaModeloEsfuerzo:
    """``(providerID, modelID, reasoning_effort)`` para ``perfil`` al nivel
    ``esfuerzo`` ("bajo"/"medio"/"alto"), leído de ``config/modelos.yml`` en
    cada llamada -- nunca copiado a mano (ver la sección del docstring del
    módulo para el mapeo exacto y por qué). Lanza :class:`MotorOpencodeError`
    si el perfil no está declarado o no tiene ``modelo`` -- nunca inventa un
    modelo para un perfil que no existe."""

    nivel = _normalizar_nivel(esfuerzo)
    config = cargar_configuracion_modelos(ruta_yaml_modelos)
    perfiles = config.get("perfiles")
    perfil_cfg = perfiles.get(perfil) if isinstance(perfiles, dict) else None
    if not isinstance(perfil_cfg, dict):
        raise MotorOpencodeError(
            f"El perfil «{perfil}» no está declarado en 'perfiles' de config/modelos.yml -- "
            "no invento un modelo para un perfil que no existe."
        )
    modelo_fuerte = perfil_cfg.get("modelo")
    if not modelo_fuerte:
        raise MotorOpencodeError(
            f"'perfiles.{perfil}.modelo' no está declarado en config/modelos.yml."
        )
    modelo_rapido = perfil_cfg.get("modelo_alternativo") or modelo_fuerte
    modelo = str(modelo_rapido) if nivel == "bajo" else str(modelo_fuerte)
    return ReferenciaModeloEsfuerzo(
        provider_id=PROVEEDOR_WORKERS_AI_ID,
        model_id=modelo,
        reasoning_effort=_REASONING_EFFORT_POR_NIVEL[nivel],
        nivel=nivel,
    )


def referencia_para_modelo(modelo_id: str, esfuerzo: str) -> ReferenciaModeloEsfuerzo:
    """``ReferenciaModeloEsfuerzo`` para un modelo YA elegido por la
    persona -- ver la sección del docstring del módulo ("selector vs.
    esfuerzo"). A diferencia de ``modelo_para``, esto NO consulta
    ``perfiles.<perfil>.modelo`` de ``config/modelos.yml`` para decidir EL
    MODELO -- ya vino decidido; el YAML solo entra, vía
    ``_REASONING_EFFORT_POR_NIVEL``, para traducir ``esfuerzo`` al
    ``reasoning_effort`` que le corresponde. ``providerID`` es siempre
    Workers AI (mismo criterio que ``modelo_para``): hoy el catálogo del
    selector del IDE (``edecan_llm.task_router.modelos_ide_disponibles``)
    solo ofrece modelos de esa cuenta. Quien llama es responsable de haber
    validado ``modelo_id`` contra ese catálogo ANTES de llamar acá -- este
    módulo no lo revalida, solo arma la referencia."""

    nivel = _normalizar_nivel(esfuerzo)
    if not isinstance(modelo_id, str) or not modelo_id.strip():
        raise MotorOpencodeError("El modelo elegido no puede estar vacío.")
    return ReferenciaModeloEsfuerzo(
        provider_id=PROVEEDOR_WORKERS_AI_ID,
        model_id=modelo_id.strip(),
        reasoning_effort=_REASONING_EFFORT_POR_NIVEL[nivel],
        nivel=nivel,
    )


async def variante_de_esfuerzo(
    servidor: ServidorOpencode,
    *,
    proveedor: str,
    modelo: str,
    reasoning_effort: str,
    timeout: float = 5.0,
) -> str | None:
    """Busca, en el catálogo EN VIVO de modelos de opencode (``GET
    /model`` -- ver ``ModelV2Info.variants`` en ``/doc``, comprobado contra
    un ``opencode serve`` real, la sección "referencia_para_modelo() y
    variante_de_esfuerzo()" del docstring del módulo tiene el detalle
    completo de por qué esta es la ÚNICA vía), si ``modelo``/``proveedor``
    declara una variante cuyo ``id`` sea EXACTAMENTE ``reasoning_effort``
    ("low"/"medium"/"high"). Si la encuentra, devuelve ese mismo string
    (listo para pasar como ``variante=`` a ``crear_sesion``/``cambiar_modelo``);
    si el modelo no aparece en el catálogo, no declara ninguna variante con
    ese nombre, o el catálogo no se pudo consultar (servidor caído a medio
    camino, tiempo agotado), devuelve ``None`` -- eso es lo que
    ``ide_sessions.py`` usa para decidir que hoy NO hay ninguna vía en vivo
    para el ``reasoning_effort`` de este modelo, en vez de fingir que se
    mandó."""

    # El catálogo de un proveedor CUSTOM tarda ~1-2 s en poblarse DESPUÉS de
    # que el servidor ya responde -- medido: `servidor_para()` volvió en 0,80 s,
    # y este catálogo devolvió vacío a t+1,11 s y completo a t+2,22 s, sin
    # ninguna sesión de por medio. Es carga perezosa de arranque de opencode.
    #
    # Sin este reintento, la PRIMERA conversación de cada arranque pierde el
    # nivel de esfuerzo elegido en silencio (los turnos siguientes sí lo
    # aplican) -- justo el tipo de fallo intermitente e inexplicable que este
    # proyecto lleva persiguiendo. Se reintenta con espera creciente hasta
    # ~3,5 s en total, que cubre con margen la ventana medida.
    esperas = (0.0, 0.4, 0.8, 1.2, 1.6)
    ultimo_error: Exception | None = None
    for intento, espera in enumerate(esperas):
        if espera:
            await asyncio.sleep(espera)
        try:
            async with httpx.AsyncClient(
                base_url=servidor.base_url, timeout=httpx.Timeout(timeout)
            ) as cliente:
                resp = await cliente.get("/model")
            resp.raise_for_status()
            cuerpo = resp.json()
        except (httpx.TransportError, httpx.HTTPStatusError, ValueError) as exc:
            ultimo_error = exc
            continue

        for entrada in cuerpo.get("data") or []:
            if not isinstance(entrada, dict):
                continue
            if entrada.get("providerID") != proveedor or entrada.get("id") != modelo:
                continue
            for variante in entrada.get("variants") or []:
                if isinstance(variante, dict) and variante.get("id") == reasoning_effort:
                    return reasoning_effort
            # El modelo SÍ está en el catálogo y no declara esa variante: es
            # una respuesta definitiva, no una carrera. No se reintenta.
            return None

        # El modelo todavía no aparece: puede ser la carga perezosa. Se
        # reintenta salvo que ya sea el último intento.
        if intento == len(esperas) - 1:
            logger.warning(
                "El catálogo de modelos de opencode nunca listó %s/%s tras %d intentos "
                "(~%.1f s) -- se sigue sin variante de esfuerzo para este turno.",
                proveedor,
                modelo,
                len(esperas),
                sum(esperas),
            )
    if ultimo_error is not None:
        logger.warning(
            "No pude consultar el catálogo de modelos en vivo de opencode para %s/%s: %s -- "
            "se asume que no declara ninguna variante (sin vía en vivo para su "
            "reasoning_effort).",
            proveedor,
            modelo,
            ultimo_error,
        )
    return None


# --------------------------------------------------------------------------- #
# El secreto en opencode.json -- encargo punto 6
# --------------------------------------------------------------------------- #


def _cuenta_windows_actual() -> str | None:
    """Copia deliberada de ``ide_opencode_config._cuenta_windows_actual`` --
    ver el docstring de esa función para la explicación completa (por qué
    ``USERDOMAIN``/``USERNAME``, y el respaldo con ``getpass.getuser()``).
    Duplicada a propósito en vez de importada: es la misma decisión de
    "cinturón y tirantes" que ya explica la decisión 5 del docstring de este
    módulo para ``_asegurar_permisos_restrictivos`` en su conjunto -- si
    ``ide_opencode_config.py`` cambia esta lógica algún día, el blindaje que
    hace ESTE motor no depende de que ese cambio sea correcto.

    Se mantiene tal cual (la usan tests existentes y el respaldo de
    ``_cuentas_windows_candidatas`` cuando no hay perfil de usuario) -- ver
    esa función para el arreglo real del hueco medido en vivo
    (``USERDOMAIN=WORKGROUP`` no es un dominio resoluble por icacls)."""

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
"""Copia deliberada de ``ide_opencode_config._DOMINIO_WORKGROUP_POR_DEFECTO``
-- mismo criterio de "cinturón y tirantes" que el resto de este bloque. Ver
el docstring de esa constante para la explicación completa (confirmado en
vivo contra la VM de este proyecto: ``USERDOMAIN`` vale ``WORKGROUP`` en una
máquina standalone y ``icacls`` lo rechaza con el código 1332)."""


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
    """Copia deliberada de ``ide_opencode_config._restringir_windows`` -- ver
    el docstring de esa función para la explicación completa (por qué
    ``icacls /inheritance:r /grant:r <cuenta>:F /grant:r SYSTEM:F``, por qué
    nunca ``shell=True``, por qué ahora se prueba una LISTA de candidatos en
    vez de una sola cuenta, y el PENDIENTE de verificación real contra la VM
    de Windows que aplica igual aquí: la lógica se probó en macOS, la
    sintaxis exacta se probó en vivo en la fase anterior con una cuenta fija
    -- falta que una fase con acceso a la VM confirme que ``.\\usuario``
    resuelve de verdad donde ``DOMINIO\\usuario`` falló con el código 1332)."""

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
    """``chmod 0600`` en POSIX, ``icacls`` real en Windows (ver
    ``_restringir_windows``) -- best-effort sobre ``opencode.json`` (que
    trae el token de Cloudflare en texto claro, ver
    ``ide_opencode_config.py``). Si la restricción falla (POSIX o Windows),
    se loguea y se sigue -- no vale la pena tumbar el arranque de un
    servidor por esto, pero tampoco se traga el fallo en silencio."""

    if os.name == "nt":
        _restringir_windows(ruta)
        return
    try:
        os.chmod(ruta, 0o600)
    except OSError as exc:
        logger.warning("No pude restringir los permisos de %s a 0600: %s", ruta, exc)


# --------------------------------------------------------------------------- #
# Salud -- foto de solo lectura de un workspace
# --------------------------------------------------------------------------- #


@dataclass(slots=True, frozen=True)
class SaludMotor:
    """Estado de un workspace en el instante en que se pidió. ``salud()`` la
    produce sin actuar; ``comprobar_y_reponer()`` actúa primero y devuelve la
    foto resultante."""

    directorio_trabajo: Path
    arrancado: bool
    """``False`` si nunca se llamó a ``servidor_para`` para este workspace, o
    si ya se llamó a ``detener`` -- distinto de "arrancado pero muerto"."""
    servidor_vivo: bool
    puente_vivo: bool
    motivo_puente: str | None
    """Por qué el puente no está vivo -- ``None`` si ``puente_vivo`` es
    ``True``."""
    reintentos_servidor: int
    reintentos_puente: int


async def _servidor_responde(servidor: ServidorOpencode, *, timeout: float = 3.0) -> bool:
    """Mismo criterio que ``ServidorOpencode.iniciar`` usa para decidir "ya
    puede atender una petición real": un ``GET`` de verdad a
    ``{base_url}/health``, nunca se asume vivo solo porque el objeto Python
    todavía existe."""

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as cliente:
            resp = await cliente.get(f"{servidor.base_url}/health")
    except httpx.TransportError:
        return False
    return resp.status_code == 200


def _diagnosticar_tarea_puente(tarea: asyncio.Task[None]) -> tuple[bool, str | None]:
    """``(vivo, motivo)`` para la tarea de fondo de ``PuenteDePermisos.escuchar()``.

    Cubre los TRES finales posibles, no solo "murió con excepción": una
    tarea cancelada a propósito, una que murió con una excepción real (de
    opencode o de este módulo), y -- el caso más traicionero, porque no
    grita nada por sí solo -- una que ``escuchar()`` terminó sola sin
    ninguna excepción (ver ``_correr_puente`` más abajo: eso no debería
    pasar nunca porque ese método lo convierte en
    :class:`PuenteDePermisosCaidoError`, pero esta función no confía en esa
    garantía y lo detecta de todas formas por si ``escuchar()`` cambia de
    comportamiento en una versión futura de opencode)."""

    if not tarea.done():
        return True, None
    if tarea.cancelled():
        return False, "la tarea de PuenteDePermisos.escuchar() fue cancelada"
    exc = tarea.exception()
    if exc is not None:
        return False, f"{type(exc).__name__}: {exc}"
    return False, "PuenteDePermisos.escuchar() terminó sin excepción (no debería pasar nunca)"


# --------------------------------------------------------------------------- #
# El motor
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _EntradaMotor:
    """Todo lo que ``MotorOpencode`` guarda por workspace. Mutable a
    propósito (a diferencia de ``SaludMotor``, que es una foto): los
    contadores de reintento y la tarea/puente vigentes cambian con
    ``comprobar_y_reponer``."""

    servidor: ServidorOpencode
    puente: PuenteDePermisos
    tarea_puente: asyncio.Task[None]
    aviso_puente_requerido: str | None = None
    reintentos_servidor: int = 0
    reintentos_puente: int = 0


async def _correr_puente(puente: PuenteDePermisos) -> None:
    """El cuerpo de la tarea de fondo: agota ``puente.escuchar()`` (que en
    condiciones normales nunca termina -- es un bus en vivo). Si el
    ``async for`` sale por cualquier otra vía que no sea cancelación, esto
    SIEMPRE deja la tarea en estado de error -- nunca "terminó bien" -- para
    que ``_diagnosticar_tarea_puente`` (y por tanto ``salud``/
    ``comprobar_y_reponer``) lo detecten. Si ``escuchar()`` ya lanzó su
    propia excepción (``ErrorOpencode``, pérdida de conexión), esa excepción
    real se propaga tal cual -- este método solo agrega un motivo cuando
    opencode NO dio ninguno."""

    try:
        async for _evento in puente.escuchar():
            pass
    except asyncio.CancelledError:
        raise
    raise PuenteDePermisosCaidoError(
        "PuenteDePermisos.escuchar() del motor terminó por su cuenta -- el bus global de "
        "opencode (GET /api/event) se cerró sin que nadie lo cancelara. Desde este momento "
        "ningún permission.v2.asked/question.v2.asked de este workspace tiene quien lo "
        "atienda: el próximo edit/bash/webfetch real se cuelga para siempre (ver "
        "docs/opencode-motor.md §3)."
    )


class MotorOpencode:
    """Gestiona un :class:`ServidorOpencode` por workspace, reutilizado entre
    sesiones de ese workspace, con :class:`PuenteDePermisos` SIEMPRE
    escuchando desde antes del primer prompt. Ver el docstring del módulo
    para las cinco decisiones de diseño detrás de cada método.

    ``obtener_modo`` es la MISMA función síncrona que espera
    ``PuenteDePermisos`` (``session_id -> ModoAgente | str``) -- se comparte
    para todos los workspaces que gestione este motor porque los ``session_id``
    de opencode ya son únicos por servidor/sesión, así que no hace falta un
    ``obtener_modo`` distinto por workspace."""

    def __init__(
        self,
        *,
        obtener_modo: Callable[[str], ModoAgente | str],
        ruta_binario: str | Path | None = None,
        ruta_yaml_modelos: Path | str | None = None,
        ruta_env: Path | str | None = None,
        cuenta_id: str | None = None,
        api_token: str | None = None,
        permisos: dict[str, str] | None = None,
        max_reintentos: int = 3,
    ) -> None:
        if max_reintentos < 0:
            raise MotorOpencodeError("max_reintentos no puede ser negativo.")
        self._obtener_modo = obtener_modo
        self._ruta_binario = ruta_binario
        self._ruta_yaml_modelos = ruta_yaml_modelos
        self._ruta_env = ruta_env
        self._cuenta_id = cuenta_id
        self._api_token = api_token
        self._permisos = permisos
        self._max_reintentos = max_reintentos
        self._entradas: dict[Path, _EntradaMotor] = {}
        self._locks: dict[Path, asyncio.Lock] = {}
        self._lock_locks = asyncio.Lock()

    async def __aenter__(self) -> MotorOpencode:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.detener_todo()

    # ----------------------------------------------------------------- #
    # Candado por workspace -- decisión 1 del docstring del módulo
    # ----------------------------------------------------------------- #

    async def _lock_para(self, directorio: Path) -> asyncio.Lock:
        async with self._lock_locks:
            lock = self._locks.get(directorio)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[directorio] = lock
            return lock

    # ----------------------------------------------------------------- #
    # Arranque / reutilización
    # ----------------------------------------------------------------- #

    def _preparar_config_workspace(self, directorio: Path) -> ResultadoGeneracionOpencodeJson:
        """Decisión 3 del docstring del módulo: asegura ``opencode.json`` con
        ``provider.workersai`` + ``permission: ask`` ANTES de arrancar el
        proceso, y blinda el archivo (decisión 5) apenas se escribe."""

        resultado = generar_opencode_json(
            directorio,
            ruta_yaml_modelos=self._ruta_yaml_modelos,
            cuenta_id=self._cuenta_id,
            api_token=self._api_token,
            ruta_env=self._ruta_env,
            permisos=self._permisos,
        )
        _asegurar_permisos_restrictivos(resultado.ruta)
        return resultado

    async def _arrancar_puente(
        self, servidor: ServidorOpencode
    ) -> tuple[PuenteDePermisos, asyncio.Task[None]]:
        """Decisión 2 del docstring del módulo: arranca la tarea de fondo y
        comprueba, tras cederle una vuelta del loop, que no haya muerto ya."""

        puente = PuenteDePermisos(servidor, obtener_modo=self._obtener_modo)
        tarea = asyncio.create_task(_correr_puente(puente))
        await asyncio.sleep(0)  # dejar que un fallo inmediato se manifieste ya
        vivo, motivo = _diagnosticar_tarea_puente(tarea)
        if not vivo:
            await puente.cerrar()
            raise PuenteDePermisosCaidoError(
                f"PuenteDePermisos no arrancó para {servidor.directorio_trabajo}: {motivo}"
            )
        return puente, tarea

    async def _arrancar(self, directorio: Path) -> _EntradaMotor:
        if not directorio.is_dir():
            raise ValueError(f"El workspace no existe o no es una carpeta: {directorio}")
        resultado_config = self._preparar_config_workspace(directorio)
        servidor = await ServidorOpencode.iniciar(directorio, ruta_binario=self._ruta_binario)
        try:
            puente, tarea = await self._arrancar_puente(servidor)
        except Exception:
            await servidor.detener()
            raise
        return _EntradaMotor(
            servidor=servidor,
            puente=puente,
            tarea_puente=tarea,
            aviso_puente_requerido=resultado_config.aviso_puente_requerido,
        )

    async def servidor_para(self, directorio_trabajo: str | Path) -> ServidorOpencode:
        """Arranca (o reutiliza) el ``ServidorOpencode`` de ``directorio_trabajo``,
        con el puente ya escuchando cuando esto devuelve. Dos llamadas
        concurrentes para el MISMO workspace esperan el candado de ese
        workspace -- una sola arranca, la otra reutiliza."""

        directorio = Path(directorio_trabajo).resolve()
        lock = await self._lock_para(directorio)
        async with lock:
            entrada = self._entradas.get(directorio)
            if entrada is not None:
                if entrada.servidor._proceso.returncode is not None or entrada.tarea_puente.done():
                    logger.warning(
                        "El servidor de opencode o puente para %s ya no está activo "
                        "(código %s). Reiniciando...",
                        directorio,
                        entrada.servidor._proceso.returncode,
                    )
                    await self._apagar_entrada(entrada)
                    self._entradas.pop(directorio, None)
                    entrada = None
                else:
                    return entrada.servidor

            entrada = await self._arrancar(directorio)
            self._entradas[directorio] = entrada
            return entrada.servidor

    # ----------------------------------------------------------------- #
    # Apagado
    # ----------------------------------------------------------------- #

    async def _apagar_entrada(self, entrada: _EntradaMotor, *, timeout: float = 5.0) -> None:
        entrada.tarea_puente.cancel()
        try:
            await entrada.tarea_puente
        except asyncio.CancelledError:
            pass
        except BaseException as exc:  # noqa: BLE001 -- apagando: se registra, no se propaga
            logger.warning("PuenteDePermisos terminó con error durante el apagado: %s", exc)
        await entrada.puente.cerrar()
        await entrada.servidor.detener(timeout=timeout)

    async def detener(self, directorio_trabajo: str | Path, *, timeout: float = 5.0) -> None:
        """Para limpio el servidor y el puente de UN workspace. No-op si ese
        workspace nunca se arrancó (o ya se detuvo)."""

        directorio = Path(directorio_trabajo).resolve()
        lock = await self._lock_para(directorio)
        async with lock:
            entrada = self._entradas.pop(directorio, None)
            if entrada is None:
                return
            await self._apagar_entrada(entrada, timeout=timeout)

    async def detener_todo(self) -> None:
        """Para limpio TODOS los workspaces gestionados por este motor --
        pensado para el cierre de la app / gestor de contexto."""

        for directorio in list(self._entradas):
            await self.detener(directorio)

    # ----------------------------------------------------------------- #
    # Salud -- decisión 4 del docstring del módulo
    # ----------------------------------------------------------------- #

    async def salud(self, directorio_trabajo: str | Path) -> SaludMotor:
        """Foto de solo lectura: no repone nada, solo mira. Para reponer, ver
        :meth:`comprobar_y_reponer`."""

        directorio = Path(directorio_trabajo).resolve()
        entrada = self._entradas.get(directorio)
        if entrada is None:
            return SaludMotor(
                directorio_trabajo=directorio,
                arrancado=False,
                servidor_vivo=False,
                puente_vivo=False,
                motivo_puente=None,
                reintentos_servidor=0,
                reintentos_puente=0,
            )
        servidor_vivo = await _servidor_responde(entrada.servidor)
        puente_vivo, motivo_puente = _diagnosticar_tarea_puente(entrada.tarea_puente)
        return SaludMotor(
            directorio_trabajo=directorio,
            arrancado=True,
            servidor_vivo=servidor_vivo,
            puente_vivo=puente_vivo,
            motivo_puente=motivo_puente,
            reintentos_servidor=entrada.reintentos_servidor,
            reintentos_puente=entrada.reintentos_puente,
        )

    async def comprobar_y_reponer(self, directorio_trabajo: str | Path) -> SaludMotor:
        """Comprueba la salud de ``directorio_trabajo`` y, si algo murió,
        intenta reponerlo -- con tope ``max_reintentos`` POR MITAD (servidor y
        puente cuentan cada uno el suyo), nunca un bucle infinito. Si el
        servidor murió, se reinicia TODO el stack (no tiene sentido reponer
        solo el puente contra un proceso muerto). Si solo murió el puente, se
        reinicia solo el puente y se llama a ``recuperar_pendientes`` antes de
        devolver -- para atrapar cualquier solicitud que haya quedado
        huérfana durante el hueco (ver docs/opencode-motor.md §3)."""

        directorio = Path(directorio_trabajo).resolve()
        lock = await self._lock_para(directorio)
        async with lock:
            entrada = self._entradas.get(directorio)
            if entrada is None:
                raise MotorOpencodeError(
                    f"No hay ningún motor arrancado para {directorio} -- llama a "
                    "servidor_para primero."
                )

            servidor_vivo = await _servidor_responde(entrada.servidor)
            if not servidor_vivo:
                if entrada.reintentos_servidor >= self._max_reintentos:
                    return SaludMotor(
                        directorio_trabajo=directorio,
                        arrancado=True,
                        servidor_vivo=False,
                        puente_vivo=False,
                        motivo_puente=(
                            f"el proceso opencode de {directorio} murió y ya se agotaron los "
                            f"{self._max_reintentos} reintentos de reposición -- requiere "
                            "intervención humana"
                        ),
                        reintentos_servidor=entrada.reintentos_servidor,
                        reintentos_puente=entrada.reintentos_puente,
                    )
                reintentos = entrada.reintentos_servidor + 1
                # Ya está muerto -- apagar lo que quede es solo limpieza de
                # recursos Python (cancelar la tarea, cerrar el cliente HTTP
                # del puente), no hay proceso vivo al que mandarle SIGTERM.
                await self._apagar_entrada(entrada, timeout=2.0)
                nueva = await self._arrancar(directorio)
                nueva.reintentos_servidor = reintentos
                nueva.reintentos_puente = entrada.reintentos_puente
                self._entradas[directorio] = nueva
                entrada = nueva
            else:
                puente_vivo, motivo_puente = _diagnosticar_tarea_puente(entrada.tarea_puente)
                if not puente_vivo:
                    if entrada.reintentos_puente >= self._max_reintentos:
                        return SaludMotor(
                            directorio_trabajo=directorio,
                            arrancado=True,
                            servidor_vivo=True,
                            puente_vivo=False,
                            motivo_puente=(
                                f"{motivo_puente} -- ya se agotaron los {self._max_reintentos} "
                                "reintentos de reposición del puente, requiere intervención "
                                "humana"
                            ),
                            reintentos_servidor=entrada.reintentos_servidor,
                            reintentos_puente=entrada.reintentos_puente,
                        )
                    await entrada.puente.cerrar()
                    puente_nuevo, tarea_nueva = await self._arrancar_puente(entrada.servidor)
                    # Catch-up: nada de lo que haya pedido permiso durante el
                    # hueco sin puente lo va a resolver opencode solo (sin
                    # timeout de su lado, ver docs/opencode-motor.md §3).
                    await puente_nuevo.recuperar_pendientes(str(directorio))
                    entrada.puente = puente_nuevo
                    entrada.tarea_puente = tarea_nueva
                    entrada.reintentos_puente += 1

            puente_vivo, motivo_puente = _diagnosticar_tarea_puente(entrada.tarea_puente)
            return SaludMotor(
                directorio_trabajo=directorio,
                arrancado=True,
                servidor_vivo=True,
                puente_vivo=puente_vivo,
                motivo_puente=motivo_puente,
                reintentos_servidor=entrada.reintentos_servidor,
                reintentos_puente=entrada.reintentos_puente,
            )
