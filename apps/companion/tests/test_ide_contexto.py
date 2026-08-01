"""Pruebas de ``ide_contexto.py``: ``/context``, ``/compact`` y ``/btw``.

Cubren, en orden, lo que el archivo promete en su docstring:
- ``leer_bitacora`` lee el mismo esquema JSONL que escribe
  ``edecan_core.llm_call_log.log_llm_call``, con sus filtros y su tolerancia
  a archivos ausentes/corruptos;
- ``analizar_contexto`` REUSA los tokens ya registrados (nunca los cuenta
  por caracteres), calcula el porcentaje contra la ventana del modelo, y
  atribuye el crecimiento entre llamadas a las herramientas de la llamada
  anterior;
- ``compactar`` conserva pedidos/decisiones/archivos verbatim y descarta el
  resto como relleno (solo como conteo, nunca su texto);
- ``preparar_contexto_btw`` nunca muta el historial ni permite escribirlo.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest
from edecan_companion.ide_contexto import (
    ContextoSoloLectura,
    IDEContextoError,
    analizar_contexto,
    compactar,
    leer_bitacora,
    preparar_contexto_btw,
)

_MODELOS_TEST: list[dict[str, Any]] = [
    {"id": "modelo-chico", "contexto_ventana": 1000},
    {"id": "modelo-grande", "contexto_ventana": 100_000},
]


def _registro(
    *,
    ts: str,
    input_tokens: int,
    output_tokens: int,
    modelo: str = "modelo-grande",
    iteration: int = 1,
    tools_requested: list[str] | None = None,
    tenant_id: str = "t1",
    user_id: str = "u1",
) -> dict[str, Any]:
    return {
        "ts": ts,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "provider": "workers_ai",
        "model": modelo,
        "iteration": iteration,
        "duration_ms": 100,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "system_preview": "eres edecán",
        "messages_preview": [{"role": "user", "content_preview": "hola"}],
        "tools_offered": ["leer_archivo", "editar_archivo"],
        "tools_requested": tools_requested or [],
        "response_text_preview": "listo",
    }


# --------------------------------------------------------------------- #
# leer_bitacora
# --------------------------------------------------------------------- #


def test_leer_bitacora_archivo_ausente_devuelve_lista_vacia(tmp_path: Path):
    assert leer_bitacora(tmp_path) == []


def test_leer_bitacora_lee_lineas_validas_y_salta_las_corruptas(tmp_path: Path):
    path = tmp_path / "llm-calls.jsonl"
    buenas = [_registro(ts="2026-07-28T10:00:00+00:00", input_tokens=100, output_tokens=10)]
    with path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(buenas[0]) + "\n")
        handle.write("esto no es json\n")
        handle.write("\n")  # línea vacía
        handle.write(json.dumps(["no es un dict"]) + "\n")

    registros = leer_bitacora(tmp_path)
    assert registros == buenas


def test_leer_bitacora_filtra_por_tenant_usuario_y_fecha(tmp_path: Path):
    path = tmp_path / "llm-calls.jsonl"
    filas = [
        _registro(ts="2026-07-28T10:00:00+00:00", input_tokens=1, output_tokens=1, tenant_id="a"),
        _registro(ts="2026-07-28T11:00:00+00:00", input_tokens=2, output_tokens=1, tenant_id="b"),
        _registro(ts="2026-07-28T09:00:00+00:00", input_tokens=3, output_tokens=1, tenant_id="a"),
    ]
    with path.open("w", encoding="utf-8") as handle:
        for fila in filas:
            handle.write(json.dumps(fila) + "\n")

    solo_a = leer_bitacora(tmp_path, tenant_id="a")
    assert [f["input_tokens"] for f in solo_a] == [1, 3]

    solo_a_desde_10 = leer_bitacora(tmp_path, tenant_id="a", desde_ts="2026-07-28T09:30:00+00:00")
    assert [f["input_tokens"] for f in solo_a_desde_10] == [1]


# --------------------------------------------------------------------- #
# analizar_contexto
# --------------------------------------------------------------------- #


def test_analizar_contexto_sin_llamadas_no_revienta_y_avisa():
    resultado = analizar_contexto([], modelos=_MODELOS_TEST)
    assert resultado.total_llamadas == 0
    assert resultado.tokens_totales_actual == 0
    assert resultado.porcentaje_usado is None
    assert any("no hay llamadas" in a.lower() for a in resultado.advertencias)


def test_analizar_contexto_reusa_tokens_registrados_sin_recontar():
    # Los textos de la bitácora (previews) no tienen nada que ver con el
    # número real de tokens -- si el módulo recontara por caracteres, este
    # número no cuadraría con lo que se afirma abajo.
    llamadas = [_registro(ts="2026-07-28T10:00:00+00:00", input_tokens=500, output_tokens=50)]
    resultado = analizar_contexto(llamadas, modelos=_MODELOS_TEST)
    assert resultado.tokens_entrada_actual == 500
    assert resultado.tokens_salida_actual == 50
    assert resultado.tokens_totales_actual == 550


def test_analizar_contexto_calcula_porcentaje_contra_la_ventana_del_modelo():
    llamadas = [
        _registro(
            ts="2026-07-28T10:00:00+00:00",
            input_tokens=400,
            output_tokens=100,
            modelo="modelo-chico",
        )
    ]
    resultado = analizar_contexto(llamadas, modelos=_MODELOS_TEST)
    assert resultado.ventana_maxima == 1000
    assert resultado.porcentaje_usado == pytest.approx(50.0)
    assert resultado.cerca_del_limite is False


def test_analizar_contexto_avisa_cerca_del_limite():
    llamadas = [
        _registro(
            ts="2026-07-28T10:00:00+00:00",
            input_tokens=800,
            output_tokens=100,
            modelo="modelo-chico",
        )
    ]
    resultado = analizar_contexto(llamadas, modelos=_MODELOS_TEST)
    assert resultado.porcentaje_usado == pytest.approx(90.0)
    assert resultado.cerca_del_limite is True
    assert any("umbral de alerta" in a for a in resultado.advertencias)


def test_analizar_contexto_modelo_desconocido_no_calcula_porcentaje():
    llamadas = [
        _registro(
            ts="2026-07-28T10:00:00+00:00",
            input_tokens=400,
            output_tokens=100,
            modelo="modelo-fantasma",
        )
    ]
    resultado = analizar_contexto(llamadas, modelos=_MODELOS_TEST)
    assert resultado.ventana_maxima is None
    assert resultado.porcentaje_usado is None
    assert any("no se conoce la ventana" in a.lower() for a in resultado.advertencias)


def test_analizar_contexto_atribuye_el_crecimiento_a_la_herramienta_previa():
    llamadas = [
        _registro(
            ts="2026-07-28T10:00:00+00:00",
            input_tokens=100,
            output_tokens=20,
            iteration=1,
            tools_requested=["leer_archivo"],
        ),
        _registro(
            ts="2026-07-28T10:00:05+00:00",
            input_tokens=500,  # 100 + 20 esperado; 380 de más -> leer_archivo
            output_tokens=15,
            iteration=2,
            tools_requested=["editar_archivo"],
        ),
        _registro(
            ts="2026-07-28T10:00:10+00:00",
            input_tokens=535,  # 500 + 15 esperado, casi sin sorpresa
            output_tokens=10,
            iteration=3,
            tools_requested=[],
        ),
    ]
    resultado = analizar_contexto(llamadas, modelos=_MODELOS_TEST)
    assert len(resultado.crecimiento) == 3

    linea_base = resultado.crecimiento[0]
    assert linea_base.es_linea_base is True
    assert linea_base.delta_tokens == 100
    assert linea_base.herramientas == ()

    segunda = resultado.crecimiento[1]
    assert segunda.delta_tokens == 500 - (100 + 20)
    assert segunda.herramientas == ("leer_archivo",)

    tercera = resultado.crecimiento[2]
    assert tercera.delta_tokens == 535 - (500 + 15)
    assert tercera.herramientas == ("editar_archivo",)

    assert resultado.mayor_incremento is segunda


def test_analizar_contexto_sin_crecimiento_positivo_no_senala_mayor_incremento():
    llamadas = [
        _registro(ts="2026-07-28T10:00:00+00:00", input_tokens=100, output_tokens=20),
        _registro(ts="2026-07-28T10:00:05+00:00", input_tokens=110, output_tokens=10),
    ]
    resultado = analizar_contexto(llamadas, modelos=_MODELOS_TEST)
    # 110 - (100+20) = -10: no hubo sorpresa, solo la charla normal.
    assert resultado.mayor_incremento is None


def test_analizar_contexto_avisa_si_hay_mas_de_un_modelo():
    llamadas = [
        _registro(
            ts="2026-07-28T10:00:00+00:00",
            input_tokens=100,
            output_tokens=20,
            modelo="modelo-chico",
        ),
        _registro(
            ts="2026-07-28T10:00:05+00:00",
            input_tokens=200,
            output_tokens=20,
            modelo="modelo-grande",
        ),
    ]
    resultado = analizar_contexto(llamadas, modelos=_MODELOS_TEST)
    assert resultado.modelo == "modelo-grande"  # el de la última llamada
    assert any("más de un modelo" in a for a in resultado.advertencias)


# --------------------------------------------------------------------- #
# compactar
# --------------------------------------------------------------------- #


def _evento(cursor: int, tipo: str, texto: str) -> dict[str, Any]:
    ts = f"2026-07-28T10:00:{cursor:02d}+00:00"
    return {"cursor": cursor, "type": tipo, "text": texto, "timestamp": ts}


def _conversacion_tipica() -> list[dict[str, Any]]:
    return [
        _evento(1, "user", "Arregla el typo en el README."),
        _evento(2, "status", "Agente iniciado."),
        _evento(3, "tool", "Usando leer_archivo."),
        _evento(4, "progress", "Voy a revisar el README primero."),
        _evento(5, "tool", "Usando editar_archivo."),
        _evento(6, "file", "Archivo actualizado: README.md"),
        _evento(7, "tool", "Usando ejecutar_comando."),
        _evento(8, "command", "pytest -q"),
        _evento(9, "output", "1 passed in 0.42s"),
        _evento(10, "assistant_final", "Corregí el typo y corrí los tests: pasan."),
    ]


def test_compactar_conserva_pedidos_decisiones_y_archivos_verbatim():
    resumen = compactar(_conversacion_tipica())
    assert resumen.pedidos == ("Arregla el typo en el README.",)
    assert resumen.decisiones == ("Corregí el typo y corrí los tests: pasan.",)
    assert resumen.archivos_tocados == ("README.md",)
    assert "Arregla el typo en el README." in resumen.resumen_texto
    assert "Corregí el typo y corrí los tests: pasan." in resumen.resumen_texto
    assert "README.md" in resumen.resumen_texto


def test_compactar_descarta_el_relleno_solo_como_conteo_no_como_texto():
    eventos = _conversacion_tipica()
    resumen = compactar(eventos)
    # El texto crudo del relleno NUNCA debe sobrevivir al resumen.
    assert "pytest -q" not in resumen.resumen_texto
    assert "1 passed in 0.42s" not in resumen.resumen_texto
    assert "Voy a revisar el README primero." not in resumen.resumen_texto
    assert "Usando leer_archivo." not in resumen.resumen_texto
    # Pero sí queda contado: status(1) + tool(3) + progress(1) + command(1) + output(1) = 7.
    assert resumen.eventos_descartados == 7
    assert resumen.descartados_por_tipo == {
        "status": 1,
        "tool": 3,
        "progress": 1,
        "command": 1,
        "output": 1,
    }
    assert "Se descartó como relleno" in resumen.resumen_texto


def test_compactar_objetivo_pendiente_si_la_conversacion_quedo_sin_responder():
    eventos = _conversacion_tipica()[:5]  # corta antes del assistant_final
    resumen = compactar(eventos)
    assert resumen.objetivo_pendiente == "Arregla el typo en el README."


def test_compactar_objetivo_pendiente_ninguno_si_ya_se_respondio():
    resumen = compactar(_conversacion_tipica())
    assert resumen.objetivo_pendiente is None
    assert "ninguno detectado" in resumen.resumen_texto


def test_compactar_objetivo_pendiente_explicito_gana_sobre_la_derivacion():
    resumen = compactar(_conversacion_tipica(), objetivo_pendiente="Meta explícita del plan activo")
    assert resumen.objetivo_pendiente == "Meta explícita del plan activo"


def test_compactar_dedup_archivos_conserva_el_primer_orden_visto():
    eventos = [
        _evento(1, "file", "Archivo actualizado: a.py"),
        _evento(2, "file", "Archivo actualizado: b.py"),
        _evento(3, "file", "Archivo actualizado: a.py"),
    ]
    resumen = compactar(eventos)
    assert resumen.archivos_tocados == ("a.py", "b.py")


def test_compactar_respeta_los_topes_de_pedidos_y_decisiones():
    eventos = []
    cursor = 0
    for i in range(5):
        cursor += 1
        eventos.append(_evento(cursor, "user", f"pedido {i}"))
        cursor += 1
        eventos.append(_evento(cursor, "assistant_final", f"decision {i}"))
    resumen = compactar(eventos, max_pedidos=2, max_decisiones=2)
    assert resumen.pedidos == ("pedido 3", "pedido 4")
    assert resumen.decisiones == ("decision 3", "decision 4")


def test_compactar_acepta_un_deque_como_events():
    # Session.events real es un collections.deque, no una lista (ver
    # test_piezas_ide_integrables.py) -- esta función solo debe iterarlo.
    eventos = deque(_conversacion_tipica())
    resumen = compactar(eventos)
    assert resumen.archivos_tocados == ("README.md",)


# --------------------------------------------------------------------- #
# preparar_contexto_btw
# --------------------------------------------------------------------- #


def test_preparar_contexto_btw_rechaza_pregunta_vacia():
    with pytest.raises(IDEContextoError):
        preparar_contexto_btw(_conversacion_tipica(), "   ")


def test_preparar_contexto_btw_no_muta_los_eventos_de_entrada():
    eventos = _conversacion_tipica()
    copia = [dict(e) for e in eventos]
    preparar_contexto_btw(eventos, "¿qué hace /rewind?")
    assert eventos == copia


def test_preparar_contexto_btw_ve_decisiones_y_archivos_pero_no_arrastra_pedidos():
    contexto = preparar_contexto_btw(_conversacion_tipica(), "¿qué hace /rewind?")
    assert contexto.pregunta == "¿qué hace /rewind?"
    assert contexto.decisiones_recientes == ("Corregí el typo y corrí los tests: pasan.",)
    assert contexto.archivos_tocados == ("README.md",)


def test_contexto_solo_lectura_es_inmutable():
    contexto = preparar_contexto_btw(_conversacion_tipica(), "una pregunta")
    assert isinstance(contexto, ContextoSoloLectura)
    with pytest.raises(FrozenInstanceError):
        contexto.pregunta = "otra cosa"  # type: ignore[misc]
