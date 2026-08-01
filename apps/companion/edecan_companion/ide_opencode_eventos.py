"""Traductor: eventos de opencode -> el vocabulario de eventos de la interfaz de Edecán.

# POR QUÉ existe este archivo

``ide_opencode.ServidorOpencode.eventos()`` entrega ``EventoSesion`` -- la forma NATIVA de
opencode (``session.next.tool.called``, ``session.next.text.ended``, ...). La interfaz de
Edecán no habla ese idioma: lee lo que ``ide_sessions.Session.append(tipo, texto,
presentation=...)`` escribe, con un vocabulario propio (``status``, ``tool``, ``file``,
``assistant_final``, ...) que ``apps/web/src/components/ide/AgentThread.tsx`` ya sabe pintar.
Sin este módulo, aunque todo lo demás se cablee (§4-5 de ``docs/opencode-motor.md``), el dueño
no ve NADA mientras el agente trabaja -- ese es el encargo completo, palabra por palabra.

Este módulo SOLO traduce. No arranca ``ServidorOpencode``, no escribe en ninguna sesión, no
importa ``ide_sessions.py`` -- deliberadamente, porque ese archivo (y ``ide_runtime.py``,
``ide_workers_agent.py``, ``ide_modos.py``) están bajo edición de otro workflow en paralelo
ahora mismo (misma regla que ya siguen ``ide_opencode.py``/``ide_opencode_permisos.py``). Quien
cablee esto después solo tiene que hacer, por cada evento del stream::

    traductor = TraductorDeTurno()
    async for evento in servidor.eventos(sesion_id):
        for traducido in traductor.traducir(evento):
            session.append(traducido.tipo, traducido.texto, stream=traducido.stream,
                            presentation=traducido.presentation)
    # cuando el stream se queda callado (TimeoutError de `eventos()`):
    cierre = traductor.cerrar_turno(permiso_pendiente=..., pregunta_pendiente=...)
    session.append(cierre.tipo, cierre.texto, stream=cierre.stream)

# De dónde salieron las 28 variantes -- nada inventado, todo verificado en vivo HOY

``ide_opencode.py`` documenta la unión ``SessionDurableEvent`` como "27" variantes citando
``/doc#/components/schemas/SessionDurableEvent``. Al escribir este módulo se arrancó un
``opencode serve`` real (mismo binario ``1.17.18`` de esta Mac) y se leyó ``GET /doc`` de
verdad: la unión trae **28** miembros, no 27 -- ``SessionNextSynthetic`` es el que el conteo
original no incluyó (o se agregó entre revisiones; el binario es el mismo ``1.17.18``, así que
lo más probable es un conteo manual que se quedó corto, no un cambio de versión). Se deja
constancia aquí en vez de forzar "27" a secas: regla 7 del encargo, "nada simulado, si algo no
se puede, se declara" -- y un conteo que no cuadra con lo que el propio servidor declara hoy
es exactamente ese caso. ``TIPOS_EVENTO_SESION`` abajo son los 28 nombres reales, uno por uno.

De esos 28, **20 se dispararon de verdad** contra Cloudflare Workers AI real (prompts reales,
cambios de modelo/agente reales, un ``revert`` real con un ``messageID`` real, un fallo de
proveedor real forzado con una API key inválida a propósito) y sus payloads exactos son el
material de ``test_ide_opencode_eventos.py``. Los 8 restantes
(``session.next.moved``, ``session.next.synthetic``, ``session.next.shell.started``,
``session.next.shell.ended``, ``session.next.tool.progress``, ``session.next.retried``,
``session.next.compaction.started``, ``session.next.compaction.ended``) NO se lograron disparar
en esta sesión de trabajo -- no por no haberlo intentado, sino porque cada uno necesita algo que
no es practicable aquí sin gastar tiempo/tokens desproporcionados o que ``ide_opencode.py`` ni
siquiera expone:

- ``moved``: el único endpoint que lo dispara es ``/experimental/control-plane/move-session``
  (control plane, fuera de la superficie ``/api/session`` que usa este cimiento).
- ``synthetic``: no hay ningún endpoint documentado en ``/doc`` que lo dispare a demanda; según
  su forma (``{sessionID, messageID, text}``, igual que ``context.updated``) es opencode
  inyectando SU PROPIO texto al contexto del modelo -- se disparará solo cuando el runtime
  interno de opencode decida que hace falta, no por una llamada nuestra.
- ``shell.started``/``shell.ended``: se probó en vivo contra ``POST /session/{id}/shell`` (la
  única ruta que los documenta) y el request SÍ respondió 200 con un mensaje real -- pero
  ningún evento ``shell.*`` llegó por ``GET /api/session/{id}/event`` en los ~10s siguientes.
  Esa ruta vive fuera del prefijo ``/api/`` que este cimiento usa exclusivamente (ver el
  docstring de ``ide_opencode.py``, punto 1), así que puede pertenecer a otro subsistema
  (``/pty/shells`` sugiere una sesión de shell interactiva persistente, no la ejecución
  puntual que probamos).
- ``tool.progress``: solo lo emiten herramientas de ejecución larga con salida incremental;
  ``bash``/``edit``/``write``/``read`` contra un workspace de prueba responden completos en
  menos de un segundo, sin progreso intermedio que capturar.
- ``retried``: opencode reintenta solo ante un fallo TRANSITORIO del proveedor (5xx, timeout de
  red) -- forzar un fallo de verdad (API key inválida) da ``step.failed`` directo, sin reintento,
  porque un 401 no es transitorio.
- ``compaction.*``: ``POST /session/{id}/compact`` devolvió ``503 ServiceUnavailableError:
  "Session compact is not available yet"`` en cada intento -- necesita cruzar un umbral real de
  tokens usados en la sesión que no es razonable alcanzar solo para este módulo.

Para esos 8, la forma de ``data`` que usa este módulo sale de ``/doc`` (leído del MISMO servidor
real, no copiado de memoria ni de documentación vieja) -- estructuralmente real, aunque el
contenido de ejemplo en los tests esté marcado explícitamente como "derivado del esquema, no
capturado". Nunca se presenta uno como el otro.

# Qué se descarta a propósito (``TIPOS_DESCARTADOS``) y por qué

Siete variantes no producen NINGÚN evento traducido -- no por omisión, sino porque traducirlas
sería ruido o, peor, una fuga de contenido interno que no es para la persona:

- ``prompt.admitted``: idéntico en contenido a ``prompted`` (mismo ``sessionID``/``messageID``/
  ``prompt``/``delivery``, confirmado en captura real) -- es el paso interno "admitido" antes
  del paso interno "programado"; traducir ambos duplicaría el mismo anuncio dos veces.
- ``context.updated`` / ``synthetic``: capturados en vivo -- ``context.updated`` resultó ser
  opencode inyectando la LISTA COMPLETA DE SKILLS disponibles (miles de caracteres) en el
  contexto del modelo antes de un paso; es opencode hablándose a sí mismo, no a la persona.
  ``synthetic`` tiene la misma forma exacta (``sessionID``/``messageID``/``text``) y el mismo
  propósito documentado (texto que opencode sintetiza para SU PROPIO contexto) -- mismo criterio.
- ``text.started`` / ``tool.input.started`` / ``tool.input.ended`` / ``reasoning.started``: son
  marcadores de "esto está empezando a transmitirse" sin contenido -- el texto completo llega
  ~milisegundos después en ``text.ended``/``tool.called``/``reasoning.ended``. Traducir el
  marcador además del evento completo duplicaría el anuncio sin aportar nada nuevo.

# Herramientas de escritura -- misma lista que ``test_ide_opencode.py``

``_HERRAMIENTAS_ESCRITURA`` reusa exactamente el conjunto que ``test_ide_opencode.py`` ya
verificó en vivo como las herramientas de opencode que tocan disco (comentario de ese archivo:
"session.next.tool.success NO trae el nombre de la herramienta en su data ... el nombre vive en
el session.next.tool.called anterior con el MISMO callID"). Por eso ``TraductorDeTurno`` guarda
esa correlación por turno: sin ella, ``tool.success`` no tiene forma de saber si lo que acaba de
terminar fue una escritura o una lectura.

# El evento ``file`` -- formato exacto, no negociable

``ide_contexto.py`` (línea ~472) parsea ``file`` con el patrón exacto
``^Archivo actualizado: (?P<ruta>.+)$`` para reconstruir qué archivos tocó una tarea (usado por
``/btw``, ``/export`` y el estimador de costos de ``ide_costos.py``). Este módulo reproduce ese
formato AL CARÁCTER -- no es un capricho de estilo, es el contrato que ya existe.

# El evento ``error`` -- una honestidad que el motor viejo nunca tuvo

Se comprobó leyendo ``ide_workers_agent.py``/``ide_sessions.py`` completos: NINGÚN camino del
motor viejo escribe jamás un evento de tipo ``error`` -- un fallo de herramienta se traga en un
``{"ok": False, "error": ...}`` que solo el MODELO lee (nunca la persona, salvo que el modelo
decida mencionarlo). El frontend (``AgentThread.tsx``: ``EVENT_LABELS["error"]``, el cálculo de
``hasError``) ya está preparado para este tipo -- simplemente nunca lo recibió. Este módulo, al
traducir ``session.next.tool.failed``/``session.next.step.failed`` a ``error`` con el mensaje
REAL de opencode, es la primera vez que ese contrato se usa de verdad. Es exactamente lo
contrario del bug que esta migración entera existe para matar ("dice que hizo algo y no lo
hizo") -- ahora, cuando algo falla, la persona lo ve con el motivo real, no con silencio.

# El cierre del turno (punto 4 del encargo) -- por qué es un método aparte, con estado

opencode no manda ningún evento de "el turno terminó": el último evento real es
``session.next.step.ended`` y después el stream se queda callado (confirmado en TODAS las
capturas de esta ronda: nunca hay un evento posterior a ``step.ended`` cuando el turno de verdad
terminó). Que el stream se calle es ambiguo a propósito -- puede significar "terminó de verdad",
"está esperando que apruebes un permiso" (bus GLOBAL, ``permission.v2.asked``, invisible en este
stream -- ver el docstring de ``ide_opencode_permisos.py``) o "está esperando que respondas una
pregunta" (``question.v2.asked``, mismo bus). Por eso ``cerrar_turno()`` es un método aparte que
quien integra llama DESPUÉS de que ``servidor.eventos()`` lance ``TimeoutError`` (o el stream
termine), pasándole lo que averiguó por el otro canal (``PuenteDePermisos.permisos_pendientes``/
``preguntas_pendientes``). Nunca dice el genérico "este turno no dejó respuesta" que ya engañó
al dueño haciéndole creer que el sistema estaba caído cuando en realidad lo esperaba a él (ver
la advertencia del encargo) -- siempre nombra qué espera, o entrega la respuesta real si la hay,
o -- solo como último recurso, y diciéndolo explícitamente -- admite que no sabe en qué se quedó.

# ``question.v2.*`` -- "que la IA me hable" (punto 5 del encargo)

``question.v2.asked`` NO viaja por ``EventoSesion`` (confirmado por el propio
``ide_opencode_permisos.py``, hallazgo 1 de su docstring: ese stream nunca trae
``permission.v2.*`` ni ``question.v2.*``) -- viaja por el bus global que
``PuenteDePermisos.escuchar()`` ya expone como ``SolicitudPregunta`` tipada. Por eso
``traducir_pregunta()`` es una función aparte (no un caso más de ``TraductorDeTurno.traducir``):
recibe la ``SolicitudPregunta`` ya parseada y produce el evento nuevo ``agent_question`` con
la pregunta completa en JSON (mismo patrón que ``plan_proposed``/``confirmation_required``/
``mcp_confirmation``, que ya usan JSON-en-texto para cargar una estructura sin inventar un canal
nuevo). Deliberadamente NO se traducen aquí ``question.v2.replied``/``question.v2.rejected``
-- son ecos informativos del bus (``ide_opencode_permisos.EventoPermisoOPreguntaExterno``) que
no forman parte de "la IA me habla" (eso es la pregunta, no su resolución) ni de las 28
variantes de ``EventoSesion`` que es el encargo central de este módulo; quien cablee el hilo
completo decide si también los refleja.

# ``permission.v2.*`` en ``pedir_aprobacion`` -- cierre de un fallo real de verificación

Ronda anterior: cuando ``PuenteDePermisos.clasificar_y_decidir`` devolvía ``pedir_aprobacion``
(clase ``PELIGROSA`` -- ``webfetch``/``websearch``/un ``bash`` irreversible/etc.),
``ide_sessions._consumir_turno_opencode`` la RECHAZABA sola con un mensaje fijo, porque "esta
ronda no tiene todavía una acción para aprobar permisos a mitad de turno". Un verificador lo
probó en vivo (modo Manual, una edición real) y lo marcó ``no_cumple``: el turno no se colgaba
mudo (eso sí funcionaba), pero la única forma de progresar era abandonar el turno bloqueado y
mandar uno NUEVO en modo Auto -- no conceder el permiso pedido y que el MISMO turno continuara,
que es lo que el encargo pide.

Esta función es la mitad "avisar" de la corrección: igual que ``traducir_pregunta()`` para
``question.v2.asked``, ``traducir_permiso()`` recibe la ``SolicitudPermiso`` ya tipada que
``PuenteDePermisos`` expone y produce un evento ``agent_permission`` con la solicitud completa en
JSON -- mismo patrón, mismo canal (JSON-en-texto), ninguna tubería nueva. La mitad "conceder" es
``SessionManager.responder_permiso_agente`` (``ide_sessions.py``), que llama
``PuenteDePermisos.responder_permiso`` -- ese método YA EXISTÍA en ``ide_opencode_permisos.py``
desde la ronda anterior (nadie lo tocó); lo que faltaba era la acción de ``IDE_ACTIONS`` que lo
expusiera y el bucle de ``_consumir_turno_opencode`` que, en vez de rechazar sola, esperara de
verdad -- igual que ya hacía con las preguntas.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from edecan_companion.ide_opencode import EventoSesion
from edecan_companion.ide_opencode_permisos import SolicitudPermiso, SolicitudPregunta

# --------------------------------------------------------------------------- #
# Los 28 nombres reales de SessionDurableEvent -- ver "De dónde salieron las
# 28 variantes" en el docstring del módulo. Cada uno tiene una rama explícita
# en TraductorDeTurno.traducir; ninguno cae en el fallback silenciosamente.
# --------------------------------------------------------------------------- #

TIPOS_EVENTO_SESION: frozenset[str] = frozenset(
    {
        "session.next.agent.switched",
        "session.next.model.switched",
        "session.next.moved",
        "session.next.prompted",
        "session.next.prompt.admitted",
        "session.next.context.updated",
        "session.next.synthetic",
        "session.next.shell.started",
        "session.next.shell.ended",
        "session.next.step.started",
        "session.next.step.ended",
        "session.next.step.failed",
        "session.next.text.started",
        "session.next.text.ended",
        "session.next.tool.input.started",
        "session.next.tool.input.ended",
        "session.next.tool.called",
        "session.next.tool.progress",
        "session.next.tool.success",
        "session.next.tool.failed",
        "session.next.reasoning.started",
        "session.next.reasoning.ended",
        "session.next.retried",
        "session.next.compaction.started",
        "session.next.compaction.ended",
        "session.next.revert.staged",
        "session.next.revert.cleared",
        "session.next.revert.committed",
    }
)

# Ver "Qué se descarta a propósito" en el docstring del módulo para el porqué
# de cada uno -- ninguno es un olvido, cada uno tiene una razón documentada.
TIPOS_DESCARTADOS: frozenset[str] = frozenset(
    {
        "session.next.prompt.admitted",
        "session.next.context.updated",
        "session.next.synthetic",
        "session.next.text.started",
        "session.next.tool.input.started",
        "session.next.tool.input.ended",
        "session.next.reasoning.started",
    }
)

# Ver "Herramientas de escritura" en el docstring del módulo -- mismo
# conjunto que test_ide_opencode.py ya verificó en vivo.
_HERRAMIENTAS_ESCRITURA: frozenset[str] = frozenset({"write", "edit", "patch", "apply_patch"})

# El tipo de evento nuevo para question.v2.asked -- ver "question.v2.*" en el
# docstring del módulo.
TIPO_PREGUNTA_AGENTE = "agent_question"

# El tipo de evento nuevo para permission.v2.asked que quedó en
# "pedir_aprobacion" -- ver "permission.v2.* en pedir_aprobacion" más abajo en
# el docstring del módulo (agregado en la ronda que cierra el fallo "no existe
# ninguna acción para conceder un permiso a mitad de turno").
TIPO_PERMISO_AGENTE = "agent_permission"


@dataclass(slots=True, frozen=True)
class EventoTraducido:
    """Un evento ya en el idioma de Edecán, listo para ``Session.append(tipo, texto,
    stream=stream, presentation=presentation)`` -- mismos nombres de parámetro a propósito,
    para que cablear esto sea un paso mecánico."""

    tipo: str
    texto: str
    # Mismo criterio que ide_sessions.Session.append/write_event: un evento "error" viaja
    # como stderr -- se decide aquí, no se deja para que cada integrador lo repita.
    stream: str | None = None
    presentation: list[dict[str, Any]] | None = None


# --------------------------------------------------------------------------- #
# Extracción de campos -- siempre con .get()/isinstance, nunca asumiendo que
# un campo opcional está: EventoSesion.data es un dict suelto a propósito
# (ver el docstring de ide_opencode.py, "Qué eventos llegan tipados").
# --------------------------------------------------------------------------- #


def _modelo_texto(modelo: Any) -> str:
    if not isinstance(modelo, dict):
        return "modelo desconocido"
    proveedor = modelo.get("providerID") or "?"
    id_modelo = modelo.get("id") or "?"
    return f"{proveedor}/{id_modelo}"


def _error_texto(data: dict[str, Any]) -> str:
    """``SessionErrorUnknown`` real: ``{"type": "unknown", "message": "..."}`` -- se usa el
    mensaje tal cual lo manda opencode (regla 6 de ide_opencode.py: nunca se inventa uno)."""

    error = data.get("error")
    if isinstance(error, dict):
        mensaje = error.get("message")
        if isinstance(mensaje, str) and mensaje:
            return mensaje
    return "opencode reportó un error sin mensaje legible."


def _ruta_de_entrada(entrada: Any) -> str | None:
    """``input.path`` es el campo real visto en ``write``/``edit`` (capturado en vivo);
    ``filePath`` queda como alternativa razonable para herramientas no observadas
    (``patch``/``apply_patch``) que podrían nombrarlo distinto -- si ninguna está, se
    devuelve ``None`` en vez de inventar una ruta."""

    if not isinstance(entrada, dict):
        return None
    for clave in ("path", "filePath"):
        valor = entrada.get(clave)
        if isinstance(valor, str) and valor:
            return valor
    return None


def _contenido_texto(content: Any) -> str:
    """``content`` de ``tool.success``/``tool.progress``: lista de partes ``{"type": "text",
    "text": "..."}`` (forma real vista en capturas de ``bash``) -- se unen las partes de
    texto en orden; cualquier otra forma de parte se ignora sin fallar."""

    if not isinstance(content, list):
        return ""
    partes = [
        item["text"]
        for item in content
        if isinstance(item, dict)
        and item.get("type") == "text"
        and isinstance(item.get("text"), str)
    ]
    return "\n".join(p for p in partes if p)


# --------------------------------------------------------------------------- #
# El traductor -- con estado, ver "El cierre del turno" en el docstring del
# módulo sobre por qué. Una instancia por TURNO (una llamada a enviar_prompt
# + su stream de eventos), nunca compartida entre turnos: la correlación
# callID->herramienta y el último texto/error son de ESTE turno.
# --------------------------------------------------------------------------- #


class TraductorDeTurno:
    def __init__(self) -> None:
        # callID -> (nombre de herramienta, ruta si la hay) -- puesto en
        # tool.called, leído en tool.success/tool.failed (ver "Herramientas
        # de escritura" arriba: tool.success/failed NUNCA traen el nombre).
        self._herramienta_por_call_id: dict[str, tuple[str, str | None]] = {}
        # Último texto de respuesta visto (text.ended) -- lo que cerrar_turno
        # promueve a assistant_final si el turno termina de verdad sin que
        # ninguna espera (permiso/pregunta) explique el silencio.
        self._ultimo_texto_final: str | None = None
        # Último error visto (tool.failed/step.failed) -- para que el cierre
        # del turno, si no hay ni texto final ni espera pendiente, al menos
        # recuerde qué fue lo último que falló en vez de encogerse de hombros
        # sin ninguna pista.
        self._ultimo_error: str | None = None

    def traducir(self, evento: EventoSesion) -> list[EventoTraducido]:  # noqa: C901 - dispatch plano a propósito
        """Traduce UN ``EventoSesion`` a cero o más ``EventoTraducido``, en el orden en que
        deben escribirse. Nunca lanza por un evento de forma inesperada -- un campo que
        falta se trata como ausente (ver los helpers de arriba), y un ``type`` que esta
        versión no reconoce (opencode agrega una variante 29 mañana) cae en un ``status``
        honesto que lo dice, no en una excepción que tumbe el turno completo."""

        tipo = evento.type
        data = evento.data

        if tipo in TIPOS_DESCARTADOS:
            return []

        if tipo == "session.next.agent.switched":
            agente = data.get("agent") or "?"
            return [EventoTraducido("status", f"Cambié de agente a «{agente}».")]

        if tipo == "session.next.model.switched":
            modelo = _modelo_texto(data.get("model"))
            return [EventoTraducido("status", f"Cambié de modelo a {modelo}.")]

        if tipo == "session.next.moved":
            location = data.get("location") if isinstance(data.get("location"), dict) else {}
            directorio = location.get("directory") or "?"
            texto = f"Cambié el directorio de trabajo a {directorio}."
            subdirectorio = data.get("subdirectory")
            if isinstance(subdirectorio, str) and subdirectorio:
                texto += f" (subdirectorio: {subdirectorio})"
            return [EventoTraducido("status", texto)]

        if tipo == "session.next.prompted":
            return [
                EventoTraducido("status", "opencode recibió el mensaje y va a empezar a trabajar.")
            ]

        if tipo == "session.next.shell.started":
            comando = data.get("command") or ""
            return [EventoTraducido("command", f"$ {comando}")]

        if tipo == "session.next.shell.ended":
            salida = data.get("output")
            if not isinstance(salida, str) or not salida:
                return []
            return [EventoTraducido("output", salida)]

        if tipo == "session.next.step.started":
            agente = data.get("agent") or "?"
            modelo = _modelo_texto(data.get("model"))
            return [EventoTraducido("status", f"Nuevo paso -- agente «{agente}», modelo {modelo}.")]

        if tipo == "session.next.step.ended":
            finish = data.get("finish") or "?"
            return [EventoTraducido("status", f"Paso terminado (motivo: {finish}).")]

        if tipo == "session.next.step.failed":
            texto = f"El paso del agente falló: {_error_texto(data)}"
            self._ultimo_error = texto
            return [EventoTraducido("error", texto, stream="stderr")]

        if tipo == "session.next.text.ended":
            texto = data.get("text")
            if not isinstance(texto, str) or not texto:
                return []
            self._ultimo_texto_final = texto
            return [EventoTraducido("progress", texto)]

        if tipo == "session.next.tool.called":
            call_id = data.get("callID")
            herramienta = str(data.get("tool") or "?")
            entrada = data.get("input")
            ruta = _ruta_de_entrada(entrada)
            if isinstance(call_id, str):
                self._herramienta_por_call_id[call_id] = (herramienta, ruta)
            eventos = [EventoTraducido("tool", f"Usando {herramienta}.")]
            # bash es a la vez una "herramienta" (mensaje genérico) y un
            # "comando" con su propia línea -- mismo criterio de dos eventos
            # que ya usa el motor viejo (ide_workers_agent._ejecutar_comando:
            # write_event("tool", ...) en el llamador + write_event("command",
            # argv) adentro). Ver "El evento file" y el docstring del módulo.
            if herramienta == "bash" and isinstance(entrada, dict):
                comando = entrada.get("command")
                if isinstance(comando, str) and comando:
                    eventos.append(EventoTraducido("command", f"$ {comando}"))
            return eventos

        if tipo == "session.next.tool.progress":
            texto = _contenido_texto(data.get("content"))
            if not texto:
                return []
            return [EventoTraducido("output", texto)]

        if tipo == "session.next.tool.success":
            call_id = data.get("callID")
            info = self._herramienta_por_call_id.get(str(call_id)) if call_id else None
            herramienta, ruta = info if info is not None else (None, None)
            eventos: list[EventoTraducido] = []
            if herramienta == "bash":
                salida = _contenido_texto(data.get("content"))
                if salida:
                    eventos.append(EventoTraducido("output", salida))
                return eventos
            if herramienta in _HERRAMIENTAS_ESCRITURA and ruta:
                # Formato EXACTO -- ver "El evento file" en el docstring del
                # módulo: ide_contexto._PATRON_ARCHIVO lo parsea al carácter.
                eventos.append(EventoTraducido("file", f"Archivo actualizado: {ruta}"))
            return eventos

        if tipo == "session.next.tool.failed":
            call_id = data.get("callID")
            info = self._herramienta_por_call_id.get(str(call_id)) if call_id else None
            herramienta = info[0] if info is not None else "(herramienta desconocida)"
            texto = f"Falló la herramienta «{herramienta}»: {_error_texto(data)}"
            self._ultimo_error = texto
            return [EventoTraducido("error", texto, stream="stderr")]

        if tipo == "session.next.reasoning.ended":
            texto = data.get("text")
            if not isinstance(texto, str) or not texto:
                return []
            return [EventoTraducido("status", f"Razonando: {texto}")]

        if tipo == "session.next.retried":
            error = data.get("error") if isinstance(data.get("error"), dict) else {}
            mensaje = error.get("message") or "?"
            intento = data.get("attempt")
            return [
                EventoTraducido(
                    "status",
                    f"Reintentando (intento {intento if intento is not None else '?'}) "
                    f"tras un error transitorio del proveedor: {mensaje}",
                )
            ]

        if tipo == "session.next.compaction.started":
            razon = data.get("reason") or "?"
            return [
                EventoTraducido("status", f"Compactando el historial de la conversación ({razon})…")
            ]

        if tipo == "session.next.compaction.ended":
            razon = data.get("reason") or "?"
            return [EventoTraducido("status", f"Historial compactado ({razon}).")]

        if tipo == "session.next.revert.staged":
            revert = data.get("revert") if isinstance(data.get("revert"), dict) else {}
            archivos = revert.get("files")
            n_archivos = len(archivos) if isinstance(archivos, list) else 0
            return [
                EventoTraducido(
                    "status",
                    f"opencode preparó una reversión a un punto anterior "
                    f"({n_archivos} archivo(s) afectado(s)) -- pendiente de confirmar o descartar.",
                )
            ]

        if tipo == "session.next.revert.cleared":
            return [EventoTraducido("status", "Se descartó la reversión pendiente; nada cambió.")]

        if tipo == "session.next.revert.committed":
            texto = "Se aplicó la reversión a un punto anterior del historial."
            return [EventoTraducido("status", texto)]

        # Variante NO reconocida (opencode agregó una nueva desde que se
        # escribió este módulo -- ya pasó una vez, ver "De dónde salieron las
        # 28 variantes" arriba). Nunca se descarta en silencio ni se lanza:
        # se declara, con los datos crudos truncados, para que quien lo vea
        # sepa que hay una variante sin traducir todavía.
        resumen = json.dumps(data, ensure_ascii=False, default=str)[:300]
        return [
            EventoTraducido(
                "status",
                f"[opencode mandó un evento que este traductor no reconoce: {tipo}] {resumen}",
            )
        ]

    def cerrar_turno(
        self,
        *,
        permiso_pendiente: SolicitudPermiso | None = None,
        pregunta_pendiente: SolicitudPregunta | None = None,
    ) -> EventoTraducido:
        """Se llama cuando ``servidor.eventos()`` se quedó callado (``TimeoutError``) o el
        stream terminó, para decidir qué mostrar en el hueco donde iría la respuesta del
        turno. Ver "El cierre del turno" en el docstring del módulo -- la regla dura es
        NUNCA el genérico "no dejó respuesta" cuando en realidad está esperando a la
        persona; siempre se nombra la espera concreta si se conoce."""

        if permiso_pendiente is not None:
            recursos = ", ".join(permiso_pendiente.resources) or "(sin recursos declarados)"
            return EventoTraducido(
                "status",
                f"El turno está esperando tu aprobación para «{permiso_pendiente.action}» "
                f"sobre {recursos}.",
            )
        if pregunta_pendiente is not None and pregunta_pendiente.questions:
            primera = pregunta_pendiente.questions[0].question
            resto = len(pregunta_pendiente.questions) - 1
            extra = f" (y {resto} pregunta(s) más esperando)" if resto > 0 else ""
            return EventoTraducido(
                "status", f"El turno está esperando que respondas: {primera}{extra}"
            )
        if self._ultimo_texto_final is not None:
            return EventoTraducido("assistant_final", self._ultimo_texto_final)
        if self._ultimo_error is not None:
            return EventoTraducido(
                "status",
                "El turno se quedó sin eventos nuevos y sin una respuesta de texto. "
                f"Último error visto en este turno: {self._ultimo_error}",
            )
        return EventoTraducido(
            "status",
            "El turno se quedó sin eventos nuevos, no dejó una respuesta de texto y no hay "
            "ningún permiso ni pregunta pendiente detectado -- revisa el registro de "
            "herramientas de este turno para ver en qué se quedó.",
        )


# --------------------------------------------------------------------------- #
# question.v2.asked -- ver "question.v2.*" en el docstring del módulo.
# --------------------------------------------------------------------------- #


def traducir_permiso(solicitud: SolicitudPermiso) -> EventoTraducido:
    """``permission.v2.asked`` que quedó en ``pedir_aprobacion`` -> ``agent_permission``: el
    turno está esperando que la persona conceda o rechace una acción real de opencode (encargo
    punto 4/5: nunca un cuelgue mudo, y ahora con una acción real para resolverlo, ver
    "``permission.v2.*`` en ``pedir_aprobacion``" en el docstring del módulo). ``solicitud`` es
    la ``SolicitudPermiso`` ya tipada que expone ``PuenteDePermisos`` -- este módulo no vuelve a
    parsear el bus global, reusa el trabajo que ese módulo ya hizo (mismo criterio que
    ``traducir_pregunta``)."""

    payload: dict[str, Any] = {
        "request_id": solicitud.id,
        "session_id": solicitud.sessionID,
        "action": solicitud.action,
        "resources": list(solicitud.resources),
        # ``save`` no vacío es la señal real de opencode de que ESTA solicitud admite
        # "recordar" (ver hallazgo 6 de ide_opencode_permisos.py) -- se expone tal cual para que
        # quien pinte la tarjeta sepa si ofrecer la opción de recordar o no.
        "puede_recordar": bool(solicitud.save),
    }
    if solicitud.metadata:
        payload["metadata"] = solicitud.metadata
    if solicitud.source is not None:
        payload["tool"] = {
            "message_id": solicitud.source.messageID,
            "call_id": solicitud.source.callID,
        }
    return EventoTraducido(TIPO_PERMISO_AGENTE, json.dumps(payload, ensure_ascii=False))


def traducir_pregunta(solicitud: SolicitudPregunta) -> EventoTraducido:
    """``question.v2.asked`` -> ``agent_question``: el agente preguntándole algo a la
    persona (encargo: "que la IA me hable"). ``solicitud`` es la ``SolicitudPregunta`` ya
    tipada que expone ``PuenteDePermisos`` -- este módulo no vuelve a parsear el bus
    global, reusa el trabajo que ese módulo ya hizo."""

    payload: dict[str, Any] = {
        "request_id": solicitud.id,
        "session_id": solicitud.sessionID,
        "questions": [
            {
                "question": pregunta.question,
                "header": pregunta.header,
                "options": [
                    {"label": opcion.label, "description": opcion.description}
                    for opcion in pregunta.options
                ],
                "multiple": pregunta.multiple,
                "custom": pregunta.custom,
            }
            for pregunta in solicitud.questions
        ],
    }
    if solicitud.tool is not None:
        payload["tool"] = {
            "message_id": solicitud.tool.messageID,
            "call_id": solicitud.tool.callID,
        }
    return EventoTraducido(TIPO_PREGUNTA_AGENTE, json.dumps(payload, ensure_ascii=False))
