"""Manejo de contexto del IDE: ``/context``, ``/compact`` y ``/btw``.

Los tres comandos son la misma idea con tres caras: el contexto que se le
manda al modelo es un recurso finito, y cada uno resuelve una pregunta
distinta sobre ese mismo recurso.

- ``/context`` -- ¿cuánto contexto lleva usado la conversación y en qué se
  está yendo?
- ``/compact`` -- resume la conversación para liberar espacio, sin perder lo
  que de verdad importa para seguir trabajando.
- ``/btw`` -- deja hacer una pregunta lateral que VE el contexto pero no lo
  ensucia (no se vuelve parte del hilo principal).

Este archivo, como ``ide_costos.py``/``ide_checkpoints.py``, es puro estado
y cálculo: no importa ``ide_sessions.py`` (otra corrida lo está tocando en
paralelo) ni ``ide_runtime.py``. Recibe datos ya recortados por quien
integre -- eventos de sesión (``ide_sessions.Session.events``, la misma
forma ``{"cursor", "type", "text", "timestamp", "stream"}`` que ya consume
``ide_costos.analizar_tarea``) y/o registros de la bitácora de llamadas al
modelo -- y devuelve estructuras listas para que la UI las pinte.

Parte A -- ``/context`` y ``edecan_core.llm_call_log``
--------------------------------------------------------
El encargo es explícito: "``llm_call_log.py`` ya registra tokens por
llamada: LÉELO y reúsalo en vez de contar por tu cuenta." Este módulo NO
recalcula tokens con la heurística de caracteres (la que sí usa
``ide_costos.py`` como respaldo cuando no hay nada mejor) -- usa los
``input_tokens``/``output_tokens`` reales que ese módulo ya escribió a
``llm-calls.jsonl``.

Lo que este módulo NO puede hacer es *importar* ``edecan_core.llm_call_log``:
``edecan_companion`` está construido a propósito sin esa dependencia (ver
``pyproject.toml`` de este paquete -- es el único paquete pensado para
instalarse solo, en la máquina del usuario, separado del servidor). La
solución es la misma que ya usa ``ide_costos.py`` con el formato de eventos
de ``ide_sessions.Session.append``: leer el MISMO esquema JSONL que
``log_llm_call`` ya escribe (documentado en su propio docstring), sin
importar ni una línea de código de ese paquete. Si ese esquema cambia
algún día, se actualiza el parseo de acá, no allá.

Dato real que hay que asumir con las dos manos: hoy ``log_llm_call`` solo lo
llama el agente de chat del *servidor* (``edecan_core.agent``), no el agente
local del IDE (``ide_workers_agent.WorkersIDEAgent``, que no registra
``usage`` de ningún tipo todavía). Este módulo funciona igual sin esa
integración -- ``leer_bitacora`` sobre un ``DATA_DIR`` sin ese archivo
devuelve una lista vacía y ``analizar_contexto`` lo reporta como advertencia
en vez de reventar -- pero el day-one real de ``/context`` para el IDE
depende de que alguien, en otra corrida, haga que ``WorkersIDEAgent`` escriba
al mismo archivo con el mismo esquema.

Cada registro de la bitácora trae el ``input_tokens`` de ESA llamada, que en
una API de chat sin estado es, por definición, el tamaño completo del
historial que se mandó en ese momento -- el proxy más honesto de "cuánto
contexto ocupa la conversación ahora mismo" que existe sin instrumentar el
proveedor. Comparar ``input_tokens`` de una llamada contra la siguiente
(menos lo que esa llamada generó de salida) aísla, además, cuánto se sumó
por CADA vuelta de herramientas -- que es "en qué se está yendo" con datos
reales, no con una estimación de caracteres.

Parte B -- ``/compact``: qué se conserva y qué es relleno
------------------------------------------------------------
El criterio de qué sobrevive un ``/compact`` NO es una decisión nueva de
este archivo: ya existe en producción. ``ide_sessions.SessionManager.
_continuation_prompt`` -- el mecanismo que reinyecta contexto entre turnos
de una misma sesión viva -- ya filtra el historial completo a solo tres
tipos de evento: ``user`` (lo que se pidió), ``assistant_final`` (lo que se
resolvió) y ``file`` (qué cambió). Todo lo demás (``status``, ``progress``,
``tool``, ``command``, ``output``, ``error``, ``mcp_confirmation``) es
narración operativa del turno -- útil para depurar en el momento, inservible
seis mensajes después. Este módulo usa ese MISMO criterio (sin importar
``ide_sessions.py``, por la frontera de arriba) porque inventar uno propio
y distinto haría que "lo que sobrevive a un turno en caliente" y "lo que
sobrevive a un ``/compact``" fueran dos nociones distintas de la misma
conversación, y eso es peor que ninguna.

La diferencia real con ``_continuation_prompt`` es que aquél solo FILTRA y
concatena verbatim (bueno para reinyectar en el próximo prompt sin
reinterpretar nada); ``/compact`` además tiene que decidir un ``objetivo
pendiente`` singular (no toda la lista de pedidos) y producir un texto
legible para una persona, no para el próximo turno del modelo.

Parte C -- ``/btw``: ve el contexto, no lo escribe
------------------------------------------------------
``preparar_contexto_btw`` es deliberadamente una función de solo LECTURA:
nunca muta ``events`` (ni siquiera los recorre con efectos secundarios) y
devuelve un ``ContextoSoloLectura`` inmutable (``frozen``, con tuplas en vez
de listas) para que ni siquiera por accidente alguien lo use como si fuera
un buffer que se puede seguir llenando. La garantía de "no entra al hilo
principal" es, en última instancia, responsabilidad de quien integre esto
(no se llama a ``Session.append`` con la pregunta ni con la respuesta) --
este módulo hace su parte no dando ninguna superficie para hacerlo.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from edecan_llm.task_router import modelos_ide_disponibles

# --------------------------------------------------------------------------- #
# Compartido
# --------------------------------------------------------------------------- #


class IDEContextoError(ValueError):
    """Entrada inválida para alguna de las tres operaciones de este módulo."""


def _truncar(texto: str, limite: int) -> str:
    """Recorta con un sufijo explícito -- mismo criterio que ``_truncate`` de
    ``edecan_core.llm_call_log`` (sin ``redact()``: a diferencia de esa
    bitácora, que persiste texto crudo a disco, esto solo reformatea texto
    que ya vive en la sesión en memoria del propio usuario)."""
    if len(texto) <= limite:
        return texto
    resto = len(texto) - limite
    return f"{texto[:limite]}…[recortado, {resto} caracteres más]"


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Parte A -- /context
# --------------------------------------------------------------------------- #

_LOG_FILENAME = "llm-calls.jsonl"
"""Debe coincidir con ``edecan_core.llm_call_log._LOG_FILENAME``. No se
importa esa constante (ver el docstring del módulo); si el nombre cambia
allá, se actualiza acá."""

UMBRAL_ALERTA_PORCENTAJE = 75.0
"""A partir de qué porcentaje de la ventana se avisa que conviene ``/compact``.
75 % deja margen real antes de que el proveedor empiece a recortar mensajes
o de que la próxima llamada, ya con la respuesta sumada, se pase de la
ventana -- no es el borde, es la señal para actuar antes del borde."""


@dataclass(frozen=True)
class LlamadaLLM:
    """Una línea ya parseada de ``llm-calls.jsonl`` -- ver
    ``edecan_core.llm_call_log.log_llm_call`` para el esquema completo. Solo
    se conservan los campos que este módulo necesita."""

    ts: datetime | None
    modelo: str | None
    iteracion: int | None
    input_tokens: int
    output_tokens: int
    tools_requested: tuple[str, ...]

    @property
    def tokens_totales(self) -> int:
        return self.input_tokens + self.output_tokens


def _parsear_registro(raw: Any) -> LlamadaLLM | None:
    """Convierte un dict crudo de la bitácora en ``LlamadaLLM``, o ``None``
    si viene corrupto/incompleto -- una línea mala del log no debe tumbar el
    cálculo completo de contexto (mismo criterio defensivo que
    ``ide_checkpoints._load_all`` con manifiestos corruptos)."""
    if not isinstance(raw, Mapping):
        return None
    try:
        input_tokens = int(raw.get("input_tokens") or 0)
        output_tokens = int(raw.get("output_tokens") or 0)
    except (TypeError, ValueError):
        return None
    modelo_raw = raw.get("model")
    modelo = modelo_raw if isinstance(modelo_raw, str) and modelo_raw else None
    iteracion_raw = raw.get("iteration")
    iteracion = iteracion_raw if isinstance(iteracion_raw, int) else None
    tools_requested = tuple(
        str(t) for t in (raw.get("tools_requested") or ()) if isinstance(t, str)
    )
    return LlamadaLLM(
        ts=_parse_ts(raw.get("ts")),
        modelo=modelo,
        iteracion=iteracion,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        tools_requested=tools_requested,
    )


def leer_bitacora(
    data_dir: Path | str,
    *,
    archivo_nombre: str = _LOG_FILENAME,
    tenant_id: Any = None,
    user_id: Any = None,
    desde_ts: str | None = None,
) -> list[dict[str, Any]]:
    """Lee ``llm-calls.jsonl`` y devuelve los registros crudos (dict), en el
    mismo orden en que quedaron escritos (cronológico).

    Filtros opcionales -- la bitácora es compartida por todo el tenant/usuario,
    no por conversación (no existe un ``session_id`` en su esquema), así que
    acotarla a UNA conversación es tarea de quien integre esto, con lo que
    tenga a mano (``tenant_id``/``user_id`` del turno, y el ``created_at`` de
    la sesión como ``desde_ts``).

    Nunca lanza: archivo ausente, ilegible, o con líneas corruptas -> esas
    líneas se saltan (o, si el archivo entero no se puede leer, lista vacía).
    Esto es deliberado -- una bitácora de depuración no debe bloquear
    ``/context`` si el archivo está de alguna forma dañado.
    """
    path = Path(data_dir).expanduser() / archivo_nombre
    if not path.is_file():
        return []
    desde_dt = _parse_ts(desde_ts) if desde_ts else None
    registros: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for linea in handle:
                linea = linea.strip()
                if not linea:
                    continue
                try:
                    fila = json.loads(linea)
                except json.JSONDecodeError:
                    continue
                if not isinstance(fila, dict):
                    continue
                if tenant_id is not None and str(fila.get("tenant_id")) != str(tenant_id):
                    continue
                if user_id is not None and str(fila.get("user_id")) != str(user_id):
                    continue
                if desde_dt is not None:
                    ts = _parse_ts(fila.get("ts"))
                    if ts is not None and ts < desde_dt:
                        continue
                registros.append(fila)
    except OSError:
        return []
    return registros


@dataclass(frozen=True)
class CrecimientoLlamada:
    """Cuánto contexto se sumó entre una llamada y la siguiente, y a qué
    herramientas se le atribuye.

    ``delta_tokens`` para la PRIMERA llamada es simplemente su
    ``input_tokens`` (la línea base: system prompt, herramientas ofrecidas,
    primer mensaje -- nada que atribuirle a una herramienta previa). Para el
    resto, es ``input_tokens`` de esta llamada MENOS lo que ya se esperaba
    que hubiera (``input_tokens + output_tokens`` de la llamada anterior,
    que es lo que de todas formas iba a viajar de vuelta como historial). Lo
    que sobra sobre eso es -- casi siempre -- el resultado de las
    herramientas que pidió la llamada anterior, que es a quien se le
    atribuye (``herramientas``), no a esta.
    """

    iteracion: int | None
    ts: str | None
    tokens_totales: int
    delta_tokens: int
    herramientas: tuple[str, ...]
    es_linea_base: bool

    def resumen(self) -> dict[str, Any]:
        return {
            "iteracion": self.iteracion,
            "ts": self.ts,
            "tokens_totales": self.tokens_totales,
            "delta_tokens": self.delta_tokens,
            "herramientas": list(self.herramientas),
            "es_linea_base": self.es_linea_base,
        }


@dataclass(frozen=True)
class ContextoConversacion:
    """Resultado completo de ``/context``: cuánto se usó y en qué se fue."""

    total_llamadas: int
    modelo: str | None
    tokens_entrada_actual: int
    tokens_salida_actual: int
    tokens_totales_actual: int
    ventana_maxima: int | None
    porcentaje_usado: float | None
    cerca_del_limite: bool
    crecimiento: list[CrecimientoLlamada] = field(default_factory=list)
    mayor_incremento: CrecimientoLlamada | None = None
    advertencias: list[str] = field(default_factory=list)

    def resumen(self) -> dict[str, Any]:
        return {
            "total_llamadas": self.total_llamadas,
            "modelo": self.modelo,
            "tokens": {
                "entrada": self.tokens_entrada_actual,
                "salida": self.tokens_salida_actual,
                "total": self.tokens_totales_actual,
            },
            "ventana_maxima": self.ventana_maxima,
            "porcentaje_usado": (
                round(self.porcentaje_usado, 1) if self.porcentaje_usado is not None else None
            ),
            "cerca_del_limite": self.cerca_del_limite,
            "crecimiento": [c.resumen() for c in self.crecimiento],
            "mayor_incremento": (
                self.mayor_incremento.resumen() if self.mayor_incremento else None
            ),
            "advertencias": list(self.advertencias),
        }


def _ventana_para_modelo(
    modelo: str | None, modelos: Sequence[Mapping[str, Any]]
) -> int | None:
    if not modelo:
        return None
    for fila in modelos:
        if str(fila.get("id")) == modelo:
            ventana = fila.get("contexto_ventana")
            if isinstance(ventana, (int, float)) and ventana > 0:
                return int(ventana)
    return None


def analizar_contexto(
    llamadas: Sequence[Mapping[str, Any]],
    *,
    modelo: str | None = None,
    modelos: Sequence[Mapping[str, Any]] | None = None,
    ruta_modelos_yaml: Path | str | None = None,
    umbral_alerta_porcentaje: float = UMBRAL_ALERTA_PORCENTAJE,
) -> ContextoConversacion:
    """Calcula el uso de contexto de UNA conversación a partir de sus
    registros de ``llm-calls.jsonl`` ya filtrados (ver ``leer_bitacora``).

    Parámetros
    ----------
    llamadas:
        Registros crudos (dict), en orden cronológico. Quien llame es
        responsable de acotarlos a la conversación que interesa -- este
        módulo no sabe de sesiones (ver docstring del archivo).
    modelo:
        Fuerza qué modelo usar para buscar la ventana de contexto, en vez de
        tomar el de la ÚLTIMA llamada (útil si el usuario cambió de modelo a
        mitad de conversación y se quiere ver el uso contra el actual).
    modelos:
        Lista de ModelCards ``{"id", "contexto_ventana", ...}`` -- por
        defecto ``edecan_llm.task_router.modelos_ide_disponibles()``. Se
        puede inyectar en tests para no depender del contenido real de
        ``config/modelos.yml``.
    """
    advertencias: list[str] = []
    parseadas = [p for p in (_parsear_registro(r) for r in llamadas) if p is not None]

    tabla_modelos = modelos if modelos is not None else modelos_ide_disponibles(ruta_modelos_yaml)

    if not parseadas:
        return ContextoConversacion(
            total_llamadas=0,
            modelo=modelo,
            tokens_entrada_actual=0,
            tokens_salida_actual=0,
            tokens_totales_actual=0,
            ventana_maxima=_ventana_para_modelo(modelo, tabla_modelos),
            porcentaje_usado=None,
            cerca_del_limite=False,
            advertencias=[
                "No hay llamadas registradas todavía para esta conversación; "
                "nada que contabilizar."
            ],
        )

    modelos_vistos = {p.modelo for p in parseadas if p.modelo}
    if len(modelos_vistos) > 1:
        advertencias.append(
            "Esta conversación usó más de un modelo "
            f"({', '.join(sorted(modelos_vistos))}); el porcentaje usa el más reciente."
        )

    ultima = parseadas[-1]
    modelo_resuelto = modelo or ultima.modelo
    ventana = _ventana_para_modelo(modelo_resuelto, tabla_modelos)
    if ventana is None:
        if modelo_resuelto:
            advertencias.append(
                f"No se conoce la ventana de contexto del modelo «{modelo_resuelto}»; "
                "no se calcula el porcentaje usado."
            )
        else:
            advertencias.append(
                "Ningún registro trae el modelo usado; no se calcula el porcentaje usado."
            )

    tokens_totales_actual = ultima.tokens_totales
    porcentaje = 100.0 * tokens_totales_actual / ventana if ventana else None
    cerca_del_limite = porcentaje is not None and porcentaje >= umbral_alerta_porcentaje
    if cerca_del_limite:
        advertencias.append(
            f"El contexto usado ({porcentaje:.1f}%) superó el umbral de alerta "
            f"({umbral_alerta_porcentaje:.0f}%); conviene correr /compact pronto."
        )

    crecimiento: list[CrecimientoLlamada] = []
    for indice, actual in enumerate(parseadas):
        if indice == 0:
            crecimiento.append(
                CrecimientoLlamada(
                    iteracion=actual.iteracion,
                    ts=actual.ts.isoformat() if actual.ts else None,
                    tokens_totales=actual.tokens_totales,
                    delta_tokens=actual.input_tokens,
                    herramientas=(),
                    es_linea_base=True,
                )
            )
            continue
        anterior = parseadas[indice - 1]
        esperado = anterior.input_tokens + anterior.output_tokens
        crecimiento.append(
            CrecimientoLlamada(
                iteracion=actual.iteracion,
                ts=actual.ts.isoformat() if actual.ts else None,
                tokens_totales=actual.tokens_totales,
                delta_tokens=actual.input_tokens - esperado,
                herramientas=anterior.tools_requested,
                es_linea_base=False,
            )
        )

    candidatos = [c for c in crecimiento if not c.es_linea_base and c.delta_tokens > 0]
    mayor_incremento = max(candidatos, key=lambda c: c.delta_tokens) if candidatos else None

    return ContextoConversacion(
        total_llamadas=len(parseadas),
        modelo=modelo_resuelto,
        tokens_entrada_actual=ultima.input_tokens,
        tokens_salida_actual=ultima.output_tokens,
        tokens_totales_actual=tokens_totales_actual,
        ventana_maxima=ventana,
        porcentaje_usado=porcentaje,
        cerca_del_limite=cerca_del_limite,
        crecimiento=crecimiento,
        mayor_incremento=mayor_incremento,
        advertencias=advertencias,
    )


def resumen_contexto(llamadas: Sequence[Mapping[str, Any]], **kwargs: Any) -> dict[str, Any]:
    """Atajo para la UI: ``analizar_contexto(...).resumen()`` en una llamada."""
    return analizar_contexto(llamadas, **kwargs).resumen()


# --------------------------------------------------------------------------- #
# Parte B -- /compact
# --------------------------------------------------------------------------- #

MAX_PEDIDOS = 10
MAX_DECISIONES = 15
MAX_CHARS_ITEM = 400

_PATRON_ARCHIVO = re.compile(r"^Archivo actualizado: (?P<ruta>.+)$")
"""``WorkersIDEAgent`` escribe el evento ``file`` con este prefijo exacto
(ver ``ide_workers_agent.py``). Si el texto no calza con el patrón se usa
crudo -- mismo criterio defensivo que ``ide_costos._nombre_herramienta``:
mejor un dato raro y visible que perder el evento entero."""


def _extraer_ruta_archivo(texto: str) -> str:
    match = _PATRON_ARCHIVO.match(texto.strip())
    return match.group("ruta").strip() if match else texto.strip()


@dataclass(frozen=True)
class ResumenCompacto:
    """Lo que sobrevive a un ``/compact``, más el texto ya armado para
    mostrar o reinyectar."""

    resumen_texto: str
    pedidos: tuple[str, ...]
    decisiones: tuple[str, ...]
    archivos_tocados: tuple[str, ...]
    objetivo_pendiente: str | None
    cursor_desde: int | None
    cursor_hasta: int | None
    eventos_conservados: int
    eventos_descartados: int
    descartados_por_tipo: dict[str, int] = field(default_factory=dict)

    def resumen(self) -> dict[str, Any]:
        return {
            "resumen_texto": self.resumen_texto,
            "pedidos": list(self.pedidos),
            "decisiones": list(self.decisiones),
            "archivos_tocados": list(self.archivos_tocados),
            "objetivo_pendiente": self.objetivo_pendiente,
            "cursor_desde": self.cursor_desde,
            "cursor_hasta": self.cursor_hasta,
            "eventos_conservados": self.eventos_conservados,
            "eventos_descartados": self.eventos_descartados,
            "descartados_por_tipo": dict(self.descartados_por_tipo),
        }


def _construir_resumen_texto(
    *,
    pedidos: tuple[str, ...],
    decisiones: tuple[str, ...],
    archivos: tuple[str, ...],
    objetivo: str | None,
    descartados_por_tipo: dict[str, int],
) -> str:
    partes: list[str] = ["Resumen compactado de la conversación."]

    if objetivo:
        partes.append(f"\nObjetivo pendiente: {objetivo}")
    else:
        partes.append("\nObjetivo pendiente: ninguno detectado (el último pedido ya se respondió).")

    if pedidos:
        partes.append("\nSe pidió:")
        partes.extend(f"- {p}" for p in pedidos)

    if decisiones:
        partes.append("\nDecisiones tomadas:")
        partes.extend(f"- {d}" for d in decisiones)

    if archivos:
        partes.append("\nArchivos tocados:")
        partes.extend(f"- {a}" for a in archivos)

    if descartados_por_tipo:
        total = sum(descartados_por_tipo.values())
        detalle = ", ".join(f"{n} de «{t}»" for t, n in sorted(descartados_por_tipo.items()))
        partes.append(f"\nSe descartó como relleno: {total} eventos ({detalle}).")

    return "\n".join(partes)


def compactar(
    events: Sequence[Mapping[str, Any]],
    *,
    objetivo_pendiente: str | None = None,
    max_pedidos: int = MAX_PEDIDOS,
    max_decisiones: int = MAX_DECISIONES,
    max_chars_item: int = MAX_CHARS_ITEM,
) -> ResumenCompacto:
    """Resume ``events`` (la misma forma que ``ide_sessions.Session.events``)
    conservando lo que no puede perderse: pedidos, decisiones y archivos --
    ver el docstring del módulo sobre por qué es exactamente ese criterio, y
    no otro, el que se usa acá.

    ``objetivo_pendiente`` fuerza el valor (p. ej. si quien integra ya sabe
    la meta activa por ``ide_plan.Plan.goal``); si no se pasa, se deriva del
    último evento ``user`` SI no vino seguido de un ``assistant_final`` --
    es decir, si la conversación quedó a media respuesta.

    ``events`` puede ser cualquier ``Sequence`` (incluido un ``deque``, como
    el que usa ``Session`` de verdad): esta función solo itera, nunca hace
    slicing ni indexado negativo.
    """
    pedidos: list[str] = []
    decisiones: list[str] = []
    archivos: list[str] = []
    vistos_archivos: set[str] = set()
    descartados_por_tipo: dict[str, int] = {}
    cursor_desde: int | None = None
    cursor_hasta: int | None = None
    pendiente_sin_responder = False

    for evento in events:
        cursor = evento.get("cursor")
        if isinstance(cursor, int):
            cursor_desde = cursor if cursor_desde is None else min(cursor_desde, cursor)
            cursor_hasta = cursor if cursor_hasta is None else max(cursor_hasta, cursor)

        tipo = str(evento.get("type") or "")
        texto = str(evento.get("text") or "").strip()
        if not texto:
            continue

        if tipo == "user":
            pedidos.append(_truncar(texto, max_chars_item))
            pendiente_sin_responder = True
        elif tipo == "assistant_final":
            decisiones.append(_truncar(texto, max_chars_item))
            pendiente_sin_responder = False
        elif tipo == "file":
            ruta = _extraer_ruta_archivo(texto)
            if ruta not in vistos_archivos:
                vistos_archivos.add(ruta)
                archivos.append(ruta)
        else:
            descartados_por_tipo[tipo] = descartados_por_tipo.get(tipo, 0) + 1

    objetivo = (
        objetivo_pendiente.strip()
        if isinstance(objetivo_pendiente, str) and objetivo_pendiente.strip()
        else None
    )
    if objetivo is None and pendiente_sin_responder and pedidos:
        objetivo = pedidos[-1]

    pedidos_recortados = tuple(pedidos[-max_pedidos:]) if max_pedidos > 0 else ()
    decisiones_recortadas = tuple(decisiones[-max_decisiones:]) if max_decisiones > 0 else ()
    archivos_tupla = tuple(archivos)

    resumen_texto = _construir_resumen_texto(
        pedidos=pedidos_recortados,
        decisiones=decisiones_recortadas,
        archivos=archivos_tupla,
        objetivo=objetivo,
        descartados_por_tipo=descartados_por_tipo,
    )

    return ResumenCompacto(
        resumen_texto=resumen_texto,
        pedidos=pedidos_recortados,
        decisiones=decisiones_recortadas,
        archivos_tocados=archivos_tupla,
        objetivo_pendiente=objetivo,
        cursor_desde=cursor_desde,
        cursor_hasta=cursor_hasta,
        eventos_conservados=(
            len(pedidos_recortados) + len(decisiones_recortadas) + len(archivos_tupla)
        ),
        eventos_descartados=sum(descartados_por_tipo.values()),
        descartados_por_tipo=descartados_por_tipo,
    )


# --------------------------------------------------------------------------- #
# Parte C -- /btw
# --------------------------------------------------------------------------- #

MAX_DECISIONES_BTW = 8
"""``/btw`` es una pregunta lateral corta: no necesita el historial completo
de decisiones, solo las suficientes recientes para responder con contexto."""


@dataclass(frozen=True)
class ContextoSoloLectura:
    """Vista congelada del contexto para responder una pregunta ``/btw`` sin
    tocar el hilo principal -- ver el docstring del módulo. Todo lo expuesto
    acá son tuplas/strings inmutables a propósito."""

    pregunta: str
    objetivo_pendiente: str | None
    decisiones_recientes: tuple[str, ...]
    archivos_tocados: tuple[str, ...]
    resumen_texto: str

    def resumen(self) -> dict[str, Any]:
        return {
            "pregunta": self.pregunta,
            "objetivo_pendiente": self.objetivo_pendiente,
            "decisiones_recientes": list(self.decisiones_recientes),
            "archivos_tocados": list(self.archivos_tocados),
            "resumen_texto": self.resumen_texto,
        }


def preparar_contexto_btw(
    events: Sequence[Mapping[str, Any]],
    pregunta: str,
    *,
    max_decisiones: int = MAX_DECISIONES_BTW,
) -> ContextoSoloLectura:
    """Arma el contexto de solo lectura para responder ``pregunta`` sin que
    entre al hilo principal.

    Esta función NUNCA muta ``events`` ni devuelve nada mutable que permita
    "escribirle" al historial -- la garantía completa de "no entra al hilo
    principal" también depende de que quien integre esto (fuera de este
    archivo) no llame a ``Session.append`` con la pregunta ni la respuesta;
    acá solo se garantiza la mitad que le toca a este módulo: leer sin
    tocar.
    """
    if not isinstance(pregunta, str) or not pregunta.strip():
        raise IDEContextoError("La pregunta de /btw no puede estar vacía.")

    compacto = compactar(events, max_pedidos=0, max_decisiones=max_decisiones)

    return ContextoSoloLectura(
        pregunta=pregunta.strip(),
        objetivo_pendiente=compacto.objetivo_pendiente,
        decisiones_recientes=compacto.decisiones,
        archivos_tocados=compacto.archivos_tocados,
        resumen_texto=compacto.resumen_texto,
    )
