"""Registro central de comandos "/" del IDE (Forge Studio) + su parser.

Casi ninguno de estos comandos es funcionalidad nueva. Cada uno es un atajo de
teclado a algo que YA existe en otro módulo -- el mismo patrón que usa una CLI
de agente conocida, donde ``/rewind`` no reimplementa checkpoints, solo llama
al motor de checkpoints. Este archivo es exclusivamente:

1. ``ComandoIDE``: la declaración de un comando (nombre, alias, descripción
   de una línea, si toma argumentos, si es destructivo, y a qué capacidad ya
   existente llama). El campo ``capacidad`` es documentación -- un puntero de
   texto al módulo/función real (p. ej. ``"ide_checkpoints.CheckpointStore"``)
   -- no una llamada en vivo: este módulo no importa ``ide_checkpoints``,
   ``ide_plan``, etc. Quien cablee cada comando a su ejecución real (siguiente
   fase, probablemente en ``ide_runtime.py``) lee ese campo para saber adónde
   ir; este registro no depende de esos módulos ni se rompe si cambian de
   forma.
2. ``resolver_comando``: convierte lo que la persona escribió ("/rewind abc")
   en un ``ComandoResuelto`` (o en un ``IDEComandoError`` con una sugerencia
   si el nombre no existe).
3. ``autocompletar``: dado un prefijo ("/re"), qué nombres se pueden escribir
   a continuación -- para el menú de la UI.
4. ``texto_ayuda``: el texto de ``/help``, generado SIEMPRE desde el propio
   registro. No hay una lista escrita a mano en ningún lado: si mañana se
   agrega un comando aquí, ``/help`` lo muestra solo.

Por qué los alias son un solo ``ComandoIDE`` y no comandos separados: si
``/new``, ``/clear`` y ``/reset`` fueran tres registros distintos que además
apuntan al mismo ``capacidad``, nada impediría que se desincronizaran (uno se
marca destructivo y el otro no, por ejemplo) y ``/help`` los listaría como si
fueran tres acciones distintas cuando la persona solo puede pensar en una.
Cada ``ComandoIDE`` guarda su propio nombre canónico más una tupla de alias;
``_construir_indice`` es lo único que expande eso a un diccionario plano por
nombre, y lo hace una sola vez al importar el módulo (revienta con
``RuntimeError`` si dos comandos comparten un nombre -- eso es un bug de
registro, no una entrada inválida de la persona).

Fuera de este archivo a propósito: no se importa ``ide_runtime.py`` (otra
corrida está trabajando ahí en paralelo) ni ningún módulo de capacidad real.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Literal

ModoArgumentos = Literal["ninguno", "opcional", "obligatorio"]
"""Qué tan obligatorios son los argumentos, para que la UI decida si abre un
campo de texto y si puede enviarlo vacío.

- ``"ninguno"``: el comando no usa argumentos (si la persona escribe texto de
  más, se ignora al ejecutar -- este parser no lo rechaza).
- ``"opcional"``: puede ir con o sin argumento (p. ej. ``/resume`` retoma la
  última sesión sin argumento, o una puntual si se da su id).
- ``"obligatorio"``: falla con ``IDEComandoError`` si no viene texto después
  del nombre del comando.
"""


class IDEComandoError(ValueError):
    """Entrada de comando inválida: sin '/', comando desconocido, o falta un
    argumento obligatorio.

    ``sugerencia`` viene poblado (con la barra incluida, p. ej. ``"/clear"``)
    cuando el nombre desconocido se parece lo bastante a uno real; si no hay
    ningún candidato razonable queda en ``None`` y el llamador no debe
    inventarse una sugerencia.
    """

    def __init__(self, mensaje: str, *, sugerencia: str | None = None) -> None:
        super().__init__(mensaje)
        self.sugerencia = sugerencia


@dataclass(frozen=True)
class ComandoIDE:
    """Un comando "/" y todo lo que la UI necesita saber sobre él sin tener
    que ejecutarlo primero."""

    nombre: str
    descripcion: str
    capacidad: str
    argumentos: ModoArgumentos = "ninguno"
    alias: tuple[str, ...] = ()
    destructivo: bool = False

    @property
    def nombres(self) -> tuple[str, ...]:
        """Nombre canónico + alias, en ese orden (el canónico primero)."""
        return (self.nombre, *self.alias)


@dataclass(frozen=True)
class ComandoResuelto:
    """Resultado de parsear una línea de comando válida."""

    comando: ComandoIDE
    alias_usado: str
    argumentos: str | None


# ---------------------------------------------------------------------------
# El registro. Agregar un comando nuevo es agregar una línea aquí -- nada más
# se actualiza a mano (/help, autocompletado, etc. se derivan solos).
# ---------------------------------------------------------------------------

_COMANDOS: tuple[ComandoIDE, ...] = (
    ComandoIDE(
        nombre="help",
        descripcion="Lista todos los comandos disponibles.",
        capacidad="ide_comandos (este mismo registro; texto_ayuda())",
    ),
    ComandoIDE(
        nombre="clear",
        alias=("new", "reset"),
        descripcion="Borra la conversación actual y empieza una nueva.",
        capacidad="ide_projects.ProjectRegistry.create_conversation",
        destructivo=True,
    ),
    ComandoIDE(
        nombre="rename",
        descripcion="Cambia el título de la conversación actual.",
        capacidad="ide_projects.ProjectRegistry.rename_conversation",
        argumentos="obligatorio",
    ),
    ComandoIDE(
        nombre="branch",
        descripcion="Bifurca la conversación actual en una copia independiente.",
        capacidad="ide_projects.ProjectRegistry (nueva conversación a partir del historial)",
        argumentos="opcional",
    ),
    ComandoIDE(
        nombre="resume",
        descripcion="Retoma una sesión de agente anterior.",
        capacidad="ide_sessions.SessionManager (continuidad de sesión)",
        argumentos="opcional",
    ),
    ComandoIDE(
        nombre="model",
        descripcion="Muestra o cambia el modelo usado en el IDE.",
        capacidad="config/modelos.yml (modelos_ide)",
        argumentos="opcional",
    ),
    ComandoIDE(
        nombre="effort",
        descripcion="Ajusta el nivel de esfuerzo de razonamiento del modelo activo.",
        capacidad="config/modelos.yml (modelos_ide, razonamiento por perfil)",
        argumentos="opcional",
    ),
    ComandoIDE(
        nombre="plan",
        descripcion="Propone o muestra el plan de pasos para la tarea actual.",
        capacidad="ide_plan.PlanStore.propose",
        argumentos="opcional",
    ),
    ComandoIDE(
        nombre="context",
        descripcion="Muestra qué contexto se le está mandando al modelo ahora mismo.",
        capacidad="edecan_core.llm_call_log",
        argumentos="ninguno",
    ),
    ComandoIDE(
        nombre="cost",
        descripcion="Analiza el costo y la duración de la tarea actual.",
        capacidad="ide_costos.analizar_tarea",
    ),
    ComandoIDE(
        nombre="usage",
        descripcion="Muestra el consumo del plan contra sus límites.",
        capacidad="edecan_schemas.plans (limits.*)",
    ),
    ComandoIDE(
        nombre="memory",
        descripcion="Muestra o edita la memoria persistente del usuario.",
        capacidad="edecan_core.memory + apps/api routers/memory.py",
        argumentos="opcional",
    ),
    ComandoIDE(
        nombre="agents",
        descripcion="Lista los sub-agentes disponibles para repartir trabajo.",
        capacidad="ide_equipo.EquipoDeAgentes",
    ),
    ComandoIDE(
        nombre="batch",
        descripcion="Reparte la tarea actual entre varios sub-agentes en paralelo.",
        capacidad="ide_equipo.construir_plan / EquipoDeAgentes",
        argumentos="obligatorio",
    ),
    ComandoIDE(
        nombre="diff",
        descripcion="Muestra los cambios sin confirmar del repo actual.",
        capacidad="ide_git.GitService.diff",
        argumentos="opcional",
    ),
    ComandoIDE(
        nombre="rewind",
        descripcion="Vuelve el repo a un checkpoint anterior.",
        capacidad="ide_checkpoints.CheckpointStore",
        argumentos="obligatorio",
        destructivo=True,
    ),
    ComandoIDE(
        nombre="review",
        descripcion="Revisa el diff pendiente en busca de errores y mejoras.",
        capacidad="tool auditar_seguridad_proyecto",
        argumentos="opcional",
    ),
    ComandoIDE(
        nombre="security-review",
        descripcion="Audita el proyecto en busca de riesgos de seguridad.",
        capacidad="tool auditar_seguridad_proyecto",
        argumentos="opcional",
    ),
    ComandoIDE(
        nombre="mcp",
        descripcion="Lista y administra los servidores MCP conectados.",
        capacidad="packages/mcp (edecan_mcp.client / protocol)",
        argumentos="opcional",
    ),
    ComandoIDE(
        nombre="doctor",
        descripcion="Diagnostica el estado local del IDE y del workspace activo.",
        # No es la tool ``diagnosticar_autorreparacion_local``: esa exige un
        # fallo puntual ya ocurrido (``intencion_original``/``fallo_reportado``)
        # para recomendar una vía, y no existe eso cuando la persona escribe
        # "/doctor" sin haber visto un error antes. ``/doctor`` es en cambio
        # un chequeo local de solo lectura (IDE habilitado, workspace
        # legible/escribible, es repo Git) -- ver el docstring de
        # ``IDERuntime._salud_local`` en ``ide_runtime.py`` para el porqué
        # completo de esta decisión.
        capacidad="ide_runtime.IDERuntime._salud_local (chequeo local de solo lectura)",
    ),
    ComandoIDE(
        nombre="debug",
        descripcion="Muestra el registro crudo de llamadas al modelo.",
        capacidad="edecan_core.llm_call_log",
        argumentos="opcional",
    ),
    ComandoIDE(
        nombre="export",
        descripcion="Exporta la conversación actual a un archivo.",
        capacidad="ide_sessions.SessionManager (eventos ya persistidos de la sesión)",
        argumentos="opcional",
    ),
    ComandoIDE(
        nombre="copy",
        descripcion="Copia la última respuesta al portapapeles.",
        capacidad="UI local (portapapeles); no necesita motor de backend",
    ),
    ComandoIDE(
        nombre="init",
        descripcion="Crea el archivo de reglas del proyecto (AGENTS.md).",
        capacidad="ide_reglas.load_project_rules",
    ),
    ComandoIDE(
        nombre="compact",
        descripcion="Resume la conversación para liberar espacio de contexto.",
        capacidad="edecan_core.llm_call_log (recorte de contexto)",
    ),
    ComandoIDE(
        nombre="btw",
        descripcion="Agrega una nota lateral a la conversación sin disparar un turno del agente.",
        # NO es ``Session.append``: ``ide_contexto.preparar_contexto_btw`` es
        # explícito en que la garantía de "no entra al hilo principal"
        # exige que ni la pregunta ni la respuesta lleguen a ``Session.append``
        # -- la respuesta viaja solo en el resultado del comando (ver
        # ``IDERuntime._responder_btw`` en ``ide_runtime.py``), nunca en los
        # eventos persistidos de la sesión.
        capacidad=(
            "ide_contexto.preparar_contexto_btw + IDERuntime._responder_btw (llamada efímera)"
        ),
        argumentos="obligatorio",
    ),
    ComandoIDE(
        nombre="goal",
        descripcion="Define o muestra la meta del plan actual.",
        capacidad="ide_plan.Plan.goal",
        argumentos="opcional",
    ),
    ComandoIDE(
        nombre="simplify",
        descripcion="Pide una pasada de simplificación sobre el código recién cambiado.",
        capacidad="ide_equipo.EquipoDeAgentes (sub-agente revisor)",
        argumentos="opcional",
    ),
    ComandoIDE(
        nombre="background",
        descripcion="Manda la tarea actual a correr en segundo plano y libera el chat.",
        capacidad="ide_sessions.SessionManager (la sesión ya corre desacoplada del chat)",
    ),
    ComandoIDE(
        nombre="tasks",
        descripcion="Lista las subtareas de la tarea actual y su estado.",
        capacidad="ide_plan.PlanStep / ide_equipo.Subtarea",
    ),
    ComandoIDE(
        nombre="permissions",
        descripcion="Muestra o cambia qué acciones piden aprobación.",
        capacidad="approval.py (aprobaciones recordadas)",
        argumentos="opcional",
    ),
    ComandoIDE(
        nombre="config",
        alias=("settings",),
        descripcion="Muestra o cambia la configuración del companion.",
        capacidad="edecan_companion.config / edecan_api.config",
        argumentos="opcional",
    ),
    ComandoIDE(
        nombre="voice",
        descripcion="Prueba o configura la voz del asistente.",
        capacidad="packages/voice (sintetizar_voz)",
        argumentos="opcional",
    ),
    ComandoIDE(
        nombre="remote-control",
        alias=("rc",),
        descripcion="Activa el control remoto de este equipo.",
        capacidad="apps/api routers/remote.py + control remoto del companion",
        argumentos="opcional",
    ),
    ComandoIDE(
        nombre="workflows",
        descripcion="Lista los flujos de trabajo guardados.",
        capacidad="packages/skills (edecan_skills)",
        argumentos="opcional",
    ),
)


def _construir_indice(comandos: tuple[ComandoIDE, ...]) -> dict[str, ComandoIDE]:
    indice: dict[str, ComandoIDE] = {}
    for comando in comandos:
        for nombre in comando.nombres:
            if nombre in indice:
                # Bug de registro (no de la persona usando el IDE): dos
                # comandos declararon el mismo nombre/alias. Revienta fuerte
                # y temprano -- al importar el módulo -- en vez de dejar que
                # el segundo pise silenciosamente al primero en el índice.
                raise RuntimeError(
                    f"'{nombre}' está registrado en más de un comando "
                    f"('{indice[nombre].nombre}' y '{comando.nombre}')."
                )
            indice[nombre] = comando
    return indice


_INDICE: dict[str, ComandoIDE] = _construir_indice(_COMANDOS)


def listar_comandos() -> tuple[ComandoIDE, ...]:
    """Todos los comandos del registro, uno por comando (los alias no
    generan entradas repetidas)."""
    return _COMANDOS


def texto_ayuda() -> str:
    """El texto de ``/help``, generado siempre desde ``_COMANDOS``.

    Una línea por comando, con todos sus nombres juntos (p. ej.
    ``/clear = /new = /reset``) para que quede claro que es una sola acción.
    """
    lineas = []
    for comando in sorted(_COMANDOS, key=lambda c: c.nombre):
        nombres = " = ".join(f"/{nombre}" for nombre in comando.nombres)
        marca = " [destructivo]" if comando.destructivo else ""
        lineas.append(f"{nombres} -- {comando.descripcion}{marca}")
    return "\n".join(lineas)


def autocompletar(prefijo: str) -> list[str]:
    """Nombres (canónicos o alias, cada uno como entrada independiente) que
    empiezan por *prefijo*, en orden alfabético y con la barra incluida.

    Se acepta *prefijo* con o sin "/" inicial y sin distinguir mayúsculas.
    Cada coincidencia se devuelve tal como se escribiría (su propio nombre,
    no el canónico del comando al que resuelve) -- así "/rc" aparece junto a
    "/remote-control" para el prefijo "/r", no solo el segundo.
    """
    limpio = prefijo.strip()
    if limpio.startswith("/"):
        limpio = limpio[1:]
    limpio = limpio.casefold()
    coincidencias = {nombre for nombre in _INDICE if nombre.casefold().startswith(limpio)}
    return sorted(f"/{nombre}" for nombre in coincidencias)


def _sugerir(nombre_desconocido: str) -> str | None:
    coincidencias = difflib.get_close_matches(nombre_desconocido, _INDICE.keys(), n=1, cutoff=0.5)
    return coincidencias[0] if coincidencias else None


def resolver_comando(entrada: str) -> ComandoResuelto:
    """Convierte lo que la persona escribió en un ``ComandoResuelto``.

    Levanta ``IDEComandoError`` si:
    - la entrada no empieza con "/",
    - no hay nombre después de "/",
    - el nombre no existe en el registro (con ``.sugerencia`` si hay un
      candidato parecido),
    - o el comando exige argumentos y no vinieron.
    """
    texto = entrada.strip()
    if not texto.startswith("/"):
        raise IDEComandoError("Los comandos empiezan con '/'.")
    cuerpo = texto[1:]
    if not cuerpo:
        raise IDEComandoError("Escribe un nombre de comando después de '/'.")
    partes = cuerpo.split(maxsplit=1)
    nombre_escrito = partes[0]
    argumentos_crudos = partes[1].strip() if len(partes) > 1 else None
    if argumentos_crudos == "":
        argumentos_crudos = None
    comando = _INDICE.get(nombre_escrito.casefold())
    if comando is None:
        sugerencia = _sugerir(nombre_escrito.casefold())
        mensaje = f"No existe el comando '/{nombre_escrito}'."
        if sugerencia:
            mensaje += f" ¿Quisiste decir '/{sugerencia}'?"
        raise IDEComandoError(
            mensaje, sugerencia=f"/{sugerencia}" if sugerencia else None
        )
    if comando.argumentos == "obligatorio" and not argumentos_crudos:
        raise IDEComandoError(f"'/{comando.nombre}' necesita argumentos.")
    return ComandoResuelto(
        comando=comando, alias_usado=nombre_escrito.casefold(), argumentos=argumentos_crudos
    )
