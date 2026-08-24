"""Pruebas de ``ide_costos.py``: contabilidad de costo por tarea del IDE.

Cubren, en orden, lo que el archivo promete en su docstring:
- la contabilidad básica (duración, acciones, tokens, desglose por
  herramienta) sobre una serie de eventos realista;
- que tokens/costo usan datos reales cuando hay un evento ``usage``, y caen
  a la heurística por caracteres cuando no lo hay (marcándolo como tal);
- que el costo en USD se omite (no se inventa) cuando no se conoce el precio
  del modelo;
- la comparación contra el historial de tareas parecidas;
- la detección de bucle -- el caso que motivó el archivo -- tanto en su
  forma simple (una herramienta repetida en seco) como en la alternada
  (dos herramientas turnándose sin avanzar), y que una tarea normal, sin
  repetición, no dispara ningún falso positivo.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from edecan_companion.ide_costos import (
    TaskCost,
    analizar_tarea,
    resumen_tarea,
)

_T0 = datetime(2026, 7, 28, 10, 0, 0, tzinfo=UTC)


class _Reloj:
    """Genera timestamps ISO-8601 crecientes, como los que escribe Session.append."""

    def __init__(self, inicio: datetime = _T0) -> None:
        self._actual = inicio

    def tick(self, segundos: float = 1.0) -> str:
        self._actual += timedelta(seconds=segundos)
        return self._actual.isoformat()


def _ev(cursor: int, tipo: str, texto: str, ts: str, stream: str | None = None) -> dict[str, Any]:
    event: dict[str, Any] = {"cursor": cursor, "type": tipo, "text": texto, "timestamp": ts}
    if stream is not None:
        event["stream"] = stream
    return event


def _tarea_simple() -> list[dict[str, Any]]:
    """Una tarea chica y normal: lee un archivo, lo edita, corre un test."""
    reloj = _Reloj()
    cursor = 0
    events: list[dict[str, Any]] = []

    def add(tipo: str, texto: str, segundos: float = 1.0) -> None:
        nonlocal cursor
        cursor += 1
        events.append(_ev(cursor, tipo, texto, reloj.tick(segundos)))

    add("user", "Arregla el typo en el README.")
    add("status", "Agente de Workers AI iniciado.")
    add("tool", "Usando leer_archivo.")
    add("progress", "Voy a revisar el README primero.", segundos=2.0)
    add("tool", "Usando editar_archivo.")
    add("file", "Archivo actualizado: README.md", segundos=1.5)
    add("tool", "Usando ejecutar_comando.")
    add("command", "pytest -q", segundos=0.5)
    add("output", "1 passed in 0.42s", segundos=3.0)
    add("assistant_final", "Corregí el typo y corrí los tests: pasan.")
    add("status", "Trabajo completado.")
    return events


def test_contabilidad_basica_cuenta_acciones_duracion_y_desglose():
    events = _tarea_simple()
    costo = analizar_tarea(events)

    assert costo.total_acciones == 3  # 3 eventos "tool"
    assert costo.total_eventos == len(events)
    assert costo.total_salidas == 1  # 1 evento "output"
    assert costo.duracion_segundos > 0
    assert costo.tokens_totales > 0
    assert costo.tokens_son_estimados is True  # no hay eventos "usage" reales

    nombres = {d.nombre for d in costo.por_herramienta}
    assert nombres == {"leer_archivo", "editar_archivo", "ejecutar_comando"}
    # Las acciones del desglose deben sumar el total de acciones de la tarea.
    assert sum(d.acciones for d in costo.por_herramienta) == costo.total_acciones
    # Los porcentajes de tiempo entre herramientas deben cerrar cerca de 100%.
    assert 99.0 <= sum(d.porcentaje_tiempo for d in costo.por_herramienta) <= 101.0

    assert costo.bucles == []
    assert costo.comparacion is None  # sin historial, no hay con qué comparar


def test_resumen_tarea_es_serializable_y_trae_las_mismas_cifras():
    events = _tarea_simple()
    resumen = resumen_tarea(events)

    assert resumen["total_acciones"] == 3
    assert resumen["tokens"]["estimados"] is True
    assert resumen["tokens"]["total"] > 0
    assert resumen["senales"] == []
    assert isinstance(resumen["duracion_humana"], str)
    # Debe ser JSON-friendly: solo tipos primitivos, listas y dicts.
    import json

    json.dumps(resumen)


def test_tarea_sin_herramientas_no_reporta_cero_tokens():
    """El agente puede responder sin usar ninguna herramienta.

    Antes de la corrección, el total de tokens se calculaba solo a partir
    de los tramos de herramienta, así que una tarea sin ninguna "tool"
    reportaba 0 tokens aunque hubiera un prompt y una respuesta reales.
    """
    reloj = _Reloj()
    events = [
        _ev(1, "user", "¿Qué hace la función _now() en ide_sessions.py?", reloj.tick(1.0)),
        _ev(2, "status", "Agente de Workers AI iniciado.", reloj.tick(1.0)),
        _ev(
            3,
            "assistant_final",
            "Devuelve la hora actual en UTC como texto ISO-8601.",
            reloj.tick(2.0),
        ),
        _ev(4, "status", "Trabajo completado.", reloj.tick(0.1)),
    ]

    costo = analizar_tarea(events)

    assert costo.total_acciones == 0
    assert costo.tokens_totales > 0
    assert costo.por_herramienta == []
    assert costo.duracion_segundos > 0


def test_tokens_reales_se_usan_cuando_hay_evento_usage():
    reloj = _Reloj()
    events = [
        _ev(1, "user", "Suma 2 y 2.", reloj.tick(1.0)),
        _ev(
            2,
            "usage",
            '{"input_tokens": 120, "output_tokens": 30, "cached_input_tokens": 0}',
            reloj.tick(0.5),
        ),
        _ev(3, "assistant_final", "4.", reloj.tick(0.5)),
    ]

    costo = analizar_tarea(events)

    assert costo.tokens_son_estimados is False
    assert costo.tokens_totales == 150
    assert costo.tokens_entrada + costo.tokens_salida == 150


def test_costo_usd_se_omite_si_no_se_conoce_el_precio_del_modelo():
    events = _tarea_simple()

    sin_modelo = analizar_tarea(events)
    assert sin_modelo.costo_usd is None
    assert any("no se indicó el modelo" in a.lower() for a in sin_modelo.advertencias)

    modelo_desconocido = analizar_tarea(events, modelo="@cf/zai-org/glm-5.2")
    assert modelo_desconocido.costo_usd is None
    assert any("no se conoce el precio" in a.lower() for a in modelo_desconocido.advertencias)


def test_costo_usd_se_calcula_con_un_modelo_conocido():
    reloj = _Reloj()
    events = [
        _ev(1, "user", "hola", reloj.tick(1.0)),
        _ev(
            2,
            "usage",
            '{"input_tokens": 1000000, "output_tokens": 1000000, "cached_input_tokens": 0}',
            reloj.tick(0.5),
        ),
        _ev(3, "assistant_final", "hola", reloj.tick(0.5)),
    ]
    # claude-haiku-4-5 en edecan_llm.costs.COSTOS: (0.80, 4.00) USD/MTok.
    costo = analizar_tarea(events, modelo="claude-haiku-4-5")
    assert costo.costo_usd is not None
    assert costo.costo_usd == 0.80 + 4.00


def test_comparacion_marca_fuera_de_lo_normal_cuando_se_dispara_por_mucho():
    events = _tarea_simple()
    historial = [
        {"duracion_segundos": 60.0, "total_acciones": 5, "tokens_totales": 800},
        {"duracion_segundos": 55.0, "total_acciones": 4, "tokens_totales": 750},
        {"duracion_segundos": 62.0, "total_acciones": 6, "tokens_totales": 820},
    ]
    # Fuerza una duración muy por encima de lo típico para probar el disparo,
    # sin depender de cuánto tarde en generarse `_tarea_simple()`.
    costo = analizar_tarea(
        events,
        started_at=_T0.isoformat(),
        ended_at=(_T0 + timedelta(minutes=22)).isoformat(),
        history=historial,
    )

    assert costo.comparacion is not None
    assert costo.comparacion.tareas_comparadas == 3
    assert costo.comparacion.fuera_de_lo_normal is True
    assert "duración" in costo.comparacion.motivo


def test_comparacion_no_marca_nada_cuando_esta_dentro_de_lo_tipico():
    events = _tarea_simple()
    historial = [
        {"duracion_segundos": 9.0, "total_acciones": 3, "tokens_totales": 400},
        {"duracion_segundos": 8.5, "total_acciones": 3, "tokens_totales": 380},
        {"duracion_segundos": 10.0, "total_acciones": 3, "tokens_totales": 420},
    ]
    costo = analizar_tarea(events, history=historial)
    assert costo.comparacion is not None
    assert costo.comparacion.fuera_de_lo_normal is False


def test_comparacion_se_omite_con_historial_insuficiente():
    events = _tarea_simple()
    historial = [{"duracion_segundos": 9.0, "total_acciones": 3, "tokens_totales": 400}]
    costo = analizar_tarea(events, history=historial)
    assert costo.comparacion is None
    assert any("hacen falta al menos" in a for a in costo.advertencias)


def test_task_cost_como_historial_de_si_mismo():
    """El historial también acepta objetos ``TaskCost``, no solo dicts."""
    base = analizar_tarea(_tarea_simple())
    historial: list[TaskCost] = [base, base, base]
    otra = analizar_tarea(_tarea_simple(), history=historial)
    assert otra.comparacion is not None
    assert otra.comparacion.tareas_comparadas == 3


def _tarea_con_bucle_simple() -> list[dict[str, Any]]:
    """El caso literal del incidente: la misma edición fallando una y otra vez.

    ``editar_archivo`` se repite 6 veces sobre el mismo archivo sin que
    ningún intento aparezca como distinto (mismo ``path`` en el evento
    ``file``), y sin que aparezca ningún evento adicional entre medio que
    indique una verificación -- es la firma exacta del bucle que costó los
    22 minutos.
    """
    reloj = _Reloj()
    cursor = 0
    events: list[dict[str, Any]] = []

    def add(tipo: str, texto: str, segundos: float = 1.0) -> None:
        nonlocal cursor
        cursor += 1
        events.append(_ev(cursor, tipo, texto, reloj.tick(segundos)))

    add("user", "Corrige el formato del README.")
    add("tool", "Usando leer_archivo.")
    for _ in range(6):
        add("tool", "Usando editar_archivo.")
        add("error", "El texto anterior no aparece en el archivo.", segundos=2.0)
    add("assistant_final", "No logré aplicar el cambio.")
    return events


def test_detecta_bucle_de_periodo_1_con_la_misma_herramienta_repetida():
    events = _tarea_con_bucle_simple()
    costo = analizar_tarea(events)

    assert len(costo.bucles) == 1
    bucle = costo.bucles[0]
    assert bucle.periodo == 1
    assert bucle.herramientas == ("editar_archivo",)
    assert bucle.repeticiones_del_ciclo == 6
    assert bucle.hubo_cambios_en_el_tramo is False  # nunca hubo un evento "file"
    assert "bucle_detectado" in costo.resumen()["senales"]


def _tarea_con_bucle_alternado() -> list[dict[str, Any]]:
    """Ping-pong: intenta editar, lee para verificar, falla, repite igual."""
    reloj = _Reloj()
    cursor = 0
    events: list[dict[str, Any]] = []

    def add(tipo: str, texto: str, segundos: float = 1.0) -> None:
        nonlocal cursor
        cursor += 1
        events.append(_ev(cursor, tipo, texto, reloj.tick(segundos)))

    add("user", "Actualiza la versión en package.json.")
    for _ in range(4):
        add("tool", "Usando editar_archivo.")
        add("error", "El texto anterior no aparece en el archivo.", segundos=1.0)
        add("tool", "Usando leer_archivo.")
    add("assistant_final", "No pude aplicar el cambio tras varios intentos.")
    return events


def test_detecta_bucle_de_periodo_2_alternando_dos_herramientas():
    events = _tarea_con_bucle_alternado()
    costo = analizar_tarea(events)

    assert len(costo.bucles) == 1
    bucle = costo.bucles[0]
    assert bucle.periodo == 2
    assert bucle.herramientas == ("editar_archivo", "leer_archivo")
    assert bucle.repeticiones_del_ciclo == 4


def test_tarea_normal_sin_repeticion_no_dispara_falso_positivo_de_bucle():
    events = _tarea_simple()
    costo = analizar_tarea(events)
    assert costo.bucles == []


def test_repeticiones_por_debajo_del_umbral_no_cuentan_como_bucle():
    """Repetir una herramienta 2 veces es normal (leer, corregir, releer)."""
    reloj = _Reloj()
    events = [
        _ev(1, "user", "Revisa el archivo dos veces.", reloj.tick(1.0)),
        _ev(2, "tool", "Usando leer_archivo.", reloj.tick(1.0)),
        _ev(3, "tool", "Usando leer_archivo.", reloj.tick(1.0)),
        _ev(4, "assistant_final", "Ya lo revisé.", reloj.tick(1.0)),
    ]
    costo = analizar_tarea(events)
    assert costo.bucles == []


def test_detalle_de_tramo_distingue_ediciones_a_archivos_distintos():
    """Editar A y luego B, alternando, no debería verse como un bucle real.

    La firma incluye el detalle (la ruta que deja el evento "file"), así que
    3 ediciones a rutas DISTINTAS con la misma herramienta no cuentan como
    la misma firma repetida.
    """
    reloj = _Reloj()
    cursor = 0
    events: list[dict[str, Any]] = []

    def add(tipo: str, texto: str, segundos: float = 1.0) -> None:
        nonlocal cursor
        cursor += 1
        events.append(_ev(cursor, tipo, texto, reloj.tick(segundos)))

    add("user", "Actualiza tres archivos.")
    for nombre in ("a.py", "b.py", "c.py"):
        add("tool", "Usando editar_archivo.")
        add("file", f"Archivo actualizado: {nombre}", segundos=1.0)
    add("assistant_final", "Listo, actualicé los tres archivos.")

    costo = analizar_tarea(events)
    assert costo.bucles == []


def test_bucle_con_cambios_reales_se_marca_pero_distinto_a_uno_sin_avance():
    """Repetir 'escribir_archivo' sobre el MISMO archivo sí deja rastro de bucle,
    pero si en el tramo hubo eventos 'file' el flag hubo_cambios_en_el_tramo
    debe reflejarlo -- no es lo mismo bucle destructivo que bucle que sí avanzó.
    """
    reloj = _Reloj()
    cursor = 0
    events: list[dict[str, Any]] = []

    def add(tipo: str, texto: str, segundos: float = 1.0) -> None:
        nonlocal cursor
        cursor += 1
        events.append(_ev(cursor, tipo, texto, reloj.tick(segundos)))

    add("user", "Reescribe el archivo hasta que quede bien.")
    for _ in range(4):
        add("tool", "Usando escribir_archivo.")
        add("file", "Archivo actualizado: notas.md", segundos=1.0)
    add("assistant_final", "Reescribí el archivo varias veces hasta ajustarlo.")

    costo = analizar_tarea(events)
    assert len(costo.bucles) == 1
    assert costo.bucles[0].hubo_cambios_en_el_tramo is True


def test_evento_sin_timestamp_valido_no_revienta_el_calculo():
    events = [
        {"cursor": 1, "type": "user", "text": "hola", "timestamp": "no-es-una-fecha"},
        {"cursor": 2, "type": "assistant_final", "text": "hola", "timestamp": "tampoco"},
    ]
    costo = analizar_tarea(events)
    assert costo.duracion_segundos == 0.0
    assert any("duración" in a.lower() for a in costo.advertencias)


def test_lista_de_eventos_vacia_no_revienta():
    costo = analizar_tarea([])
    assert costo.total_acciones == 0
    assert costo.total_eventos == 0
    assert costo.duracion_segundos == 0.0
    assert costo.tokens_totales == 0
    assert costo.bucles == []
    assert any("no tiene eventos" in a for a in costo.advertencias)


def test_nombre_de_herramienta_con_texto_estructurado_tiene_prioridad():
    """Si el evento ya trae ``tool`` como campo, se usa en vez de parsear el texto."""
    reloj = _Reloj()
    events = [
        _ev(1, "user", "hola", reloj.tick(1.0)),
        {
            "cursor": 2,
            "type": "tool",
            "text": "Usando algo raro sin el formato esperado",
            "tool": "editar_archivo",
            "timestamp": reloj.tick(1.0),
        },
        _ev(3, "assistant_final", "listo", reloj.tick(1.0)),
    ]
    costo = analizar_tarea(events)
    assert costo.por_herramienta[0].nombre == "editar_archivo"
