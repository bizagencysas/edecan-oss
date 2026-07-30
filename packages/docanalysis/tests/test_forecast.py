"""Tests de `edecan_docanalysis.forecast` (WP-V5-12): funciones puras de
predicción de series y detección de anomalías, más las dos tools
(`predecir_serie`, `detectar_anomalias`).

Todos los valores esperados se verificaron de forma INDEPENDIENTE (script
aparte, reimplementando las fórmulas desde cero) antes de fijarlos aquí —
no son un chequeo tautológico contra la propia implementación.
"""

from __future__ import annotations

import math

import pytest
from edecan_docanalysis.forecast import (
    DISCLAIMER_FORECAST,
    DetectarAnomaliasTool,
    PredecirSerieTool,
    backtest,
    detectar_anomalias,
    detectar_outliers_iqr,
    detectar_outliers_zscore,
    detectar_rachas,
    elegir_mejor_metodo,
    holt,
    media_movil,
    predecir,
    regresion_lineal,
    suavizado_exponencial_simple,
)

# ---------------------------------------------------------------------------
# media_movil
# ---------------------------------------------------------------------------


def test_media_movil_calculada_a_mano():
    assert media_movil([10.0, 12.0, 14.0, 16.0], 3) == [12.0, 14.0]


def test_media_movil_ventana_1_no_suaviza():
    assert media_movil([1.0, 5.0, 2.0], 1) == [1.0, 5.0, 2.0]


def test_media_movil_ventana_igual_a_longitud_da_un_solo_promedio():
    assert media_movil([2.0, 4.0, 6.0], 3) == [4.0]


def test_media_movil_ventana_menor_a_1_lanza_error():
    with pytest.raises(ValueError, match="ventana"):
        media_movil([1.0, 2.0], 0)


def test_media_movil_ventana_mayor_a_la_serie_lanza_error():
    with pytest.raises(ValueError, match="no puede ser mayor"):
        media_movil([1.0, 2.0], 3)


# ---------------------------------------------------------------------------
# regresion_lineal
# ---------------------------------------------------------------------------


def test_regresion_lineal_serie_perfectamente_lineal():
    pendiente, intercepto, r2 = regresion_lineal([10.0, 12.0, 14.0, 16.0])
    assert pendiente == pytest.approx(2.0)
    assert intercepto == pytest.approx(10.0)
    assert r2 == pytest.approx(1.0)


def test_regresion_lineal_serie_constante_r2_es_1_no_indefinido():
    pendiente, intercepto, r2 = regresion_lineal([5.0, 5.0, 5.0, 5.0])
    assert pendiente == pytest.approx(0.0)
    assert intercepto == pytest.approx(5.0)
    assert r2 == pytest.approx(1.0)


def test_regresion_lineal_menos_de_2_valores_lanza_error():
    with pytest.raises(ValueError, match="al menos 2"):
        regresion_lineal([1.0])


# ---------------------------------------------------------------------------
# suavizado_exponencial_simple (SES)
# ---------------------------------------------------------------------------


def test_ses_calculado_a_mano():
    resultado = suavizado_exponencial_simple([10.0, 12.0, 14.0, 16.0], 0.5)
    assert resultado == pytest.approx([10.0, 11.0, 12.5, 14.25])


def test_ses_alfa_fuera_de_rango_lanza_error():
    with pytest.raises(ValueError, match="alfa"):
        suavizado_exponencial_simple([1.0, 2.0], 0.0)
    with pytest.raises(ValueError, match="alfa"):
        suavizado_exponencial_simple([1.0, 2.0], 1.5)


def test_ses_serie_vacia_lanza_error():
    with pytest.raises(ValueError, match="al menos 1"):
        suavizado_exponencial_simple([], 0.5)


# ---------------------------------------------------------------------------
# holt (tendencia lineal doble)
# ---------------------------------------------------------------------------


def test_holt_calculado_a_mano_serie_perfectamente_lineal():
    # Con una serie ya perfectamente lineal, Holt la sigue exacto desde el
    # primer paso: niveles == valores, tendencias constantes = la pendiente real.
    niveles, tendencias = holt([10.0, 12.0, 14.0, 16.0], 0.5, 0.3)
    assert niveles == pytest.approx([10.0, 12.0, 14.0, 16.0])
    assert tendencias == pytest.approx([2.0, 2.0, 2.0, 2.0])


def test_holt_menos_de_2_valores_lanza_error():
    with pytest.raises(ValueError, match="al menos 2"):
        holt([1.0], 0.5, 0.3)


def test_holt_alfa_beta_fuera_de_rango_lanzan_error():
    with pytest.raises(ValueError, match="alfa"):
        holt([1.0, 2.0], 0.0, 0.3)
    with pytest.raises(ValueError, match="beta"):
        holt([1.0, 2.0], 0.5, 1.5)


def test_holt_beta_cero_es_valido_tendencia_nunca_se_actualiza():
    niveles, tendencias = holt([10.0, 20.0, 15.0, 25.0], 0.5, 0.0)
    assert tendencias == pytest.approx([10.0, 10.0, 10.0, 10.0])


# ---------------------------------------------------------------------------
# backtest
# ---------------------------------------------------------------------------


def test_backtest_media_movil_calculado_a_mano():
    # n=6: holdout natural = 3 (20% de 6 = 1.2, pero mínimo 3), train=[2,4,6]
    # (ventana=3 -> media 4.0, plano), holdout real=[8,10,12].
    resultado = backtest([2.0, 4.0, 6.0, 8.0, 10.0, 12.0], "media_movil", horizonte=6)
    assert resultado["n_holdout"] == 3
    assert resultado["n_entrenamiento"] == 3
    assert resultado["residuos"] == pytest.approx([-4.0, -6.0, -8.0])
    assert resultado["mae"] == pytest.approx(6.0)


def test_backtest_regresion_lineal_serie_lineal_da_mae_cero():
    resultado = backtest([2.0, 4.0, 6.0, 8.0, 10.0, 12.0], "regresion_lineal", horizonte=6)
    assert resultado["mae"] == pytest.approx(0.0)
    assert resultado["residuos"] == pytest.approx([0.0, 0.0, 0.0])


def test_backtest_regresion_lineal_en_su_piso_de_4_puntos_no_revienta():
    # n=4 es el mínimo elegible de regresión (_MIN_TOTAL); con la regla del
    # 20%/mín-3 a secas el entrenamiento se quedaría en 1 punto (insuficiente
    # para una recta) — `_tamano_holdout` lo evita dejando 2 de entrenamiento.
    resultado = backtest([1.0, 3.0, 5.0, 7.0], "regresion_lineal", horizonte=4)
    assert resultado["n_entrenamiento"] == 2
    assert resultado["n_holdout"] == 2
    assert resultado["mae"] == pytest.approx(0.0)  # sigue siendo perfectamente lineal


def test_backtest_media_movil_en_su_piso_de_3_puntos_no_revienta():
    resultado = backtest([1.0, 2.0, 3.0], "media_movil", horizonte=3)
    assert resultado["n_entrenamiento"] == 1
    assert resultado["n_holdout"] == 2
    assert resultado["residuos"] == pytest.approx([-1.0, -2.0])
    assert resultado["mae"] == pytest.approx(1.5)


def test_backtest_horizonte_recorta_el_holdout_natural():
    # El holdout natural de esta serie (n=6, media_movil) es 3; pedir
    # horizonte=1 lo recorta a 1 solo punto evaluado.
    resultado = backtest([2.0, 4.0, 6.0, 8.0, 10.0, 12.0], "media_movil", horizonte=1)
    assert resultado["n_holdout"] == 1
    assert resultado["n_entrenamiento"] == 5
    assert resultado["residuos"] == pytest.approx([-4.0])


def test_backtest_metodo_desconocido_lanza_error():
    with pytest.raises(ValueError, match="método desconocido"):
        backtest([1.0, 2.0, 3.0], "bogus", horizonte=1)


def test_backtest_serie_menor_al_piso_del_metodo_lanza_error():
    with pytest.raises(ValueError, match="al menos 8"):
        backtest([1.0, 2.0, 3.0], "holt", horizonte=1)


def test_backtest_horizonte_no_positivo_lanza_error():
    with pytest.raises(ValueError, match="horizonte"):
        backtest([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0], "holt", horizonte=0)


# ---------------------------------------------------------------------------
# elegir_mejor_metodo
# ---------------------------------------------------------------------------


def test_elegir_mejor_metodo_serie_lineal_perfecta_prefiere_regresion():
    # n=8: holt también da MAE 0 exacto en esta serie (empate) — el
    # desempate determinista favorece a regresion_lineal (más simple/
    # interpretable, primero en el orden de desempate entre los dos).
    serie = [10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 22.0, 24.0]
    seleccion = elegir_mejor_metodo(serie)
    assert seleccion["metodo"] == "regresion_lineal"
    assert seleccion["candidatos"]["regresion_lineal"]["mae"] == pytest.approx(0.0)
    assert seleccion["candidatos"]["holt"]["mae"] == pytest.approx(0.0)
    assert seleccion["candidatos"]["media_movil"]["mae"] == pytest.approx(6.0)
    assert seleccion["candidatos"]["suavizado_exponencial"]["mae"] == pytest.approx(5.875)


def test_elegir_mejor_metodo_serie_plana_todos_empatan_gana_media_movil():
    seleccion = elegir_mejor_metodo([7.0] * 8)
    assert seleccion["metodo"] == "media_movil"
    assert all(c["mae"] == pytest.approx(0.0) for c in seleccion["candidatos"].values())
    assert set(seleccion["candidatos"]) == {
        "media_movil",
        "suavizado_exponencial",
        "regresion_lineal",
        "holt",
    }


def test_elegir_mejor_metodo_media_movil_pierde_contra_holt_en_serie_con_bandazos_cortos():
    # Serie con tendencia alcista clara pero con bajones cortos cada 2-3
    # puntos ("estacionalidad corta"): la media móvil, ciega a tendencia,
    # queda muy por debajo del holdout real; Holt sí sigue la pendiente.
    serie = [50.0, 55.0, 53.0, 60.0, 65.0, 63.0, 70.0, 75.0, 73.0, 80.0]
    seleccion = elegir_mejor_metodo(serie)
    mae_media_movil = seleccion["candidatos"]["media_movil"]["mae"]
    mae_holt = seleccion["candidatos"]["holt"]["mae"]
    assert mae_holt < mae_media_movil
    assert mae_media_movil == pytest.approx(10.0)
    assert mae_holt == pytest.approx(1.948368, abs=1e-5)
    assert seleccion["metodo"] == "holt"  # además resulta ser el mejor de los 4


def test_elegir_mejor_metodo_solo_dos_candidatos_en_el_piso_minimo():
    # n=3: ni regresión (>=4) ni holt (>=8) son aplicables todavía.
    seleccion = elegir_mejor_metodo([1.0, 2.0, 3.0])
    assert set(seleccion["candidatos"]) == {"media_movil", "suavizado_exponencial"}


def test_elegir_mejor_metodo_serie_muy_corta_lanza_error():
    with pytest.raises(ValueError, match="al menos 3"):
        elegir_mejor_metodo([1.0, 2.0])


# ---------------------------------------------------------------------------
# predecir — pipeline completo
# ---------------------------------------------------------------------------


def test_predecir_serie_lineal_perfecta_predice_exacto_con_intervalo_cero():
    resultado = predecir([10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 22.0, 24.0], 3)
    assert resultado["metodo"] == "regresion_lineal"
    assert resultado["predicciones"] == pytest.approx([26.0, 28.0, 30.0])
    assert resultado["intervalo_inferior"] == pytest.approx([26.0, 28.0, 30.0])
    assert resultado["intervalo_superior"] == pytest.approx([26.0, 28.0, 30.0])
    assert resultado["margen"] == pytest.approx(0.0)
    assert resultado["pendiente"] == pytest.approx(2.0)
    assert resultado["intercepto"] == pytest.approx(10.0)
    assert resultado["r2"] == pytest.approx(1.0)
    assert resultado["tendencia"] is None


def test_predecir_serie_plana_intervalo_es_cero_por_desviacion_cero():
    resultado = predecir([7.0] * 8, 2)
    assert resultado["metodo"] == "media_movil"
    assert resultado["predicciones"] == pytest.approx([7.0, 7.0])
    assert resultado["margen"] == pytest.approx(0.0)
    assert resultado["intervalo_inferior"] == pytest.approx([7.0, 7.0])
    assert resultado["intervalo_superior"] == pytest.approx([7.0, 7.0])
    assert resultado["pendiente"] is None
    assert resultado["tendencia"] is None


def test_predecir_metodo_holt_expone_tendencia_no_pendiente_ni_r2():
    serie = [50.0, 55.0, 53.0, 60.0, 65.0, 63.0, 70.0, 75.0, 73.0, 80.0]
    resultado = predecir(serie, 2)
    assert resultado["metodo"] == "holt"
    assert resultado["tendencia"] is not None
    assert resultado["pendiente"] is None
    assert resultado["r2"] is None


def test_predecir_refita_sobre_la_serie_completa_no_solo_el_tramo_de_entrenamiento():
    # n=3: el backtest de "media_movil" entrena con 1 solo punto ([1.0]), pero
    # el pronóstico real de `predecir` debe usar los 3 puntos completos.
    resultado = predecir([1.0, 2.0, 3.0], 2)
    assert resultado["metodo"] == "media_movil"
    assert resultado["predicciones"] == pytest.approx([2.0, 2.0])  # media de [1,2,3], no de [1]


def test_predecir_filtra_none_y_nan_con_aviso():
    resultado = predecir([1.0, None, 2.0, float("nan"), 3.0], 1)
    assert resultado["aviso"] is not None
    assert "2" in resultado["aviso"]


def test_predecir_serie_menor_a_3_puntos_lanza_error():
    with pytest.raises(ValueError, match="al menos 3"):
        predecir([1.0, 2.0], 3)


def test_predecir_valor_no_numerico_lanza_error_claro():
    with pytest.raises(ValueError, match="abc"):
        predecir([1.0, 2.0, "abc"], 3)


@pytest.mark.parametrize("periodos", [0, 25, -1])
def test_predecir_periodos_fuera_de_rango_lanza_error(periodos):
    with pytest.raises(ValueError, match="periodos"):
        predecir([1.0, 2.0, 3.0], periodos)


def test_predecir_periodos_no_entero_lanza_error():
    with pytest.raises(ValueError, match="entero"):
        predecir([1.0, 2.0, 3.0], 3.5)
    with pytest.raises(ValueError, match="entero"):
        predecir([1.0, 2.0, 3.0], True)


def test_predecir_valores_no_es_lista_lanza_error():
    with pytest.raises(ValueError, match="lista"):
        predecir("no soy una lista", 3)


def test_predecir_mas_de_500_valores_lanza_error():
    with pytest.raises(ValueError, match="máximo"):
        predecir([float(i) for i in range(501)], 3)


# ---------------------------------------------------------------------------
# detectar_outliers_iqr
# ---------------------------------------------------------------------------


def test_detectar_outliers_iqr_calculado_a_mano():
    valores = [10.0, 12.0, 13.0, 12.0, 11.0, 13.0, 12.0, 100.0]
    outliers = detectar_outliers_iqr(valores)
    assert outliers == [{"indice": 7, "valor": 100.0, "score": pytest.approx(58.666667)}]


def test_detectar_outliers_iqr_serie_constante_sin_dispersion_no_marca_nada():
    assert detectar_outliers_iqr([5.0] * 6) == []


def test_detectar_outliers_iqr_umbral_mas_alto_deja_de_marcar():
    valores = [10.0, 12.0, 13.0, 12.0, 11.0, 13.0, 12.0, 100.0]
    assert detectar_outliers_iqr(valores, umbral=100.0) == []


def test_detectar_outliers_iqr_umbral_no_positivo_lanza_error():
    with pytest.raises(ValueError, match="umbral"):
        detectar_outliers_iqr([1.0, 2.0, 3.0, 4.0], umbral=0.0)


def test_detectar_outliers_iqr_menos_de_4_valores_lanza_error():
    with pytest.raises(ValueError, match="al menos 4"):
        detectar_outliers_iqr([1.0, 2.0, 3.0])


# ---------------------------------------------------------------------------
# detectar_outliers_zscore
# ---------------------------------------------------------------------------


def test_detectar_outliers_zscore_calculado_a_mano():
    valores = [10.0] * 11 + [100.0]
    outliers = detectar_outliers_zscore(valores)
    assert outliers == [{"indice": 11, "valor": 100.0, "score": pytest.approx(3.175426)}]


def test_detectar_outliers_zscore_no_marca_nada_por_debajo_del_umbral_default():
    # z del único punto alto es ~2.468, por debajo del umbral default 3.0.
    valores = [10.0, 11.0, 9.0, 10.0, 12.0, 11.0, 9.0, 50.0]
    assert detectar_outliers_zscore(valores) == []


def test_detectar_outliers_zscore_umbral_personalizado_mas_sensible_si_marca():
    valores = [10.0, 11.0, 9.0, 10.0, 12.0, 11.0, 9.0, 50.0]
    outliers = detectar_outliers_zscore(valores, umbral=2.0)
    assert outliers == [{"indice": 7, "valor": 50.0, "score": pytest.approx(2.46824, abs=1e-5)}]


def test_detectar_outliers_zscore_serie_sin_dispersion_no_marca_nada():
    assert detectar_outliers_zscore([5.0, 5.0, 5.0, 5.0]) == []


def test_detectar_outliers_zscore_umbral_no_positivo_lanza_error():
    with pytest.raises(ValueError, match="umbral"):
        detectar_outliers_zscore([1.0, 2.0], umbral=-1.0)


def test_detectar_outliers_zscore_menos_de_2_valores_lanza_error():
    with pytest.raises(ValueError, match="al menos 2"):
        detectar_outliers_zscore([1.0])


# ---------------------------------------------------------------------------
# detectar_rachas
# ---------------------------------------------------------------------------


def test_detectar_rachas_calculado_a_mano():
    valores = [float(v) for v in range(1, 11)]  # mediana=5.5, 5 abajo + 5 arriba
    rachas = detectar_rachas(valores)
    assert rachas == [
        {"inicio": 0, "fin": 4, "longitud": 5, "direccion": "por_debajo"},
        {"inicio": 5, "fin": 9, "longitud": 5, "direccion": "por_encima"},
    ]


def test_detectar_rachas_valor_igual_a_la_mediana_corta_la_racha():
    valores = [1.0, 2.0, 3.0, 4.0, 5.0, 100.0, 100.0, 100.0, 100.0, 100.0, 5.0, 4.0, 3.0]
    # mediana=5.0: los tramos "por_debajo" (longitud 4 y 2) no llegan al
    # mínimo; el 5.0 del medio corta cualquier racha; solo el tramo de 100.0
    # (longitud 5, "por_encima") califica.
    rachas = detectar_rachas(valores)
    assert rachas == [{"inicio": 5, "fin": 9, "longitud": 5, "direccion": "por_encima"}]


def test_detectar_rachas_por_debajo_del_minimo_no_se_reporta():
    # Racha de 4 valores por encima de la mediana: no llega al mínimo (5).
    valores = [5.0, 5.0, 5.0, 5.0, 5.0, 10.0, 10.0, 10.0, 10.0]
    assert detectar_rachas(valores) == []


def test_detectar_rachas_minimo_personalizado_mas_bajo_si_detecta():
    valores = [5.0, 5.0, 5.0, 5.0, 5.0, 10.0, 10.0, 10.0, 10.0]
    rachas = detectar_rachas(valores, minimo=4)
    assert rachas == [{"inicio": 5, "fin": 8, "longitud": 4, "direccion": "por_encima"}]


def test_detectar_rachas_minimo_no_positivo_lanza_error():
    with pytest.raises(ValueError, match="minimo"):
        detectar_rachas([1.0, 2.0, 3.0], minimo=0)


# ---------------------------------------------------------------------------
# detectar_anomalias — pipeline completo
# ---------------------------------------------------------------------------


def test_detectar_anomalias_iqr_end_to_end():
    valores = [10.0, 12.0, 13.0, 12.0, 11.0, 13.0, 12.0, 100.0]
    resultado = detectar_anomalias(valores)  # metodo="iqr" por defecto
    assert resultado["metodo"] == "iqr"
    assert resultado["umbral"] == pytest.approx(1.5)
    assert resultado["n"] == 8
    assert len(resultado["outliers"]) == 1
    assert resultado["outliers"][0]["indice"] == 7
    assert resultado["rachas"] == []
    assert resultado["aviso"] is None


def test_detectar_anomalias_zscore_end_to_end_con_umbral_explicito():
    valores = [10.0, 11.0, 9.0, 10.0, 12.0, 11.0, 9.0, 50.0]
    resultado = detectar_anomalias(valores, metodo="zscore", umbral=2.0)
    assert resultado["metodo"] == "zscore"
    assert resultado["umbral"] == pytest.approx(2.0)
    assert [o["indice"] for o in resultado["outliers"]] == [7]


def test_detectar_anomalias_filtra_none_y_nan_con_aviso():
    resultado = detectar_anomalias([1.0, None, 2.0, 3.0, float("nan"), 4.0])
    assert resultado["n"] == 4
    assert resultado["aviso"] is not None


def test_detectar_anomalias_menos_de_4_valores_lanza_error():
    with pytest.raises(ValueError, match="al menos 4"):
        detectar_anomalias([1.0, 2.0, 3.0])


def test_detectar_anomalias_metodo_invalido_lanza_error():
    with pytest.raises(ValueError, match="metodo"):
        detectar_anomalias([1.0, 2.0, 3.0, 4.0], metodo="bogus")


def test_detectar_anomalias_umbral_no_positivo_lanza_error():
    with pytest.raises(ValueError, match="umbral"):
        detectar_anomalias([1.0, 2.0, 3.0, 4.0], umbral=-5)


def test_detectar_anomalias_mas_de_1000_valores_lanza_error():
    with pytest.raises(ValueError, match="máximo"):
        detectar_anomalias([float(i) for i in range(1001)])


# ---------------------------------------------------------------------------
# PredecirSerieTool
# ---------------------------------------------------------------------------


async def test_predecir_serie_tool_camino_feliz_termina_con_el_disclaimer_exacto(make_ctx):
    ctx = make_ctx()
    resultado = await PredecirSerieTool().run(
        ctx, {"valores": [10, 12, 14, 16, 18, 20, 22, 24], "periodos": 3}
    )
    assert resultado.content.endswith(DISCLAIMER_FORECAST)
    assert resultado.data["metodo"] == "regresion_lineal"
    assert resultado.data["predicciones"] == pytest.approx([26.0, 28.0, 30.0])
    assert "t+1" in resultado.content
    assert "t+3" in resultado.content


async def test_predecir_serie_tool_usa_periodos_por_defecto_si_no_viene(make_ctx):
    ctx = make_ctx()
    resultado = await PredecirSerieTool().run(ctx, {"valores": [1, 2, 3, 4, 5, 6, 7, 8]})
    assert len(resultado.data["predicciones"]) == 6  # default


async def test_predecir_serie_tool_valores_faltantes_no_lanza_devuelve_toolresult(make_ctx):
    ctx = make_ctx()
    resultado = await PredecirSerieTool().run(ctx, {})
    assert "valores" in resultado.content
    assert resultado.data is None


async def test_predecir_serie_tool_serie_muy_corta_no_lanza(make_ctx):
    ctx = make_ctx()
    resultado = await PredecirSerieTool().run(ctx, {"valores": [1, 2]})
    assert "al menos 3" in resultado.content
    assert resultado.data is None
    assert not resultado.content.endswith(DISCLAIMER_FORECAST)  # camino de error, no feliz


async def test_predecir_serie_tool_etiquetas_de_longitud_incorrecta_devuelve_error(make_ctx):
    ctx = make_ctx()
    resultado = await PredecirSerieTool().run(
        ctx, {"valores": [1, 2, 3], "etiquetas": ["ene", "feb"]}
    )
    assert "etiquetas" in resultado.content
    assert resultado.data is None


async def test_predecir_serie_tool_con_etiquetas_las_usa_para_contexto_no_para_el_futuro(make_ctx):
    ctx = make_ctx()
    resultado = await PredecirSerieTool().run(
        ctx,
        {
            "valores": [1, 2, 3, 4, 5, 6, 7, 8],
            "periodos": 2,
            "etiquetas": ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago"],
        },
    )
    assert "ago" in resultado.content  # última etiqueta histórica, mencionada
    assert "t+1" in resultado.content  # los períodos futuros siguen sintéticos
    assert "t+2" in resultado.content


async def test_predecir_serie_tool_no_es_dangerous_ni_requiere_flags():
    tool = PredecirSerieTool()
    assert tool.dangerous is False
    assert tool.requires_flags == frozenset()
    assert tool.input_schema["type"] == "object"
    assert tool.input_schema["required"] == ["valores"]


# ---------------------------------------------------------------------------
# DetectarAnomaliasTool
# ---------------------------------------------------------------------------


async def test_detectar_anomalias_tool_camino_feliz(make_ctx):
    ctx = make_ctx()
    resultado = await DetectarAnomaliasTool().run(
        ctx, {"valores": [10, 12, 13, 12, 11, 13, 12, 100]}
    )
    assert resultado.data["metodo"] == "iqr"
    assert len(resultado.data["outliers"]) == 1
    assert "posición 8" in resultado.content  # 1-based en el texto


async def test_detectar_anomalias_tool_con_etiquetas_las_usa_en_vez_de_posiciones(make_ctx):
    ctx = make_ctx()
    resultado = await DetectarAnomaliasTool().run(
        ctx,
        {
            "valores": [10, 12, 13, 12, 11, 13, 12, 100],
            "etiquetas": ["a", "b", "c", "d", "e", "f", "g", "h"],
        },
    )
    assert "h:" in resultado.content
    assert "posición" not in resultado.content


async def test_detectar_anomalias_tool_rachas_en_el_texto(make_ctx):
    ctx = make_ctx()
    valores = list(range(1, 11))
    resultado = await DetectarAnomaliasTool().run(ctx, {"valores": valores})
    assert len(resultado.data["rachas"]) == 2
    assert "Rachas detectadas" in resultado.content


async def test_detectar_anomalias_tool_valores_faltantes_no_lanza(make_ctx):
    ctx = make_ctx()
    resultado = await DetectarAnomaliasTool().run(ctx, {})
    assert "valores" in resultado.content
    assert resultado.data is None


async def test_detectar_anomalias_tool_metodo_invalido_no_lanza(make_ctx):
    ctx = make_ctx()
    resultado = await DetectarAnomaliasTool().run(ctx, {"valores": [1, 2, 3, 4], "metodo": "bogus"})
    assert resultado.data is None


async def test_detectar_anomalias_tool_no_es_dangerous_ni_requiere_flags():
    tool = DetectarAnomaliasTool()
    assert tool.dangerous is False
    assert tool.requires_flags == frozenset()
    assert tool.input_schema["required"] == ["valores"]


# ---------------------------------------------------------------------------
# Sanity: NaN vía math.isnan (evita depender solo de float('nan') literal)
# ---------------------------------------------------------------------------


def test_predecir_filtra_nan_construido_con_math():
    resultado = predecir([1.0, 2.0, math.nan, 3.0], 1)
    assert resultado["aviso"] is not None
