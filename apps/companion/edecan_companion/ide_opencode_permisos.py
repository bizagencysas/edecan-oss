"""Los cuatro modos del IDE, por fin de verdad -- contra la API de permisos real de opencode.

# POR QUÉ existe este archivo

La interfaz de Edecán lleva tiempo ofreciendo cuatro modos (Manual, Aceptar
ediciones, Plan, Auto) y NINGUNO frena nada de verdad -- ``ide_modos.py`` ya
tiene una tabla de decisión (``decidir``) y un ``verificar_accion_permitida``
que ningún despachador real llama. El dueño lo sufre al revés: como Plan es
el único modo con respaldo, todo acaba forzado a Plan, y lo dijo tal cual:
*"es INCOMODO ESO"*. Con opencode como motor esto deja de ser un invento
nuestro -- opencode tiene una API de permisos real (``POST
.../permission/{id}/reply``) y este archivo es el puente entre esa API y los
cuatro modos de Edecán: decide, para cada solicitud de permiso real que
opencode crea, si se concede sola, se rechaza sola, o se le pregunta a la
persona -- y expone lo mismo para las preguntas que el propio agente le hace
al usuario (``/question``), que el dueño también pidió: *"la IA debe
hablarme"*.

Este módulo NO importa ``ide_modos.py`` a propósito -- está bajo edición de
OTRO workflow en paralelo ahora mismo (ver la regla del encargo). Redefine
aquí su propio ``ModoAgente`` con los MISMOS cuatro valores de cadena
(``manual``/``aceptar_ediciones``/``plan``/``auto``) para que quien cablee
este puente después pueda pasar directamente el ``.value`` del modo vigente
de la sesión sin traducir nada -- son intercambiables por valor, no por
identidad de clase. Tampoco reescribe ``ide_opencode.py`` (\"el cliente ya
construido, úsalo\"): lo importa y arma su propio cliente HTTP contra
``ServidorOpencode.base_url`` para el resto de la superficie de permisos, que
ese módulo no cubre.

# Hallazgos empíricos -- nada de esto se adivinó

Antes de escribir una sola línea de este archivo se arrancó un ``opencode
serve`` real (Workers AI real, mismo proveedor que ``ide_opencode.py``), se
dispararon escrituras/comandos/preguntas reales y se leyó ``/doc`` para cada
forma. Cinco hallazgos que no son obvios desde afuera:

1. **El stream por sesión (``GET /api/session/{id}/event``, el que ya envuelve
   ``ServidorOpencode.eventos``) NUNCA trae eventos de permisos ni de
   preguntas.** Se confirmó dos veces: leyendo el esquema de ``/doc``
   (``SessionDurableEvent`` es una unión de 27 variantes -- ninguna es
   ``permission.v2.*`` ni ``question.v2.*``) y en vivo (se disparó una
   escritura real con un ``opencode.json`` que pide ``edit: ask``, se escuchó
   ese stream varios segundos, y jamás llegó nada -- mientras tanto ``GET
   .../permission`` sí mostraba la solicitud pendiente). Por eso este módulo
   NO reutiliza ``ide_opencode.ServidorOpencode.eventos``: para permisos y
   preguntas hace falta otra fuente.

2. **Esa otra fuente es el bus GLOBAL, ``GET /api/event`` (con el prefijo
   ``/api`` -- el ``/event`` sin prefijo es la superficie vieja del TUI, con
   otra forma de evento, ``{properties: ...}`` en vez de ``{data: ...}``, y no
   se usa aquí).** Su esquema (``V2Event``) sí incluye
   ``permission.v2.asked``/``permission.v2.replied`` y
   ``question.v2.asked``/``question.v2.replied``/``question.v2.rejected``, con
   la misma forma ``{id, type, data, location}`` que el resto -- se comprobó
   en vivo: al responder una solicitud pendiente llegó un
   ``permission.v2.replied`` con exactamente esa forma.

3. **Ese bus global es EN VIVO, no un registro que se pueda reproducir.** Una
   solicitud creada ANTES de conectarse al stream nunca llega por él -- se
   comprobó: se creó una solicitud de permiso, se esperó, y conectarse
   después no trajo nada (a diferencia del stream por sesión, este no tiene
   parámetro ``after``). Por eso este módulo separa "recuperar lo que ya
   estaba pendiente" (:meth:`PuenteDePermisos.permisos_pendientes_en`, sobre
   ``GET /api/permission/request``) de "escuchar lo nuevo"
   (:meth:`PuenteDePermisos.escuchar`, sobre ``GET /api/event``). Sin el
   primero, una solicitud creada justo antes de que este puente arranque
   queda huérfana para siempre.

4. **``GET /api/permission/request`` sin el parámetro ``location`` filtra por
   el directorio de trabajo del PROCESO ``opencode serve``, no por las
   sesiones.** Si las sesiones se crearon con un ``location.directory``
   distinto (el caso normal: cada sesión fija su propio directorio de
   proyecto), la lista sale vacía aunque haya solicitudes reales pendientes
   -- se comprobó exactamente ese caso. Por eso
   :meth:`PuenteDePermisos.permisos_pendientes_en` exige el directorio como
   argumento explícito, nunca lo asume.

5. **opencode NO declara ninguna bandera "esto es irreversible" en
   ``PermissionV2Request``.** Se comprobó con tres acciones reales
   (``edit``, ``bash``, ``webfetch``): ninguna trae un campo que lo diga.
   ``edit`` trae ``save=["*"]``, un ``bash`` con ``rm -f`` trae
   ``save=[<el comando exacto>]``, ``webfetch`` trae
   ``metadata={"url":..., "format":...}`` y ``save=["*"]`` -- ningún caso
   marca riesgo. Por eso, siguiendo el punto 4 del encargo ("si opencode no
   lo dice, define una lista corta y documenta que es tuya y por qué"), la
   sección "Qué es irreversible (decisión propia)" más abajo define una
   clasificación corta y la documenta como decisión de este módulo, no como
   algo que opencode declare.

   Un hallazgo relacionado que sí es de opencode, no propio: los NOMBRES de
   acción (``action`` en ``PermissionV2Request``) no se inventaron -- son
   exactamente las claves de ``PermissionConfig`` en ``/doc`` (``read``,
   ``edit``, ``glob``, ``grep``, ``list``, ``bash``, ``task``,
   ``external_directory``, ``todowrite``, ``question``, ``webfetch``,
   ``websearch``, ``lsp``, ``doom_loop``, ``skill``, más cualquier nombre de
   herramienta MCP/custom vía ``additionalProperties``) -- es la misma config
   que opencode ya usa para decidir ``ask``/``allow``/``deny`` por defecto,
   así que estos son los nombres reales, no una lista adivinada.

6. **"Recordar" (``reply: "always"``) se guarda a nivel de PROYECTO en
   opencode, no de sesión ni de modo Edecán.** ``PermissionSavedInfo`` trae
   ``projectID`` -- si este puente mandara ``"always"`` por su cuenta al
   auto-conceder en modo ``auto``/``aceptar_ediciones``, esa concesión
   quedaría viva en opencode para CUALQUIER sesión futura del mismo proyecto,
   incluida una que la persona ponga después en modo ``manual``. Por eso las
   ramas automáticas de la tabla de modos (``permitir``/``bloquear``) SIEMPRE
   responden ``"once"``/``"reject"`` -- nunca ``"always"``. Recordar sólo lo
   decide una persona real, vía :meth:`PuenteDePermisos.responder_permiso`
   con ``recordar=True``, y sólo si la clase no es irreversible y opencode
   mismo la ofrece como recordable (``save`` no vacío en la solicitud real).

# Qué es irreversible (decisión propia de este módulo, no de opencode)

Dos reglas cortas, ninguna inventada a ciegas -- la primera viene del propio
mapeo del encargo ("Auto ... para en lo irreversible (borrar, git push,
red)"), la segunda es un patrón de texto sobre el comando real que opencode
manda en ``resources`` para ``action == "bash"``:

- ``webfetch``, ``websearch`` y ``doom_loop`` son SIEMPRE clase ``PELIGROSA``
  -- "red" es exactamente la palabra que usa el encargo para lo que Auto debe
  frenar, y ``doom_loop`` es la propia señal de opencode de que el agente
  puede estar dando vueltas (equivalente a lo que ``ide_costos.py`` detecta
  del lado de Edecán) -- ambos ameritan una persona, no una tabla.
- Un ``bash`` cuyo texto de comando coincide con ``_PATRONES_COMANDO_
  IRREVERSIBLE`` (borrado recursivo/forzado, ``git push``, ``git reset
  --hard``, ``git clean -f``, ``DROP``/``TRUNCATE`` de base de datos, ``dd``,
  ``mkfs``, ``sudo``, ``chmod -R 777``, tuberías ``curl|sh``/``wget|sh``,
  ``npm publish``, ``docker system prune``, ``kill -9``, apagar/reiniciar la
  máquina) se sube a ``PELIGROSA`` aunque la clase base de ``bash`` sea
  ``COMANDO``. Es una lista corta A PROPÓSITO -- cualquier comando que no
  calce sigue siendo ``COMANDO`` (pide aprobación en Manual, se permite en
  Auto), nunca se disfraza de lectura.

Cualquier acción que este módulo no reconozca cae en ``PELIGROSA`` -- falla
cerrado, mismo criterio que ``ide_modos.clasificar_herramienta``.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from edecan_companion.ide_opencode import ErrorOpencode, ServidorOpencode


class IDEOpencodePermisosError(ValueError):
    """Un modo, clase o forma de solicitud inválida (mismo criterio de
    ``ide_modos.IDEModosError``: error de validación local, no de opencode)."""


class NoSePuedeRecordarError(IDEOpencodePermisosError):
    """``recordar=True`` rechazado por seguridad, nunca degradado en
    silencio: o la acción es irreversible (clase ``PELIGROSA``, regla 3 del
    encargo), o la propia solicitud de opencode no la ofrece como recordable
    (``save`` vacío). Se lanza en vez de tragarse el ``recordar=True`` y
    responder "once" sin avisar, para que quien llame se entere de que su
    intención de recordar no se cumplió."""


# --------------------------------------------------------------------------- #
# Los cuatro modos -- ver el docstring del módulo sobre por qué no se importa
# ide_modos.ModoAgente.
# --------------------------------------------------------------------------- #


class ModoAgente(StrEnum):
    """Los cuatro modos de trabajo del agente del IDE. Valores idénticos por
    diseño a ``edecan_companion.ide_modos.ModoAgente`` -- ver el docstring del
    módulo."""

    MANUAL = "manual"
    ACEPTAR_EDICIONES = "aceptar_ediciones"
    PLAN = "plan"
    AUTO = "auto"


def _parse_modo(valor: ModoAgente | str) -> ModoAgente:
    """Normaliza y valida un modo. Nunca inventa uno plausible ante un valor
    raro -- lanza con la lista de los válidos (mismo criterio que
    ``ide_modos._parse_modo``)."""

    if isinstance(valor, ModoAgente):
        return valor
    if not isinstance(valor, str) or not valor.strip():
        raise IDEOpencodePermisosError("El modo no puede estar vacío.")
    clave = valor.strip().casefold()
    for candidato in ModoAgente:
        if candidato.value == clave:
            return candidato
    disponibles = ", ".join(m.value for m in ModoAgente)
    raise IDEOpencodePermisosError(f"Modo desconocido: «{valor}». Usa uno de: {disponibles}.")


# --------------------------------------------------------------------------- #
# Clasificación de acciones reales de opencode -- ver "Qué es irreversible"
# --------------------------------------------------------------------------- #


class ClaseAccion(StrEnum):
    """Qué tan arriesgada es una ``action`` real de ``PermissionV2Request``,
    de menos a más -- mismos cuatro nombres que ``ide_modos.ClaseHerramienta``
    a propósito (mismo vocabulario de riesgo), pero clasificando el
    vocabulario de ACCIONES de opencode, no los nombres de herramientas
    nativas de Edecán (son dominios distintos, ver el docstring del módulo)."""

    LECTURA = "lectura"
    EDICION = "edicion"
    COMANDO = "comando"
    PELIGROSA = "peligrosa"


# Nombres reales de PermissionConfig en /doc -- ver hallazgo 5 del docstring
# del módulo. No son nombres inventados: opencode ya los usa para decidir
# ask/allow/deny por defecto.
ACCIONES_LECTURA = frozenset({"read", "glob", "grep", "list", "lsp", "todowrite", "question"})
ACCIONES_EDICION = frozenset({"edit"})

# "task" (subagente con capacidad amplia), "external_directory" (sale del
# límite del workspace) y "skill" (puede internamente invocar bash/edit) se
# tratan como COMANDO -- no se han visto en vivo con este módulo (a
# diferencia de bash/edit/webfetch, que sí), así que es un juicio
# conservador documentado, no un hallazgo empírico: piden aprobación en
# Manual, se permiten en Auto, igual que un comando de shell.
ACCIONES_COMANDO = frozenset({"bash", "task", "external_directory", "skill"})

# "red" (webfetch/websearch, la palabra que usa el propio encargo) y
# doom_loop (opencode detectando que el agente puede estar dando vueltas) --
# ver "Qué es irreversible" en el docstring del módulo.
ACCIONES_PELIGROSAS_FIJAS = frozenset({"webfetch", "websearch", "doom_loop"})

# Lista CORTA y propia -- ver "Qué es irreversible" en el docstring del
# módulo para el porqué de cada patrón. Compilados una vez, insensibles a
# mayúsculas porque un comando real puede venir en cualquier caja. El caso
# "rm recursivo y forzado" NO está aquí -- tiene su propia función
# (``_es_rm_recursivo_forzado``) porque un regex único no cubre bien que las
# banderas puedan venir juntas (``rm -rf``) o separadas (``rm -r -f``,
# ``rm --recursive --force``).
_PATRONES_COMANDO_IRREVERSIBLE: tuple[re.Pattern[str], ...] = tuple(
    re.compile(patron, re.IGNORECASE)
    for patron in (
        r"\bgit\s+push\b",
        r"\bgit\s+reset\s+--hard\b",
        r"\bgit\s+clean\s+-[a-z]*f",
        r"\bdrop\s+(table|database|schema)\b",
        r"\btruncate\s+table\b",
        r"\bdd\s+if=",
        r"\bmkfs\b",
        r"\b(shutdown|reboot|halt)\b",
        r"\bsudo\b",
        r"\bchmod\s+-R\s+777\b",
        r"\b(curl|wget)\b[^|]*\|\s*(sh|bash|zsh)\b",
        r"\bnpm\s+publish\b",
        r"\bdocker\s+system\s+prune\b",
        r"\bkill\s+-9\b",
        r">\s*/dev/(sd|nvme|disk|hd)",
    )
)


def _es_rm_recursivo_forzado(texto: str) -> bool:
    """Heurística corta y propia: ¿hay un ``rm`` con las banderas recursiva
    (``-r``/``-R``/``--recursive``) Y forzada (``-f``/``--force``) en el
    MISMO tramo de comando? Cubre tanto banderas combinadas (``rm -rf``,
    ``rm -Rf``) como separadas (``rm -r -f``, ``rm --recursive --force``) --
    un solo regex no cubre bien ambos casos a la vez. ``rm`` sin ambas
    banderas (p. ej. borrar un único archivo con ``rm archivo.txt``) NO
    cuenta: eso lo cubre la clase ``COMANDO`` normal (pide aprobación en
    Manual, se permite en Auto), no ``PELIGROSA``."""

    for tramo in re.split(r"[;&|\n]", texto):
        if not re.search(r"\brm\b", tramo):
            continue
        tiene_recursivo = bool(re.search(r"(-[a-zA-Z]*[rR][a-zA-Z]*\b|--recursive\b)", tramo))
        tiene_forzado = bool(re.search(r"(-[a-zA-Z]*f[a-zA-Z]*\b|--force\b)", tramo))
        if tiene_recursivo and tiene_forzado:
            return True
    return False


def clasificar_accion(accion: str, recursos: Sequence[str] = ()) -> ClaseAccion:
    """Clasifica una ``action`` real de opencode.

    Regla de seguridad (falla cerrado, igual que ``ide_modos.clasificar_
    herramienta``): una ``accion`` que no está en ninguno de los conjuntos de
    arriba -- una acción nueva que opencode agregue mañana, o un nombre de
    herramienta MCP/custom que nadie clasificó aquí -- cae en ``PELIGROSA``,
    la más restrictiva. Nunca cae en ``LECTURA`` en silencio.
    """

    if accion in ACCIONES_PELIGROSAS_FIJAS:
        return ClaseAccion.PELIGROSA
    if accion == "bash":
        texto = " ".join(recursos)
        if _es_rm_recursivo_forzado(texto) or any(
            patron.search(texto) for patron in _PATRONES_COMANDO_IRREVERSIBLE
        ):
            return ClaseAccion.PELIGROSA
        return ClaseAccion.COMANDO
    if accion in ACCIONES_COMANDO:
        return ClaseAccion.COMANDO
    if accion in ACCIONES_EDICION:
        return ClaseAccion.EDICION
    if accion in ACCIONES_LECTURA:
        return ClaseAccion.LECTURA
    return ClaseAccion.PELIGROSA


Decision = Literal["permitir", "pedir_aprobacion", "bloquear"]

# El modo por defecto de una sesión que nunca lo fijó explícitamente -- el
# más conservador, igual que ``ide_modos.MODO_POR_DEFECTO``.
MODO_POR_DEFECTO = ModoAgente.MANUAL

# La tabla de decisión: 4 modos x 4 clases = 16 casos explícitos, ninguno
# derivado de otro -- para que se pueda leer y auditar de un vistazo. Mismos
# valores que la tabla de ``ide_modos.py`` (incluida ahí por su propio
# docstring, punto 4): se redefine aquí en vez de importarla porque este
# archivo clasifica ACCIONES de opencode, no herramientas nativas de Edecán
# -- son tablas del mismo TAMAÑO y espíritu, pero sobre datos de entrada
# distintos, y ``ide_modos.py`` está bajo edición de otro workflow ahora
# mismo (ver la regla del encargo sobre no tocarlo).
#
#                        lectura      edicion            comando            peligrosa
# manual              -> permitir     pedir_aprobacion   pedir_aprobacion   pedir_aprobacion
# aceptar_ediciones   -> permitir     permitir            pedir_aprobacion   pedir_aprobacion
# plan                -> permitir     bloquear            bloquear           pedir_aprobacion
# auto                -> permitir     permitir            permitir           pedir_aprobacion
_TABLA_DECISION: dict[tuple[ModoAgente, ClaseAccion], Decision] = {
    (ModoAgente.MANUAL, ClaseAccion.LECTURA): "permitir",
    (ModoAgente.MANUAL, ClaseAccion.EDICION): "pedir_aprobacion",
    (ModoAgente.MANUAL, ClaseAccion.COMANDO): "pedir_aprobacion",
    (ModoAgente.MANUAL, ClaseAccion.PELIGROSA): "pedir_aprobacion",
    (ModoAgente.ACEPTAR_EDICIONES, ClaseAccion.LECTURA): "permitir",
    (ModoAgente.ACEPTAR_EDICIONES, ClaseAccion.EDICION): "permitir",
    (ModoAgente.ACEPTAR_EDICIONES, ClaseAccion.COMANDO): "pedir_aprobacion",
    (ModoAgente.ACEPTAR_EDICIONES, ClaseAccion.PELIGROSA): "pedir_aprobacion",
    (ModoAgente.PLAN, ClaseAccion.LECTURA): "permitir",
    (ModoAgente.PLAN, ClaseAccion.EDICION): "bloquear",
    (ModoAgente.PLAN, ClaseAccion.COMANDO): "bloquear",
    (ModoAgente.PLAN, ClaseAccion.PELIGROSA): "pedir_aprobacion",
    (ModoAgente.AUTO, ClaseAccion.LECTURA): "permitir",
    (ModoAgente.AUTO, ClaseAccion.EDICION): "permitir",
    (ModoAgente.AUTO, ClaseAccion.COMANDO): "permitir",
    (ModoAgente.AUTO, ClaseAccion.PELIGROSA): "pedir_aprobacion",
}
assert len(_TABLA_DECISION) == len(ModoAgente) * len(ClaseAccion)


def decidir(modo: ModoAgente, clase: ClaseAccion) -> Decision:
    """Función pura: dado el modo vigente y la clase de la acción, ¿qué
    corresponde? Ver ``_TABLA_DECISION`` para la tabla completa y el porqué
    de cada fila."""

    return _TABLA_DECISION[(modo, clase)]


# --------------------------------------------------------------------------- #
# Modelos Pydantic -- formas leídas de /doc y confirmadas en vivo (ver
# hallazgos del docstring del módulo)
# --------------------------------------------------------------------------- #


class FuenteHerramienta(BaseModel):
    """``PermissionV2Source`` / ``QuestionV2Tool`` -- de qué llamada de
    herramienta vino la solicitud."""

    model_config = ConfigDict(extra="allow")

    messageID: str
    callID: str


class SolicitudPermiso(BaseModel):
    """``PermissionV2Request`` -- confirmada en vivo con ``edit``, ``bash`` y
    ``webfetch`` reales (ver hallazgo 5 del docstring del módulo)."""

    model_config = ConfigDict(extra="allow")

    id: str
    sessionID: str
    action: str
    resources: list[str]
    save: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] | None = None
    source: FuenteHerramienta | None = None


class OpcionPregunta(BaseModel):
    """``QuestionV2Option`` -- una opción de respuesta."""

    model_config = ConfigDict(extra="allow")

    label: str
    description: str


class InfoPregunta(BaseModel):
    """``QuestionV2Info`` -- una pregunta concreta dentro de una solicitud."""

    model_config = ConfigDict(extra="allow")

    question: str
    header: str
    options: list[OpcionPregunta]
    multiple: bool | None = None
    custom: bool | None = None


class SolicitudPregunta(BaseModel):
    """``QuestionV2Request`` -- confirmada en vivo: el agente puede preguntar
    de verdad y este módulo puede leer la pregunta y responderla."""

    model_config = ConfigDict(extra="allow")

    id: str
    sessionID: str
    questions: list[InfoPregunta]
    tool: FuenteHerramienta | None = None


class EventoBus(BaseModel):
    """Un evento crudo de ``GET /api/event`` (esquema ``V2Event``) -- ver
    hallazgo 2 del docstring del módulo. Distinto de
    ``ide_opencode.EventoSesion``: ese envuelve el stream POR SESIÓN
    (``SessionDurableEvent``, que nunca trae permisos/preguntas); este
    envuelve el bus GLOBAL, que sí los trae."""

    model_config = ConfigDict(extra="allow")

    id: str
    type: str
    data: dict[str, Any] = Field(default_factory=dict)
    location: dict[str, Any] | None = None


@dataclass(slots=True, frozen=True)
class ResultadoPermiso:
    """Qué pasó con una solicitud de permiso: cómo se clasificó, qué decidió
    la tabla de modos, y si este puente ya respondió o la dejó pendiente para
    una persona."""

    solicitud: SolicitudPermiso
    clase: ClaseAccion
    decision: Decision
    respondido: bool
    respuesta: Literal["once", "always", "reject"] | None
    motivo: str | None = None


@dataclass(slots=True, frozen=True)
class EventoPermisoOPreguntaExterno:
    """Un ``*.replied``/``*.rejected`` que llegó por el bus global -- puede
    venir de este mismo puente (acabamos de responder) o de otra pieza
    (p. ej. alguien respondiendo directo contra la API). Informativo: sirve
    para que quien escuche ``escuchar()`` mantenga su propio estado de
    "pendientes" sincronizado sin tener que volver a pedir la lista."""

    tipo: Literal["permission.v2.replied", "question.v2.replied", "question.v2.rejected"]
    session_id: str
    request_id: str
    datos: dict[str, Any]


# --------------------------------------------------------------------------- #
# El puente
# --------------------------------------------------------------------------- #


class PuenteDePermisos:
    """Conecta la tabla de decisión de los cuatro modos con la API de
    permisos y preguntas real de opencode.

    Se construye sobre un :class:`ServidorOpencode` ya arrancado (de
    ``ide_opencode.py`` -- no se reescribe, se usa) y arma su propio cliente
    HTTP contra ``servidor.base_url`` para el resto de la superficie
    (permisos/preguntas), que ese módulo no cubre. ``obtener_modo`` es una
    función síncrona ``session_id -> ModoAgente | str`` que quien cablee este
    puente provee -- normalmente un ``ModoAgenteStore.obtener`` de
    ``ide_modos.py``, pero este módulo no lo importa (ver el docstring del
    módulo), así que basta con pasar el ``.value`` o el propio callable.
    """

    def __init__(
        self,
        servidor: ServidorOpencode,
        *,
        obtener_modo: Callable[[str], ModoAgente | str],
        timeout_lectura_http: float = 60.0,
    ) -> None:
        self._servidor = servidor
        self._obtener_modo = obtener_modo
        self._cliente = httpx.AsyncClient(
            base_url=servidor.base_url,
            limits=httpx.Limits(max_keepalive_connections=0, max_connections=20),
            timeout=httpx.Timeout(connect=10.0, read=timeout_lectura_http, write=10.0, pool=10.0),
        )

    async def cerrar(self) -> None:
        await self._cliente.aclose()

    async def __aenter__(self) -> PuenteDePermisos:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.cerrar()

    # ----------------------------------------------------------------- #
    # HTTP interno -- mismo criterio que ide_opencode._solicitar: nunca se
    # traga un error, siempre se propaga con el mensaje real de opencode.
    # ----------------------------------------------------------------- #

    @staticmethod
    def _error_desde_cuerpo(
        status: int, cuerpo_bytes: bytes, metodo: str, ruta: str
    ) -> ErrorOpencode:
        mensaje = f"opencode devolvió {status} en {metodo} {ruta}"
        tag: str | None = None
        texto = cuerpo_bytes.decode("utf-8", errors="replace")
        try:
            cuerpo: Any = json.loads(texto) if texto else None
        except json.JSONDecodeError:
            cuerpo = texto
        if isinstance(cuerpo, dict):
            tag = cuerpo.get("_tag")
            mensaje_real = cuerpo.get("message")
            if mensaje_real:
                mensaje = (
                    f"{mensaje} ({tag}): {mensaje_real}" if tag else f"{mensaje}: {mensaje_real}"
                )
        elif isinstance(cuerpo, str) and cuerpo:
            mensaje = f"{mensaje}: {cuerpo[:500]}"
        return ErrorOpencode(mensaje, tag=tag, status=status, cuerpo=cuerpo)

    async def _get(self, ruta: str, *, params: dict[str, Any] | None = None) -> httpx.Response:
        intentos = 0
        while True:
            try:
                resp = await self._cliente.get(ruta, params=params)
                if resp.status_code >= 400:
                    raise self._error_desde_cuerpo(resp.status_code, resp.content, "GET", ruta)
                return resp
            except httpx.TimeoutException as exc:
                raise ErrorOpencode(f"opencode no respondió a tiempo en GET {ruta}: {exc}") from exc
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                intentos += 1
                if intentos <= 10:
                    await asyncio.sleep(0.3)
                    continue
                raise ErrorOpencode(
                    f"No se pudo conectar con opencode en GET {ruta}: {exc}"
                ) from exc
            except httpx.TransportError as exc:
                raise ErrorOpencode(f"Fallo de transporte en GET {ruta}: {exc}") from exc

    async def _post(self, ruta: str, *, json_body: dict[str, Any] | None = None) -> httpx.Response:
        intentos = 0
        while True:
            try:
                resp = await self._cliente.post(ruta, json=json_body)
                if resp.status_code >= 400:
                    raise self._error_desde_cuerpo(resp.status_code, resp.content, "POST", ruta)
                return resp
            except httpx.TimeoutException as exc:
                raise ErrorOpencode(
                    f"opencode no respondió a tiempo en POST {ruta}: {exc}"
                ) from exc
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                intentos += 1
                if intentos <= 10:
                    await asyncio.sleep(0.3)
                    continue
                raise ErrorOpencode(
                    f"No se pudo conectar con opencode en POST {ruta}: {exc}"
                ) from exc
            except httpx.TransportError as exc:
                raise ErrorOpencode(f"Fallo de transporte en POST {ruta}: {exc}") from exc

    # ----------------------------------------------------------------- #
    # Lectura de solicitudes pendientes
    # ----------------------------------------------------------------- #

    async def permisos_pendientes(self, session_id: str) -> list[SolicitudPermiso]:
        """``GET /session/{id}/permission`` -- lo pendiente de UNA sesión."""

        resp = await self._get(f"/session/{session_id}/permission")
        return [SolicitudPermiso.model_validate(x) for x in resp.json()["data"]]

    async def obtener_permiso(self, session_id: str, request_id: str) -> SolicitudPermiso:
        resp = await self._get(f"/session/{session_id}/permission/{request_id}")
        return SolicitudPermiso.model_validate(resp.json()["data"])

    async def permisos_pendientes_en(self, directorio: str) -> list[SolicitudPermiso]:
        """``GET /permission/request`` filtrado por ``directorio`` -- catch-up
        server-wide. Necesario porque ``escuchar()`` es un bus EN VIVO (ver
        hallazgo 3 del docstring del módulo): sin este método, una solicitud
        creada antes de que este puente empiece a escuchar se queda huérfana
        para siempre. El ``directorio`` DEBE ser el mismo ``location.directory``
        con el que se crearon las sesiones (ver hallazgo 4): sin coincidir
        exacto, opencode devuelve una lista vacía aunque haya solicitudes
        reales pendientes."""

        resp = await self._get("/permission/request", params={"location[directory]": directorio})
        return [SolicitudPermiso.model_validate(x) for x in resp.json()["data"]]

    async def preguntas_pendientes(self, session_id: str) -> list[SolicitudPregunta]:
        resp = await self._get(f"/session/{session_id}/question")
        return [SolicitudPregunta.model_validate(x) for x in resp.json()["data"]]

    async def preguntas_pendientes_en(self, directorio: str) -> list[SolicitudPregunta]:
        """Equivalente de ``permisos_pendientes_en`` para preguntas (``GET
        /question/request``) -- mismo motivo, mismo requisito de directorio."""

        resp = await self._get("/question/request", params={"location[directory]": directorio})
        return [SolicitudPregunta.model_validate(x) for x in resp.json()["data"]]

    # ----------------------------------------------------------------- #
    # La política de modos, aplicada
    # ----------------------------------------------------------------- #

    def clasificar_y_decidir(self, solicitud: SolicitudPermiso) -> tuple[ClaseAccion, Decision]:
        """Clasifica la solicitud y consulta la tabla de modos con el modo
        VIGENTE de esa sesión (vía ``obtener_modo``, nunca cacheado -- el
        modo puede haber cambiado entre una solicitud y la siguiente)."""

        modo = _parse_modo(self._obtener_modo(solicitud.sessionID))
        clase = clasificar_accion(solicitud.action, solicitud.resources)
        return clase, decidir(modo, clase)

    async def _responder_permiso(
        self,
        session_id: str,
        request_id: str,
        respuesta: Literal["once", "always", "reject"],
        *,
        mensaje: str | None = None,
    ) -> None:
        cuerpo: dict[str, Any] = {"reply": respuesta}
        if mensaje is not None:
            cuerpo["message"] = mensaje
        await self._post(f"/session/{session_id}/permission/{request_id}/reply", json_body=cuerpo)

    async def procesar_solicitud(self, solicitud: SolicitudPermiso) -> ResultadoPermiso:
        """El corazón del puente: clasifica, decide según el modo vigente, y
        actúa para ``permitir``/``bloquear`` (el modelo no espera a una
        persona en esos dos casos -- ver hallazgo 6 del docstring del módulo
        sobre por qué esto SIEMPRE responde ``once``/``reject``, nunca
        ``always``). ``pedir_aprobacion`` NO se responde aquí: queda
        pendiente para que una persona real llame a
        :meth:`responder_permiso`."""

        clase, decision = self.clasificar_y_decidir(solicitud)
        if decision == "permitir":
            await self._responder_permiso(solicitud.sessionID, solicitud.id, "once")
            return ResultadoPermiso(
                solicitud, clase, decision, True, "once", "el modo vigente lo permite"
            )
        if decision == "bloquear":
            mensaje = (
                f"Modo actual no permite '{solicitud.action}' ahora mismo "
                "(p. ej. modo plan: no se toca el workspace)."
            )
            await self._responder_permiso(
                solicitud.sessionID, solicitud.id, "reject", mensaje=mensaje
            )
            return ResultadoPermiso(solicitud, clase, decision, True, "reject", mensaje)
        # pedir_aprobacion: se deja pendiente a propósito.
        return ResultadoPermiso(
            solicitud, clase, decision, False, None, "esperando aprobación humana"
        )

    async def responder_permiso(
        self,
        session_id: str,
        request_id: str,
        *,
        conceder: bool,
        recordar: bool = False,
        mensaje: str | None = None,
    ) -> ResultadoPermiso:
        """La persona real responde una solicitud que quedó en
        ``pedir_aprobacion``. Vuelve a leer la solicitud (``GET
        .../permission/{id}``) para clasificarla con datos frescos antes de
        decidir si ``recordar`` procede -- nunca confía en una copia vieja
        para esta comprobación de seguridad.

        ``recordar=True`` se RECHAZA (``NoSePuedeRecordarError``, nunca se
        degrada en silencio) si la clase es ``PELIGROSA`` (regla 3 del
        encargo: nunca un sí recordado para algo irreversible) o si opencode
        mismo no ofrece esta solicitud como recordable (``save`` vacío)."""

        solicitud = await self.obtener_permiso(session_id, request_id)
        clase = clasificar_accion(solicitud.action, solicitud.resources)

        if not conceder:
            await self._responder_permiso(session_id, request_id, "reject", mensaje=mensaje)
            return ResultadoPermiso(solicitud, clase, "pedir_aprobacion", True, "reject", mensaje)

        respuesta: Literal["once", "always"] = "once"
        if recordar:
            if clase is ClaseAccion.PELIGROSA:
                raise NoSePuedeRecordarError(
                    f"'{solicitud.action}' sobre {solicitud.resources!r} es irreversible "
                    "(clase PELIGROSA); no se recuerda un sí para esto (regla 3 del encargo)."
                )
            if not solicitud.save:
                raise NoSePuedeRecordarError(
                    "opencode no marcó ningún recurso de esta solicitud como recordable "
                    f"(save=[] en la solicitud real, id={solicitud.id})."
                )
            respuesta = "always"

        await self._responder_permiso(session_id, request_id, respuesta, mensaje=mensaje)
        return ResultadoPermiso(solicitud, clase, "pedir_aprobacion", True, respuesta, mensaje)

    async def recuperar_pendientes(self, directorio: str) -> list[ResultadoPermiso]:
        """Catch-up al arrancar: procesa TODO lo que ya estaba pendiente en
        ``directorio`` antes de que este puente existiera (ver hallazgo 3 del
        docstring del módulo), con la MISMA política que el bus en vivo --
        una solicitud vieja de una sesión en modo ``auto`` se resuelve sola
        igual que si hubiera llegado ahora mismo."""

        pendientes = await self.permisos_pendientes_en(directorio)
        return [await self.procesar_solicitud(s) for s in pendientes]

    # ----------------------------------------------------------------- #
    # Preguntas del agente -- expuestas, nunca respondidas por este módulo
    # ----------------------------------------------------------------- #

    async def responder_pregunta(
        self, session_id: str, request_id: str, respuestas: list[list[str]]
    ) -> None:
        """``respuestas``: una lista por pregunta (en el mismo orden que
        ``SolicitudPregunta.questions``), cada una con las etiquetas
        (``OpcionPregunta.label``) elegidas -- confirmado en vivo con
        ``[["edecan"]]`` contra una pregunta real de un modelo real."""

        await self._post(
            f"/session/{session_id}/question/{request_id}/reply", json_body={"answers": respuestas}
        )

    async def rechazar_pregunta(self, session_id: str, request_id: str) -> None:
        await self._post(f"/session/{session_id}/question/{request_id}/reject")

    # ----------------------------------------------------------------- #
    # El bus global en vivo -- ver hallazgos 1-3 del docstring del módulo
    # ----------------------------------------------------------------- #

    async def escuchar(
        self, *, tiempo_maximo_sin_eventos: float | None = None
    ) -> AsyncIterator[ResultadoPermiso | SolicitudPregunta | EventoPermisoOPreguntaExterno]:
        """Suscribe a ``GET /event`` (bus global -- ``self._cliente`` ya tiene
        ``base_url`` con el prefijo ``/api``, así que esta ruta relativa
        apunta a ``/api/event``, NO al ``/event`` viejo del TUI).

        Filtra sólo los cinco tipos que importan aquí:

        - ``permission.v2.asked``: se clasifica y decide de inmediato (vía
          :meth:`procesar_solicitud`) y se produce el ``ResultadoPermiso``.
        - ``question.v2.asked``: se produce la ``SolicitudPregunta`` cruda --
          este módulo NUNCA la responde solo (el dueño quiere que la IA
          hable con él, no que este puente conteste en su nombre).
        - ``permission.v2.replied`` / ``question.v2.replied`` /
          ``question.v2.rejected``: informativos, se producen como
          :class:`EventoPermisoOPreguntaExterno` -- útiles para que quien
          escuche mantenga su propio estado de "pendientes" sincronizado.

        Cualquier otro tipo de evento del bus (texto, herramientas, etc.) se
        descarta en silencio -- este método sólo es la superficie de
        permisos/preguntas, no un sustituto de ``ide_opencode.eventos``.

        ES UN BUS EN VIVO -- ver hallazgo 3: llama a
        :meth:`recuperar_pendientes` antes de iterar si te importa lo que ya
        estaba pendiente cuando este puente arrancó.
        """

        try:
            async with self._cliente.stream(
                "GET",
                "/event",
                timeout=httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0),
            ) as resp:
                if resp.status_code >= 400:
                    cuerpo = await resp.aread()
                    raise self._error_desde_cuerpo(resp.status_code, cuerpo, "GET", "/event")
                lector = resp.aiter_lines()
                while True:
                    try:
                        if tiempo_maximo_sin_eventos is not None:
                            linea = await asyncio.wait_for(
                                lector.__anext__(), timeout=tiempo_maximo_sin_eventos
                            )
                        else:
                            linea = await lector.__anext__()
                    except StopAsyncIteration:
                        break
                    except TimeoutError as exc:
                        raise TimeoutError(
                            "Sin eventos del bus global de opencode en "
                            f"{tiempo_maximo_sin_eventos}s."
                        ) from exc
                    if not linea or linea.startswith(":") or not linea.startswith("data:"):
                        continue
                    crudo = linea[len("data:") :].strip()
                    if not crudo:
                        continue
                    try:
                        payload = json.loads(crudo)
                    except json.JSONDecodeError as exc:
                        raise ErrorOpencode(
                            f"Evento del bus global de opencode no es JSON válido: {crudo[:300]!r}"
                        ) from exc
                    evento = EventoBus.model_validate(payload)
                    resultado = await self._procesar_evento_bus(evento)
                    if resultado is not None:
                        yield resultado
        except httpx.TransportError as exc:
            raise ErrorOpencode(
                f"Se perdió la conexión con el bus de eventos de opencode: {exc}"
            ) from exc

    async def _procesar_evento_bus(
        self, evento: EventoBus
    ) -> ResultadoPermiso | SolicitudPregunta | EventoPermisoOPreguntaExterno | None:
        if evento.type == "permission.v2.asked":
            return await self.procesar_solicitud(SolicitudPermiso.model_validate(evento.data))
        if evento.type == "question.v2.asked":
            return SolicitudPregunta.model_validate(evento.data)
        if evento.type in ("permission.v2.replied", "question.v2.replied", "question.v2.rejected"):
            return EventoPermisoOPreguntaExterno(
                tipo=evento.type,  # type: ignore[arg-type]
                session_id=str(evento.data.get("sessionID", "")),
                request_id=str(evento.data.get("requestID", "")),
                datos=evento.data,
            )
        return None
