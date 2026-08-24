"""Contabilidad de costo por tarea del agente del IDE.

El disparador de este archivo fue un caso real: una edición de un README le
tomó al agente 22 minutos, 53 acciones y 71 salidas, y la persona solo vio
números sueltos sin saber si eso era normal o un disparate ("me pareció
abusivo", dijo). Un contador que solo muestra un total no resuelve ese
problema; lo que hace falta es (a) poder comparar contra lo típico de tareas
parecidas y (b) señalar el patrón de bucle -- herramienta repetida sin que
la tarea avance -- que es exactamente lo que voló esos 22 minutos.

Este módulo es de solo lectura y no depende de ``ide_sessions.py`` ni de
``ide_workers_agent.py``: recibe la lista de eventos ya persistidos de UN
turno de agente (el mismo recorte que ``ide_sessions._run_workers_agent`` ya
sabe hacer con ``turn_start_cursor``, es decir ``[e for e in session.events
if e["cursor"] > turn_start_cursor]``) y devuelve una estructura lista para
que la UI la pinte. La integración real (dónde se llama, qué se persiste
para construir el historial de comparación) la hace quien conecte esta
pieza; aquí solo vive el cálculo.

Formato de evento esperado (el que ya escribe ``ide_sessions.Session.append``):
``{"cursor": int, "type": str, "text": str, "timestamp": ISO-8601, "stream": str | None}``.

Los tipos que hoy emite ``WorkersIDEAgent`` son: ``user``, ``status``,
``progress``, ``tool`` (texto ``"Usando {nombre}."``), ``command``, ``file``,
``output``, ``error``, ``assistant_final``, ``mcp_confirmation``. Ninguno de
esos trae tokens reales todavía -- el ``StreamChunk`` de ``edecan_llm``
sí soporta un tipo ``"usage"`` (ver ``edecan_llm.base.Usage``), pero nadie lo
persiste como evento de sesión hoy. Por eso este módulo:

1. Si encuentra eventos ``type == "usage"`` (JSON con ``input_tokens`` /
   ``output_tokens`` / ``cached_input_tokens``), usa esos números reales. El
   día en que alguien conecte esta pieza y ``ide_workers_agent.py`` empiece a
   persistir ese chunk como evento de sesión, este módulo pasa a trabajar
   con tokens reales sin que haga falta tocar una línea de aquí -- es el
   mismo contrato de ``edecan_llm.base.Usage``, solo serializado a JSON.
2. Si no los encuentra, estima tokens con la heurística usual de ~4
   caracteres por token, y lo marca explícitamente como estimado en el
   resultado (``tokens_son_estimados``) para que la UI nunca lo presente
   como una cifra exacta.
"""

from __future__ import annotations

import json
import re
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from edecan_llm.base import Usage
from edecan_llm.costs import COSTOS
from edecan_llm.costs import estimate as _estimate_usd

# ~4 caracteres por token es la heurística estándar para texto en inglés y
# razonable para español; sirve para dar una cifra orientativa cuando no hay
# un evento "usage" real, nunca para facturar.
_CHARS_POR_TOKEN = 4.0

# Eventos cuyo texto es "lo que el modelo generó" -- se cuentan como salida
# estimada porque son texto que el modelo produjo en ese turno.
_TIPOS_SALIDA_MODELO = {"progress", "assistant_final", "tool"}
# Eventos cuyo texto es "lo que se le devolvió al modelo" (resultado de una
# herramienta, prompt del usuario) -- se cuentan como entrada estimada
# porque es lo que vuelve a viajar como contexto en la siguiente llamada.
_TIPOS_ENTRADA_MODELO = {"user", "command", "file", "output", "error", "mcp_confirmation"}

# Eventos que, ocurridos dentro de un tramo de herramienta repetida, cuentan
# como evidencia de que SÍ hubo avance (un archivo cambió de verdad).
_TIPO_EVENTO_CAMBIO = "file"

_PATRON_TOOL = re.compile(r"^Usando (?P<nombre>.+)\.$")

# Umbral por defecto para "esto se salió de lo normal": el doble de la
# mediana de tareas parecidas. Un factor menor genera demasiados falsos
# positivos con historiales chicos; uno mayor deja pasar casos reales.
UMBRAL_FACTOR_ANORMAL = 2.0
# No comparar contra menos de 3 tareas previas: con 1-2 muestras la
# "mediana" es solo la última tarea y el veredicto sería ruido.
MIN_TAREAS_PARA_COMPARAR = 3
# Repeticiones consecutivas mínimas de la misma firma (herramienta + detalle)
# para considerarlo un bucle. 3 porque 2 repeticiones son comunes en un
# flujo normal (leer, corregir, releer para verificar); 3+ ya es patrón.
MIN_REPETICIONES_BUCLE = 3


def _parse_timestamp(value: Any) -> datetime | None:
    """Convierte el ``timestamp`` ISO-8601 de un evento a ``datetime``.

    Devuelve ``None`` en vez de reventar: un evento con timestamp corrupto
    no debe tirar abajo el cálculo de costo de toda la tarea, solo quedar
    fuera del rango de tiempo que se puede medir.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _texto(evento: Mapping[str, Any]) -> str:
    value = evento.get("text")
    return value if isinstance(value, str) else ""


def _nombre_herramienta(evento: Mapping[str, Any]) -> str:
    """Extrae el nombre de la herramienta de un evento ``type == "tool"``.

    Hoy el único dato disponible es el texto humano ``"Usando X."`` que
    escribe ``ide_workers_agent.py``; si algún día ese evento trae un campo
    estructurado (``evento["tool"]``) se prioriza porque es más confiable
    que parsear una frase pensada para leerse, no para parsearse.
    """
    structured = evento.get("tool")
    if isinstance(structured, str) and structured:
        return structured
    match = _PATRON_TOOL.match(_texto(evento).strip())
    if match:
        return match.group("nombre").strip()
    # Formato inesperado: se conserva el texto crudo en vez de perder el
    # evento. Mejor una firma rara y visible que una excepción a medianoche.
    return _texto(evento).strip() or "herramienta_desconocida"


def _tokens_estimados_de_texto(texto: str) -> int:
    if not texto:
        return 0
    return max(1, round(len(texto) / _CHARS_POR_TOKEN))


def _usage_de_evento(evento: Mapping[str, Any]) -> Usage | None:
    if evento.get("type") != "usage":
        return None
    try:
        datos = json.loads(_texto(evento))
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(datos, dict):
        return None
    try:
        return Usage(
            input_tokens=int(datos.get("input_tokens") or 0),
            output_tokens=int(datos.get("output_tokens") or 0),
            cached_input_tokens=int(datos.get("cached_input_tokens") or 0),
        )
    except (TypeError, ValueError):
        return None


def _duracion_humana(segundos: float) -> str:
    if segundos < 60:
        return f"{segundos:.0f}s"
    minutos, resto = divmod(int(round(segundos)), 60)
    if minutos < 60:
        return f"{minutos}m {resto:02d}s"
    horas, minutos = divmod(minutos, 60)
    return f"{horas}h {minutos:02d}m"


@dataclass(frozen=True)
class DesgloseHerramienta:
    """Cuánto costó UNA herramienta dentro de la tarea completa."""

    nombre: str
    acciones: int
    tokens_estimados: int
    tokens_reales: int | None
    segundos: float
    porcentaje_tokens: float
    porcentaje_tiempo: float

    def resumen(self) -> dict[str, Any]:
        return {
            "nombre": self.nombre,
            "acciones": self.acciones,
            "tokens_estimados": self.tokens_estimados,
            "tokens_reales": self.tokens_reales,
            "segundos": round(self.segundos, 1),
            "porcentaje_tokens": round(self.porcentaje_tokens, 1),
            "porcentaje_tiempo": round(self.porcentaje_tiempo, 1),
        }


@dataclass(frozen=True)
class BucleDetectado:
    """Un tramo de acciones equivalentes que se repitió sin avanzar.

    Este es el hallazgo que de verdad explica una tarea cara: no "gastó
    muchos tokens" sino "se quedó dando vueltas". ``periodo`` es 1 para una
    herramienta repetida en seco (A, A, A, ...) y 2 para un ping-pong de dos
    pasos (A, B, A, B, ...), que es el otro patrón real de bucle: intentar
    algo, verificar, fallar, reintentar exactamente igual.
    """

    firma: str
    herramientas: tuple[str, ...]
    periodo: int
    repeticiones_del_ciclo: int
    indice_inicio: int
    indice_fin: int
    hubo_cambios_en_el_tramo: bool

    def resumen(self) -> dict[str, Any]:
        return {
            "herramientas": list(self.herramientas),
            "patron": " → ".join(self.herramientas),
            "repeticiones_del_ciclo": self.repeticiones_del_ciclo,
            "acciones_involucradas": self.indice_fin - self.indice_inicio + 1,
            "indice_inicio": self.indice_inicio,
            "indice_fin": self.indice_fin,
            "hubo_cambios_en_el_tramo": self.hubo_cambios_en_el_tramo,
        }


@dataclass(frozen=True)
class ComparacionHistorica:
    """Esta tarea contra la mediana de tareas parecidas anteriores."""

    tareas_comparadas: int
    duracion_tipica_segundos: float
    acciones_tipicas: float
    tokens_tipicos: float
    factor_duracion: float
    factor_acciones: float
    factor_tokens: float
    fuera_de_lo_normal: bool
    motivo: str

    def resumen(self) -> dict[str, Any]:
        return {
            "tareas_comparadas": self.tareas_comparadas,
            "duracion_tipica_segundos": round(self.duracion_tipica_segundos, 1),
            "acciones_tipicas": round(self.acciones_tipicas, 1),
            "tokens_tipicos": round(self.tokens_tipicos, 1),
            "factor_duracion": round(self.factor_duracion, 2),
            "factor_acciones": round(self.factor_acciones, 2),
            "factor_tokens": round(self.factor_tokens, 2),
            "fuera_de_lo_normal": self.fuera_de_lo_normal,
            "motivo": self.motivo,
        }


@dataclass(frozen=True)
class TaskCost:
    """Resultado completo: lo que costó una tarea del agente del IDE."""

    total_acciones: int
    total_eventos: int
    total_salidas: int
    duracion_segundos: float
    tokens_entrada: int
    tokens_salida: int
    tokens_totales: int
    tokens_son_estimados: bool
    costo_usd: float | None
    modelo: str | None
    por_herramienta: list[DesgloseHerramienta] = field(default_factory=list)
    bucles: list[BucleDetectado] = field(default_factory=list)
    comparacion: ComparacionHistorica | None = None
    advertencias: list[str] = field(default_factory=list)

    def resumen(self) -> dict[str, Any]:
        """Forma lista para que la UI pinte el resumen de una tarea."""
        senales: list[str] = []
        if self.bucles:
            senales.append("bucle_detectado")
        if self.comparacion is not None and self.comparacion.fuera_de_lo_normal:
            senales.append("fuera_de_lo_normal")
        return {
            "total_acciones": self.total_acciones,
            "total_eventos": self.total_eventos,
            "total_salidas": self.total_salidas,
            "duracion_segundos": round(self.duracion_segundos, 1),
            "duracion_humana": _duracion_humana(self.duracion_segundos),
            "tokens": {
                "entrada": self.tokens_entrada,
                "salida": self.tokens_salida,
                "total": self.tokens_totales,
                "estimados": self.tokens_son_estimados,
            },
            "costo_usd": round(self.costo_usd, 4) if self.costo_usd is not None else None,
            "modelo": self.modelo,
            "por_herramienta": [item.resumen() for item in self.por_herramienta],
            "bucles": [item.resumen() for item in self.bucles],
            "comparacion": self.comparacion.resumen() if self.comparacion else None,
            "senales": senales,
            "advertencias": list(self.advertencias),
        }


@dataclass
class _Accion:
    """Un paso de herramienta ya resuelto: nombre + detalle + costo del tramo."""

    indice: int
    nombre: str
    detalle: str | None
    inicio: datetime | None
    fin: datetime | None
    tokens_estimados: int
    tokens_reales: int | None
    hubo_cambio: bool

    @property
    def firma(self) -> str:
        return f"{self.nombre}::{self.detalle or ''}"

    @property
    def segundos(self) -> float:
        if self.inicio is None or self.fin is None:
            return 0.0
        return max(0.0, (self.fin - self.inicio).total_seconds())


def _detalle_de_tramo(eventos_tramo: Sequence[Mapping[str, Any]]) -> str | None:
    """Busca algo que distinga esta invocación de otra de la misma herramienta.

    ``ejecutar_comando`` deja el argv en el evento ``command`` y
    ``escribir_archivo``/``editar_archivo`` dejan la ruta en el evento
    ``file``. Herramientas de solo lectura (``leer_archivo``,
    ``listar_archivos``, ``buscar_en_archivos``, ``buscar_web``) no dejan
    ningún detalle hoy -- la firma queda solo con el nombre, que es una
    limitación real: dos lecturas de archivos DISTINTOS se ven iguales. Se
    documenta en vez de fingir precisión que el evento no tiene.
    """
    for evento in eventos_tramo:
        tipo = evento.get("type")
        if tipo == "command":
            return _texto(evento).strip() or None
        if tipo == "file":
            return _texto(evento).strip() or None
    return None


def _construir_acciones(eventos: Sequence[Mapping[str, Any]]) -> list[_Accion]:
    """Reconstruye la secuencia de invocaciones de herramienta con su costo.

    Un "tramo" va desde un evento ``tool`` (exclusive del anterior) hasta el
    siguiente evento ``tool`` o el final de la lista. Todo lo que ocurre en
    ese tramo (comando, salida, archivo, error) se atribuye a la herramienta
    que lo disparó -- así el desglose por herramienta refleja de verdad en
    qué se fue el tiempo/tokens, no solo cuántas veces se llamó.

    ``eventos`` se normaliza a ``list`` de entrada: el llamador real
    (``ide_sessions.Session.events``) es un ``collections.deque`` -- soporta
    iteración y ``len()`` como cualquier ``Sequence``, pero NO slicing
    (``eventos[pos:fin]`` más abajo), y un ``deque`` no se distingue de una
    lista por el tipo declarado (``Sequence``). Sin esta normalización, la
    primera tarea con al menos una herramienta revienta con ``TypeError``
    contra datos reales aunque los tests con listas armadas a mano pasen
    limpios (hallazgo real, ver ``test_piezas_ide_integrables.py``).
    """
    eventos = list(eventos)
    indices_tool = [i for i, ev in enumerate(eventos) if ev.get("type") == "tool"]
    acciones: list[_Accion] = []
    for orden, pos in enumerate(indices_tool, start=1):
        fin_tramo = indices_tool[orden] if orden < len(indices_tool) else len(eventos)
        tramo = eventos[pos:fin_tramo]
        nombre = _nombre_herramienta(eventos[pos])
        detalle = _detalle_de_tramo(tramo[1:])
        inicio = _parse_timestamp(eventos[pos].get("timestamp"))
        fin = _parse_timestamp(tramo[-1].get("timestamp")) if tramo else inicio
        tokens_reales_tramo = 0
        tiene_usage_real = False
        tokens_estimados_tramo = 0
        hubo_cambio = False
        for evento in tramo:
            tipo = evento.get("type")
            usage = _usage_de_evento(evento)
            if usage is not None:
                tiene_usage_real = True
                tokens_reales_tramo += usage.input_tokens + usage.output_tokens
                continue
            if tipo == _TIPO_EVENTO_CAMBIO:
                hubo_cambio = True
            tokens_estimados_tramo += _tokens_estimados_de_texto(_texto(evento))
        acciones.append(
            _Accion(
                indice=orden,
                nombre=nombre,
                detalle=detalle,
                inicio=inicio,
                fin=fin,
                tokens_estimados=tokens_estimados_tramo,
                tokens_reales=tokens_reales_tramo if tiene_usage_real else None,
                hubo_cambio=hubo_cambio,
            )
        )
    return acciones


def _detectar_bucles(
    acciones: Sequence[_Accion], *, min_repeticiones: int = MIN_REPETICIONES_BUCLE
) -> list[BucleDetectado]:
    """Encuentra tramos donde la misma herramienta (o el mismo par) se repite.

    Dos pasadas, de más específico a más general, para no reportar el mismo
    tramo dos veces:

    1. Período 1: la firma exacta se repite en seco (A, A, A, ...). Es el
       caso literal del incidente que motivó este archivo.
    2. Período 2 sobre lo que quedó sin marcar: un ciclo de dos pasos se
       repite (A, B, A, B, ...), típico de "intento, verifico, falla,
       reintento igual".
    """
    n = len(acciones)
    cubierto = [False] * n
    bucles: list[BucleDetectado] = []

    i = 0
    while i < n:
        j = i
        while j + 1 < n and acciones[j + 1].firma == acciones[i].firma:
            j += 1
        repeticiones = j - i + 1
        if repeticiones >= min_repeticiones:
            tramo = acciones[i : j + 1]
            bucles.append(
                BucleDetectado(
                    firma=acciones[i].firma,
                    herramientas=(acciones[i].nombre,),
                    periodo=1,
                    repeticiones_del_ciclo=repeticiones,
                    indice_inicio=acciones[i].indice,
                    indice_fin=acciones[j].indice,
                    hubo_cambios_en_el_tramo=any(a.hubo_cambio for a in tramo),
                )
            )
            for k in range(i, j + 1):
                cubierto[k] = True
            i = j + 1
        else:
            i += 1

    i = 0
    while i < n - 3:
        if cubierto[i] or cubierto[i + 1]:
            i += 1
            continue
        par = (acciones[i].firma, acciones[i + 1].firma)
        if par[0] == par[1]:
            i += 1
            continue
        j = i
        ciclos = 0
        while (
            j + 1 < n
            and not cubierto[j]
            and not cubierto[j + 1]
            and (acciones[j].firma, acciones[j + 1].firma) == par
        ):
            ciclos += 1
            j += 2
        fin = j - 1
        if ciclos >= min_repeticiones:
            tramo = acciones[i : fin + 1]
            bucles.append(
                BucleDetectado(
                    firma=f"{par[0]} → {par[1]}",
                    herramientas=(acciones[i].nombre, acciones[i + 1].nombre),
                    periodo=2,
                    repeticiones_del_ciclo=ciclos,
                    indice_inicio=acciones[i].indice,
                    indice_fin=acciones[fin].indice,
                    hubo_cambios_en_el_tramo=any(a.hubo_cambio for a in tramo),
                )
            )
            for k in range(i, fin + 1):
                cubierto[k] = True
            i = fin + 1
        else:
            i += 1

    bucles.sort(key=lambda b: b.indice_inicio)
    return bucles


def _extraer_metrica(item: Mapping[str, Any] | TaskCost, campo: str) -> float | None:
    if isinstance(item, TaskCost):
        valor = getattr(item, campo, None)
    else:
        valor = item.get(campo)
    if isinstance(valor, (int, float)):
        return float(valor)
    return None


def _comparar_con_historial(
    *,
    duracion_segundos: float,
    total_acciones: int,
    tokens_totales: int,
    history: Sequence[Mapping[str, Any] | TaskCost] | None,
    umbral_factor: float,
    min_tareas: int,
) -> ComparacionHistorica | None:
    if not history:
        return None

    def _valores(campo: str) -> list[float]:
        return [v for v in (_extraer_metrica(t, campo) for t in history) if v is not None]

    duraciones = _valores("duracion_segundos")
    accioness = _valores("total_acciones")
    tokeness = _valores("tokens_totales")
    if len(duraciones) < min_tareas or len(accioness) < min_tareas or len(tokeness) < min_tareas:
        return None

    duracion_tipica = statistics.median(duraciones)
    acciones_tipicas = statistics.median(accioness)
    tokens_tipicos = statistics.median(tokeness)

    factor_duracion = duracion_segundos / duracion_tipica if duracion_tipica > 0 else 0.0
    factor_acciones = total_acciones / acciones_tipicas if acciones_tipicas > 0 else 0.0
    factor_tokens = tokens_totales / tokens_tipicos if tokens_tipicos > 0 else 0.0

    disparadores: list[str] = []
    if factor_duracion >= umbral_factor:
        disparadores.append(
            f"la duración fue {factor_duracion:.1f}x la típica "
            f"({_duracion_humana(duracion_segundos)} vs {_duracion_humana(duracion_tipica)} típico)"
        )
    if factor_acciones >= umbral_factor:
        disparadores.append(
            f"las acciones fueron {factor_acciones:.1f}x lo típico "
            f"({total_acciones} vs {acciones_tipicas:.0f} típico)"
        )
    if factor_tokens >= umbral_factor:
        disparadores.append(
            f"los tokens fueron {factor_tokens:.1f}x lo típico "
            f"({tokens_totales} vs {tokens_tipicos:.0f} típico)"
        )

    fuera_de_lo_normal = bool(disparadores)
    motivo = (
        "; ".join(disparadores)
        if disparadores
        else "dentro de lo típico de tareas parecidas."
    )
    return ComparacionHistorica(
        tareas_comparadas=len(duraciones),
        duracion_tipica_segundos=duracion_tipica,
        acciones_tipicas=acciones_tipicas,
        tokens_tipicos=tokens_tipicos,
        factor_duracion=factor_duracion,
        factor_acciones=factor_acciones,
        factor_tokens=factor_tokens,
        fuera_de_lo_normal=fuera_de_lo_normal,
        motivo=motivo,
    )


def analizar_tarea(
    events: Sequence[Mapping[str, Any]],
    *,
    modelo: str | None = None,
    started_at: str | None = None,
    ended_at: str | None = None,
    history: Sequence[Mapping[str, Any] | TaskCost] | None = None,
    tabla_precios: Mapping[str, tuple[float, float]] | None = None,
    umbral_factor_anormal: float = UMBRAL_FACTOR_ANORMAL,
    min_tareas_para_comparar: int = MIN_TAREAS_PARA_COMPARAR,
    min_repeticiones_bucle: int = MIN_REPETICIONES_BUCLE,
) -> TaskCost:
    """Calcula la contabilidad de costo de UNA tarea del agente del IDE.

    Parámetros
    ----------
    events:
        Eventos de sesión (``cursor``/``type``/``text``/``timestamp``) de un
        solo turno, en orden. Quien llame es responsable de recortarlos al
        turno que le interesa (ver el docstring del módulo).
    modelo:
        Nombre del modelo usado en el turno (p. ej. ``session.metadata["model"]``
        o el modelo por defecto resuelto por ``ide_workers_agent``). Solo se
        usa para buscar el precio en ``tabla_precios``; si no se conoce el
        precio, ``costo_usd`` queda en ``None`` en vez de inventar un número.
    started_at / ended_at:
        ISO-8601 opcionales para fijar el borde de la tarea con más
        precisión que el primer/último evento (p. ej. los campos de
        ``session.metadata``). Si no se pasan, se derivan de los eventos.
    history:
        Tareas anteriores "parecidas" (mismo tipo de trabajo) para dar
        contexto ("esto se salió de lo normal" vs "esto es lo de siempre").
        Cada elemento puede ser un ``TaskCost`` o un dict con al menos
        ``duracion_segundos``, ``total_acciones`` y ``tokens_totales``. Con
        menos de ``min_tareas_para_comparar`` no se emite comparación: la
        mediana de 1-2 tareas es ruido, no una línea base.
    tabla_precios:
        Tabla ``{modelo: (usd_entrada_por_mtok, usd_salida_por_mtok)}``. Por
        defecto usa ``edecan_llm.costs.COSTOS``.
    """
    advertencias: list[str] = []

    acciones = _construir_acciones(events)

    inicio_dt = _parse_timestamp(started_at)
    fin_dt = _parse_timestamp(ended_at)
    if inicio_dt is None or fin_dt is None:
        marcas_crudas = (_parse_timestamp(ev.get("timestamp")) for ev in events)
        marcas = [marca for marca in marcas_crudas if marca is not None]
        if marcas:
            inicio_dt = inicio_dt or min(marcas)
            fin_dt = fin_dt or max(marcas)
    duracion_segundos = (
        max(0.0, (fin_dt - inicio_dt).total_seconds()) if inicio_dt and fin_dt else 0.0
    )
    if not events:
        advertencias.append("La tarea no tiene eventos; no hay nada que contabilizar.")
    elif inicio_dt is None or fin_dt is None:
        advertencias.append(
            "No se pudo calcular la duración: los eventos no traen timestamp válido."
        )

    # El total se calcula sobre TODOS los eventos, no solo los que caen
    # dentro de un tramo de herramienta. Si se calculara a partir de
    # ``acciones`` (como en un borrador anterior de este archivo), una
    # tarea sin ninguna invocación de herramienta -- el agente que solo
    # responde -- reportaría "0 tokens", y el saludo/prompt inicial de
    # cualquier tarea (eventos "user"/"status" antes del primer "tool")
    # quedaría fuera del total. ``acciones`` sigue existiendo para el
    # desglose POR herramienta, que por definición no cubre texto que no
    # está asociado a ninguna.
    tokens_reales_totales = 0
    tiene_usage_real = False
    tokens_entrada = 0
    tokens_salida = 0
    for evento in events:
        usage = _usage_de_evento(evento)
        if usage is not None:
            tiene_usage_real = True
            tokens_reales_totales += usage.input_tokens + usage.output_tokens
            continue
        tipo = evento.get("type")
        tokens = _tokens_estimados_de_texto(_texto(evento))
        if tipo in _TIPOS_SALIDA_MODELO:
            tokens_salida += tokens
        elif tipo in _TIPOS_ENTRADA_MODELO:
            tokens_entrada += tokens

    if tiene_usage_real:
        tokens_totales = tokens_reales_totales
        # La separación entrada/salida real no viene en el evento "usage"
        # tal como se agrega hoy (es un total por ronda, no por bloque de
        # texto), así que se conserva la FORMA de la estimación por tipo de
        # evento pero escalada para que sume el total real conocido. Es
        # mejor una proporción aproximada que fingir una separación exacta
        # que el dato de origen no ofrece.
        tokens_estimados_total = tokens_entrada + tokens_salida
        if tokens_estimados_total > 0:
            factor = tokens_totales / tokens_estimados_total
            tokens_entrada = round(tokens_entrada * factor)
            tokens_salida = tokens_totales - tokens_entrada
        else:
            tokens_entrada = tokens_totales
            tokens_salida = 0
    else:
        tokens_totales = tokens_entrada + tokens_salida

    total_acciones = len(acciones)
    total_salidas = sum(1 for ev in events if ev.get("type") == "output")

    costo_usd: float | None = None
    if modelo:
        tabla = tabla_precios if tabla_precios is not None else COSTOS
        if modelo in tabla:
            uso = Usage(input_tokens=tokens_entrada, output_tokens=tokens_salida)
            costo_usd = _estimate_usd(modelo, uso, costos=tabla)
        else:
            advertencias.append(
                f"No se conoce el precio del modelo '{modelo}'; no se estima costo en USD."
            )
    else:
        advertencias.append("No se indicó el modelo; no se estima costo en USD.")

    if not tiene_usage_real and events:
        advertencias.append(
            "Los tokens son estimados (~4 caracteres por token); esta sesión no "
            "registró eventos 'usage' con el conteo real del proveedor."
        )

    por_herramienta: list[DesgloseHerramienta] = []
    nombres_ordenados = sorted({a.nombre for a in acciones})
    total_tokens_para_pct = sum(a.tokens_reales or a.tokens_estimados for a in acciones) or 1
    total_segundos_para_pct = sum(a.segundos for a in acciones) or 1.0
    for nombre in nombres_ordenados:
        del_tool = [a for a in acciones if a.nombre == nombre]
        hay_real = any(a.tokens_reales is not None for a in del_tool)
        tokens_reales_tool = sum(a.tokens_reales or 0 for a in del_tool) if hay_real else None
        tokens_est_tool = sum(a.tokens_estimados for a in del_tool)
        segundos_tool = sum(a.segundos for a in del_tool)
        tokens_para_pct = tokens_reales_tool if tokens_reales_tool is not None else tokens_est_tool
        por_herramienta.append(
            DesgloseHerramienta(
                nombre=nombre,
                acciones=len(del_tool),
                tokens_estimados=tokens_est_tool,
                tokens_reales=tokens_reales_tool,
                segundos=segundos_tool,
                porcentaje_tokens=100.0 * tokens_para_pct / total_tokens_para_pct,
                porcentaje_tiempo=100.0 * segundos_tool / total_segundos_para_pct,
            )
        )
    def _clave_orden(item: DesgloseHerramienta) -> int:
        return item.tokens_estimados if item.tokens_reales is None else item.tokens_reales

    por_herramienta.sort(key=_clave_orden, reverse=True)

    bucles = _detectar_bucles(acciones, min_repeticiones=min_repeticiones_bucle)

    comparacion = _comparar_con_historial(
        duracion_segundos=duracion_segundos,
        total_acciones=total_acciones,
        tokens_totales=tokens_totales,
        history=history,
        umbral_factor=umbral_factor_anormal,
        min_tareas=min_tareas_para_comparar,
    )
    if comparacion is None and history is not None and len(history) < min_tareas_para_comparar:
        advertencias.append(
            f"Solo hay {len(history)} tarea(s) previa(s) parecida(s); hacen falta al menos "
            f"{min_tareas_para_comparar} para comparar de forma confiable."
        )

    return TaskCost(
        total_acciones=total_acciones,
        total_eventos=len(events),
        total_salidas=total_salidas,
        duracion_segundos=duracion_segundos,
        tokens_entrada=tokens_entrada,
        tokens_salida=tokens_salida,
        tokens_totales=tokens_totales,
        tokens_son_estimados=not tiene_usage_real,
        costo_usd=costo_usd,
        modelo=modelo,
        por_herramienta=por_herramienta,
        bucles=bucles,
        comparacion=comparacion,
        advertencias=advertencias,
    )


def resumen_tarea(events: Sequence[Mapping[str, Any]], **kwargs: Any) -> dict[str, Any]:
    """Atajo para la UI: ``analizar_tarea(...).resumen()`` en una llamada."""
    return analizar_tarea(events, **kwargs).resumen()
