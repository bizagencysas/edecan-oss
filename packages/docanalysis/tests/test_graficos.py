"""Tests de `edecan_docanalysis.graficos` (`generar_grafico`).

`test_*_coincide_con_snapshot` compara el SVG generado contra un archivo en
`tests/fixtures/` capturado de una corrida real de la implementación —
"reproducible byte a byte" (docstring del módulo) significa exactamente que
esta comparación de igualdad de string nunca debería fallar entre corridas.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from edecan_docanalysis import graficos as graficos_modulo
from edecan_docanalysis.graficos import GenerarGraficoTool

_FIXTURES = Path(__file__).parent / "fixtures"


def _leer_fixture(nombre: str) -> str:
    return (_FIXTURES / nombre).read_text(encoding="utf-8")


def _sin_declaracion_xml(svg: str) -> str:
    return svg.split("\n", 1)[1]


# ---------------------------------------------------------------------------
# Snapshots deterministas
# ---------------------------------------------------------------------------


def test_barras_coincide_con_snapshot_y_es_xml_valido():
    svg = graficos_modulo._grafico_barras("Ventas 2026", ["Ene", "Feb", "Mar"], [10.0, 25.0, 15.0])
    assert svg == _leer_fixture("grafico_barras.svg")
    raiz = ET.fromstring(_sin_declaracion_xml(svg))
    assert raiz.tag == "{http://www.w3.org/2000/svg}svg"
    assert len(raiz.findall("{http://www.w3.org/2000/svg}rect")) == 1 + 3  # fondo + 3 barras


def test_lineas_coincide_con_snapshot_y_es_xml_valido():
    svg = graficos_modulo._grafico_lineas(
        "Tráfico web",
        ["Lun", "Mar", "Mié", "Jue"],
        [("Visitas", [100.0, 150.0, 120.0, 200.0]), ("Registros", [10.0, 20.0, 15.0, 25.0])],
    )
    assert svg == _leer_fixture("grafico_lineas.svg")
    raiz = ET.fromstring(_sin_declaracion_xml(svg))
    assert len(raiz.findall("{http://www.w3.org/2000/svg}polyline")) == 2  # una por serie


def test_dona_coincide_con_snapshot_y_es_xml_valido():
    svg = graficos_modulo._grafico_dona(
        "Presupuesto", ["Renta", "Comida", "Ahorro"], [50.0, 30.0, 20.0]
    )
    assert svg == _leer_fixture("grafico_dona.svg")
    raiz = ET.fromstring(_sin_declaracion_xml(svg))
    # 1 círculo de fondo (riel gris) + 1 por porción con valor > 0
    circulos = raiz.findall("{http://www.w3.org/2000/svg}circle")
    assert len(circulos) == 1 + 3


def test_misma_entrada_produce_el_mismo_svg_byte_a_byte():
    args = ("Ventas 2026", ["Ene", "Feb", "Mar"], [10.0, 25.0, 15.0])
    assert graficos_modulo._grafico_barras(*args) == graficos_modulo._grafico_barras(*args)


def test_dona_con_una_porcion_en_cero_no_dibuja_esa_porcion_pero_si_su_leyenda():
    svg = graficos_modulo._grafico_dona("X", ["A", "B"], [100.0, 0.0])
    raiz = ET.fromstring(_sin_declaracion_xml(svg))
    circulos = raiz.findall("{http://www.w3.org/2000/svg}circle")
    assert len(circulos) == 1 + 1  # fondo + solo la porción A (B es 0)
    assert "B (0.00%)" in svg


# ---------------------------------------------------------------------------
# La tool completa (validación + subida a S3)
# ---------------------------------------------------------------------------


async def test_tipo_invalido(make_ctx, fake_s3):
    resultado = await GenerarGraficoTool().run(
        make_ctx(), {"tipo": "torta", "titulo": "X", "etiquetas": ["a"]}
    )
    assert "'tipo' debe ser uno de" in resultado.content
    assert fake_s3.subidas == []


async def test_sin_etiquetas(make_ctx, fake_s3):
    resultado = await GenerarGraficoTool().run(
        make_ctx(), {"tipo": "barras", "titulo": "X", "etiquetas": []}
    )
    assert "al menos una etiqueta" in resultado.content


async def test_demasiadas_etiquetas(make_ctx, fake_s3):
    etiquetas = [f"e{i}" for i in range(21)]
    resultado = await GenerarGraficoTool().run(
        make_ctx(), {"tipo": "barras", "titulo": "X", "etiquetas": etiquetas, "valores": [1] * 21}
    )
    assert "Demasiadas etiquetas" in resultado.content


async def test_valores_no_coinciden_en_longitud(make_ctx, fake_s3):
    resultado = await GenerarGraficoTool().run(
        make_ctx(),
        {"tipo": "barras", "titulo": "X", "etiquetas": ["a", "b"], "valores": [1.0]},
    )
    assert "'valores' debe tener exactamente 2" in resultado.content


async def test_valores_negativos_se_rechazan(make_ctx, fake_s3):
    resultado = await GenerarGraficoTool().run(
        make_ctx(),
        {"tipo": "barras", "titulo": "X", "etiquetas": ["a"], "valores": [-1.0]},
    )
    assert "no se soportan valores negativos" in resultado.content


async def test_dona_con_suma_cero_se_rechaza(make_ctx, fake_s3):
    resultado = await GenerarGraficoTool().run(
        make_ctx(),
        {"tipo": "dona", "titulo": "X", "etiquetas": ["a", "b"], "valores": [0.0, 0.0]},
    )
    assert "suma de los valores sea mayor que 0" in resultado.content


async def test_barras_sube_svg_a_s3_con_mime_y_filename_correctos(make_ctx, fake_s3):
    resultado = await GenerarGraficoTool().run(
        make_ctx(),
        {
            "tipo": "barras",
            "titulo": "Ventas Q1 2026!",
            "etiquetas": ["Ene", "Feb"],
            "valores": [1.0, 2.0],
        },
    )

    assert len(fake_s3.subidas) == 1
    subida = fake_s3.subidas[0]
    assert subida["mime"] == "image/svg+xml"
    assert subida["filename"] == "ventas-q1-2026-barras.svg"
    assert subida["contenido"].startswith(b"<?xml")
    assert resultado.data["filename"] == "ventas-q1-2026-barras.svg"
    assert resultado.data["file_id"] == str(subida["file_id"])


async def test_lineas_con_solo_valores_equivale_a_una_serie(make_ctx, fake_s3):
    resultado = await GenerarGraficoTool().run(
        make_ctx(),
        {"tipo": "lineas", "titulo": "X", "etiquetas": ["a", "b"], "valores": [1.0, 2.0]},
    )
    assert "Generé el gráfico de lineas" in resultado.content
    svg = fake_s3.subidas[0]["contenido"].decode("utf-8")
    assert "<polyline" in svg
    assert svg.count("<polyline") == 1


async def test_lineas_con_series_multiples(make_ctx, fake_s3):
    resultado = await GenerarGraficoTool().run(
        make_ctx(),
        {
            "tipo": "lineas",
            "titulo": "X",
            "etiquetas": ["a", "b"],
            "series": [
                {"nombre": "S1", "valores": [1.0, 2.0]},
                {"nombre": "S2", "valores": [3.0, 4.0]},
            ],
        },
    )
    assert resultado.data["filename"].endswith("-lineas.svg")
    svg = fake_s3.subidas[0]["contenido"].decode("utf-8")
    assert svg.count("<polyline") == 2
    assert "S1" in svg and "S2" in svg


async def test_lineas_serie_con_longitud_incorrecta(make_ctx, fake_s3):
    resultado = await GenerarGraficoTool().run(
        make_ctx(),
        {
            "tipo": "lineas",
            "titulo": "X",
            "etiquetas": ["a", "b"],
            "series": [{"nombre": "S1", "valores": [1.0]}],
        },
    )
    assert "Serie «S1»" in resultado.content
    assert fake_s3.subidas == []


async def test_dona_reporta_file_id_de_la_subida(make_ctx, fake_s3):
    resultado = await GenerarGraficoTool().run(
        make_ctx(),
        {"tipo": "dona", "titulo": "Presupuesto", "etiquetas": ["A", "B"], "valores": [1.0, 1.0]},
    )
    assert resultado.data["file_id"] == str(fake_s3.subidas[0]["file_id"])


# ---------------------------------------------------------------------------
# `generar_svg` — superficie pública pura (WP-V6-06, sin S3 ni ToolContext)
# ---------------------------------------------------------------------------


def test_generar_svg_barras_coincide_byte_a_byte_con_el_render_privado():
    from edecan_docanalysis import generar_svg

    esperado = graficos_modulo._grafico_barras(
        "Ventas 2026", ["Ene", "Feb", "Mar"], [10.0, 25.0, 15.0]
    )
    obtenido = generar_svg(
        "barras", "Ventas 2026", ["Ene", "Feb", "Mar"], valores=[10.0, 25.0, 15.0]
    )
    assert obtenido == esperado


def test_generar_svg_lineas_una_sola_serie_via_valores():
    from edecan_docanalysis import generar_svg

    esperado = graficos_modulo._grafico_lineas(
        "Tráfico", ["Lun", "Mar"], [("Serie 1", [1.0, 2.0])]
    )
    obtenido = generar_svg("lineas", "Tráfico", ["Lun", "Mar"], valores=[1.0, 2.0])
    assert obtenido == esperado


def test_generar_svg_dona_coincide_byte_a_byte():
    from edecan_docanalysis import generar_svg

    esperado = graficos_modulo._grafico_dona("Presupuesto", ["Renta", "Comida"], [50.0, 30.0])
    obtenido = generar_svg("dona", "Presupuesto", ["Renta", "Comida"], valores=[50.0, 30.0])
    assert obtenido == esperado


def test_generar_svg_nunca_sube_nada_a_s3_ni_toca_un_ctx():
    """No recibe `ctx` en absoluto — a diferencia de `GenerarGraficoTool.run()`, no hay forma
    de que `generar_svg` intente `_s3.subir_resultado` (ni siquiera importa `_s3`)."""
    import inspect

    from edecan_docanalysis import graficos as modulo

    firma = inspect.signature(modulo.generar_svg)
    assert "ctx" not in firma.parameters


def test_generar_svg_tipo_invalido_lanza_valueerror_en_espanol():
    from edecan_docanalysis import generar_svg

    with pytest.raises(ValueError, match="'tipo' debe ser uno de"):
        generar_svg("torta", "X", ["a"], valores=[1.0])


def test_generar_svg_sin_etiquetas_lanza_valueerror():
    from edecan_docanalysis import generar_svg

    with pytest.raises(ValueError, match="al menos una etiqueta"):
        generar_svg("barras", "X", [], valores=[])


def test_generar_svg_valores_negativos_lanza_valueerror():
    from edecan_docanalysis import generar_svg

    with pytest.raises(ValueError, match="no se soportan valores negativos"):
        generar_svg("barras", "X", ["a"], valores=[-1.0])


def test_generar_svg_dona_suma_cero_lanza_valueerror():
    from edecan_docanalysis import generar_svg

    with pytest.raises(ValueError, match="mayor que 0"):
        generar_svg("dona", "X", ["a", "b"], valores=[0.0, 0.0])
