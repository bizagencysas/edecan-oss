"""Contrato de los bloques ricos del IDE (`edecan_schemas.ide_blocks`)."""

from __future__ import annotations

import json

import pytest
from edecan_schemas.ide_blocks import (
    MAX_TABLE_CELL_CHARS,
    ChartBlock,
    IDEBlockAdapter,
    TableBlock,
    ide_blocks_from_presentation,
)
from pydantic import ValidationError


def _tabla(**overrides):
    datos = {
        "fallback_text": "Opción | Costo\nA | 1\nB | 2",
        "columns": [
            {"key": "opcion", "title": "Opción"},
            {"key": "costo", "title": "Costo", "align": "right"},
        ],
        "rows": [{"opcion": "A", "costo": "1"}, {"opcion": "B", "costo": "2"}],
    }
    datos.update(overrides)
    return datos


def _grafica(**overrides):
    datos = {
        "fallback_text": "build: v1 420, v2 510, v3 505",
        "chart_kind": "line",
        "title": "Tiempo de build",
        "series": [
            {
                "name": "main",
                "points": [
                    {"label": "v1", "value": 420},
                    {"label": "v2", "value": 510},
                    {"label": "v3", "value": 505},
                ],
            }
        ],
    }
    datos.update(overrides)
    return datos


# ---------------------------------------------------------------------------
# El bloque viaja entero
# ---------------------------------------------------------------------------


def test_la_tabla_viaja_entera_por_json_y_vuelve_igual():
    original = TableBlock(**_tabla(title="Costos", note="Medido hoy"))

    ida = json.dumps(original.model_dump(mode="json"))
    vuelta = IDEBlockAdapter.validate_python(json.loads(ida))

    assert isinstance(vuelta, TableBlock)
    assert vuelta == original
    assert vuelta.columns[1].align == "right"
    assert vuelta.rows == [{"opcion": "A", "costo": "1"}, {"opcion": "B", "costo": "2"}]


def test_la_grafica_viaja_entera_con_todas_sus_series():
    original = ChartBlock(
        **_grafica(
            chart_kind="bar",
            y_label="ms",
            series=[
                {
                    "name": "main",
                    "points": [
                        {"label": "v1", "value": 420.5},
                        {"label": "v2", "value": 510},
                        {"label": "v3", "value": 505},
                    ],
                },
                {
                    "name": "release",
                    "points": [
                        {"label": "v1", "value": 400},
                        {"label": "v2", "value": 480},
                        {"label": "v3", "value": 470},
                    ],
                },
            ],
        )
    )

    cable = json.dumps(original.model_dump(mode="json"))
    vuelta = IDEBlockAdapter.validate_python(json.loads(cable))

    assert isinstance(vuelta, ChartBlock)
    assert vuelta == original
    assert [serie.name for serie in vuelta.series] == ["main", "release"]
    assert vuelta.series[0].points[0].value == pytest.approx(420.5)


def test_el_discriminador_elige_el_tipo_correcto():
    assert isinstance(IDEBlockAdapter.validate_python({"type": "table", **_tabla()}), TableBlock)
    assert isinstance(IDEBlockAdapter.validate_python({"type": "chart", **_grafica()}), ChartBlock)


# ---------------------------------------------------------------------------
# Tabla: celdas faltantes y claves de más
# ---------------------------------------------------------------------------


def test_una_celda_faltante_no_revienta_la_tabla_y_queda_ausente():
    """Sin dato = clave ausente, y solo afecta a SU columna.

    Es la razón de que las filas vayan por clave: con filas posicionales, la
    celda que falta corre a todas las siguientes bajo la columna equivocada y
    la tabla se dibuja perfecta pero miente.
    """
    bloque = TableBlock(**_tabla(rows=[{"opcion": "A"}, {"costo": "2"}]))

    assert bloque.rows == [{"opcion": "A"}, {"costo": "2"}]
    assert "costo" not in bloque.rows[0]
    assert "opcion" not in bloque.rows[1]


def test_una_celda_vacia_se_normaliza_a_ausente():
    bloque = TableBlock(**_tabla(rows=[{"opcion": "A", "costo": ""}]))

    assert bloque.rows == [{"opcion": "A"}]


def test_una_clave_sin_columna_se_descarta_sin_tumbar_la_tabla():
    bloque = TableBlock(**_tabla(rows=[{"opcion": "A", "costo": "1", "secreto": "no dibujable"}]))

    assert bloque.rows == [{"opcion": "A", "costo": "1"}]


def test_una_celda_larguisima_se_recorta_en_vez_de_romper_el_render():
    bloque = TableBlock(**_tabla(rows=[{"opcion": "x" * 500, "costo": "1"}]))

    assert len(bloque.rows[0]["opcion"]) == MAX_TABLE_CELL_CHARS


def test_una_tabla_sin_ninguna_celda_util_se_rechaza():
    with pytest.raises(ValidationError, match="ni una celda con datos"):
        TableBlock(**_tabla(rows=[{"otra": "cosa"}]))


def test_dos_columnas_con_la_misma_clave_se_rechazan():
    with pytest.raises(ValidationError, match="repetir 'key'"):
        TableBlock(
            **_tabla(
                columns=[{"key": "a", "title": "Uno"}, {"key": "a", "title": "Dos"}],
                rows=[{"a": "1"}],
            )
        )


# ---------------------------------------------------------------------------
# La regla dura de la gráfica
# ---------------------------------------------------------------------------


def test_una_sola_serie_de_dos_puntos_se_rechaza_con_motivo_util():
    with pytest.raises(ValidationError) as exc:
        ChartBlock(
            **_grafica(
                series=[
                    {
                        "name": "main",
                        "points": [
                            {"label": "antes", "value": 1},
                            {"label": "después", "value": 2},
                        ],
                    }
                ]
            )
        )

    motivo = str(exc.value)
    assert "dos puntos son una diferencia" in motivo
    assert "tabla" in motivo


def test_dos_series_de_dos_puntos_si_son_una_comparacion_valida():
    bloque = ChartBlock(
        **_grafica(
            chart_kind="bar",
            series=[
                {
                    "name": "antes",
                    "points": [{"label": "p50", "value": 10}, {"label": "p99", "value": 90}],
                },
                {
                    "name": "después",
                    "points": [{"label": "p50", "value": 8}, {"label": "p99", "value": 40}],
                },
            ],
        )
    )

    assert len(bloque.series) == 2


def test_series_con_ejes_distintos_se_rechazan():
    with pytest.raises(ValidationError, match="mismas etiquetas del eje"):
        ChartBlock(
            **_grafica(
                series=[
                    {
                        "name": "a",
                        "points": [
                            {"label": "v1", "value": 1},
                            {"label": "v2", "value": 2},
                            {"label": "v3", "value": 3},
                        ],
                    },
                    {
                        "name": "b",
                        "points": [
                            {"label": "otra", "value": 1},
                            {"label": "cosa", "value": 2},
                            {"label": "aun", "value": 3},
                        ],
                    },
                ]
            )
        )


def test_todos_los_valores_iguales_se_rechaza():
    with pytest.raises(ValidationError, match="idénticos"):
        ChartBlock(
            **_grafica(
                series=[
                    {
                        "name": "main",
                        "points": [
                            {"label": "v1", "value": 7},
                            {"label": "v2", "value": 7},
                            {"label": "v3", "value": 7},
                        ],
                    }
                ]
            )
        )


def test_dos_series_planas_en_niveles_distintos_si_se_permiten():
    """Dos líneas planas a distinta altura sí dicen algo: una está por encima."""
    bloque = ChartBlock(
        **_grafica(
            series=[
                {
                    "name": "a",
                    "points": [
                        {"label": "v1", "value": 1},
                        {"label": "v2", "value": 1},
                        {"label": "v3", "value": 1},
                    ],
                },
                {
                    "name": "b",
                    "points": [
                        {"label": "v1", "value": 5},
                        {"label": "v2", "value": 5},
                        {"label": "v3", "value": 5},
                    ],
                },
            ]
        )
    )

    assert len(bloque.series) == 2


def test_una_etiqueta_repetida_en_la_misma_serie_se_rechaza():
    with pytest.raises(ValidationError, match="repite una etiqueta"):
        ChartBlock(
            **_grafica(
                series=[
                    {
                        "name": "main",
                        "points": [
                            {"label": "v1", "value": 1},
                            {"label": "v1", "value": 2},
                            {"label": "v3", "value": 3},
                        ],
                    }
                ]
            )
        )


def test_un_valor_no_finito_se_rechaza():
    with pytest.raises(ValidationError, match="NaN"):
        ChartBlock(
            **_grafica(
                series=[
                    {
                        "name": "main",
                        "points": [
                            {"label": "v1", "value": float("nan")},
                            {"label": "v2", "value": 2},
                            {"label": "v3", "value": 3},
                        ],
                    }
                ]
            )
        )


# ---------------------------------------------------------------------------
# El canal
# ---------------------------------------------------------------------------


def test_el_canal_descarta_lo_que_no_es_un_bloque_y_conserva_lo_bueno():
    bloques = ide_blocks_from_presentation(
        [
            {"type": "table", **_tabla()},
            {"type": "media", "media_kind": "image"},  # bloque de chat, no del IDE
            {"type": "chart", **_grafica()},
            "no soy un objeto",
        ]
    )

    assert [type(bloque).__name__ for bloque in bloques] == ["TableBlock", "ChartBlock"]


def test_el_canal_ignora_lo_que_no_es_una_lista():
    assert ide_blocks_from_presentation({"type": "table", **_tabla()}) == []
    assert ide_blocks_from_presentation(None) == []


def test_el_canal_no_repite_el_mismo_bloque_ni_pasa_de_su_tope():
    bloques = ide_blocks_from_presentation([{"type": "table", **_tabla()}] * 5)

    assert len(bloques) == 1


def test_el_canal_corta_en_max_bloques():
    crudos = [{"type": "table", **_tabla(title=f"Tabla {indice}")} for indice in range(6)]

    assert len(ide_blocks_from_presentation(crudos)) == 3
