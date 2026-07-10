"""Predicción de series (forecast) y detección de anomalías — WP-V5-12.

Cubre del wishlist de Analista (`REQUISITOS_V2.md`: "predice ventas, calcula
riesgos, detecta fraude"; `ROADMAP_V2.md` §3 fila 5 lo dejó pendiente como
"Detección de fraude ML = P2 (outliers básicos sí en P0)") la parte que SÍ
entra en P0: predicción de series por métodos estadísticos clásicos y
detección de anomalías por heurísticas estadísticas generales (IQR, z-score,
rachas). Un modelo de fraude con aprendizaje automático de verdad sigue
fuera de alcance — nada de este módulo pretende serlo.

Dos herramientas nuevas del agente (`predecir_serie`, `detectar_anomalias`)
más las funciones puras que las sostienen. Mismo "REGLA DE ORO" que el resto
de `edecan_docanalysis`: **cero dependencias nuevas** (nada de numpy/pandas/
scipy — solo `math`/`statistics` de la stdlib), determinista, offline. A
diferencia de `tablas.py`/`vision.py`, este módulo no toca S3 ni el LLM: los
datos llegan completos en los argumentos de la tool, así que no depende de
`edecan-llm`/`aioboto3`.

## Predicción (`predecir_serie`)

Cuatro métodos clásicos, ninguno "de caja negra":

- `media_movil`: promedio de los últimos puntos, proyectado PLANO hacia
  adelante (sin tendencia). El más simple.
- `regresion_lineal`: mínimos cuadrados ordinarios (OLS) de la serie contra
  el índice temporal — capta tendencia lineal, informa `r²` (qué tan lineal
  es en realidad la serie).
- `suavizado_exponencial_simple` (SES): promedio ponderado exponencialmente
  decreciente, también proyectado plano — reacciona más rápido que la media
  móvil a cambios recientes, pero sigue sin modelar tendencia.
- `holt`: SES con una segunda ecuación de tendencia (suavizado exponencial
  doble) — el único que proyecta una pendiente hacia adelante sin asumir
  que la serie completa es perfectamente lineal.

`elegir_mejor_metodo` corre un `backtest` interno por cada método aplicable
(aparta el tramo final de la propia serie, pronostica sobre ese tramo, mide
el error absoluto medio — MAE) y usa el que mejor predijo ese tramo apartado
para el pronóstico real (`predecir`). El intervalo alrededor de cada
predicción es `±1.96 · desviación_estándar(residuos_del_backtest)` — una
heurística común ("regla del ~95%" asumiendo errores aproximadamente
normales), **NO un intervalo de confianza riguroso** derivado del modelo
(eso exigiría supuestos estadísticos más fuertes que esta biblioteca
deliberadamente no asume). Por eso toda predicción trae `DISCLAIMER_FORECAST`
— es información a partir de los datos dados, nunca asesoría financiera ni
garantía de resultados futuros.

## Anomalías (`detectar_anomalias`)

Dos métodos de outlier clásicos —IQR (mismos cuartiles "a la Tukey" que
`tablas.py::_outliers_iqr`: mediana de cada mitad, excluyendo la mediana
global cuando `n` es impar) y z-score— más detección de RACHAS (≥5 valores
consecutivos por el mismo lado de la mediana: señal simple de cambio de
régimen sostenido). Heurística estadística general de "algo raro en los
datos", no un modelo de fraude.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from typing import Any

from edecan_core import Tool, ToolContext, ToolResult

from ._util import clamp_int

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

DISCLAIMER_FORECAST = (
    "Proyección estadística informativa basada solo en los datos dados; "
    "no es asesoría financiera ni garantía."
)

# Piso de puntos TOTALES en la serie para que un método sea "aplicable" en
# `elegir_mejor_metodo`/`backtest` (pinned en el enunciado del WP).
_MIN_TOTAL: dict[str, int] = {
    "media_movil": 3,
    "suavizado_exponencial": 3,
    "regresion_lineal": 4,
    "holt": 8,
}
# Piso de puntos de ENTRENAMIENTO (tras apartar el holdout) que cada método
# necesita para poder calcularse sin dividir por cero ni resultar trivial
# (regresión/holt necesitan al menos 2 puntos; media_movil/SES funcionan
# hasta con 1). Ver `_tamano_holdout`.
_MIN_TRAIN: dict[str, int] = {
    "media_movil": 1,
    "suavizado_exponencial": 1,
    "regresion_lineal": 2,
    "holt": 2,
}
# Orden fijo = prioridad de desempate cuando dos métodos empatan en MAE
# exacto (típicamente una serie constante): gana el más simple primero.
_ORDEN_METODOS: tuple[str, ...] = (
    "media_movil",
    "suavizado_exponencial",
    "regresion_lineal",
    "holt",
)
_METODOS_LEGIBLES: dict[str, str] = {
    "media_movil": "media móvil",
    "suavizado_exponencial": "suavizado exponencial simple",
    "regresion_lineal": "regresión lineal",
    "holt": "suavizado exponencial doble (Holt, con tendencia)",
}

# Hiperparámetros fijos (deterministas) que este módulo usa internamente
# para `elegir_mejor_metodo`/`predecir` — no configurables desde la tool, a
# propósito: la elección de método ya se adapta a los datos vía el backtest,
# no hace falta exponer más superficie de configuración.
_VENTANA_MEDIA_MOVIL = 3
_ALFA_SES_DEFECTO = 0.5
_ALFA_HOLT_DEFECTO = 0.5
_BETA_HOLT_DEFECTO = 0.3

_UMBRAL_ANOMALIA_DEFECTO: dict[str, float] = {"iqr": 1.5, "zscore": 3.0}
_MINIMO_RACHA = 5

_PERIODOS_DEFECTO = 6
_PERIODOS_MIN = 1
_PERIODOS_MAX = 24


def _round(valor: float) -> float:
    """Redondeo determinista a 6 decimales — mismo criterio que
    `tablas.py::_round`."""
    return round(valor, 6)


# ---------------------------------------------------------------------------
# Validación / normalización compartida por `predecir` y `detectar_anomalias`
# ---------------------------------------------------------------------------


def _normalizar_valores(
    valores: Any, *, minimo: int, maximo: int
) -> tuple[list[float], str | None]:
    """Valida y limpia `valores` para las funciones de este módulo.

    - Debe ser una lista o tupla; si no, `ValueError`.
    - Filtra `None` y NaN (`float('nan')`, o cualquier valor que parsee a
      NaN — p. ej. la cadena `"nan"`) contando cuántos se descartaron; el
      resultado se sigue calculando con lo que queda.
    - Cualquier otro valor no convertible a `float` (strings no numéricos,
      booleanos, cadenas vacías, listas/objetos anidados) o infinito
      (`inf`/`-inf`) es un error claro — NO se filtra en silencio.
    - Tras filtrar, exige `minimo <= len(limpios) <= maximo`.

    Devuelve `(valores_limpios, aviso)`: `aviso` es `None` si no se filtró
    nada, o un string en español si se descartaron valores nulos/NaN.
    """
    if not isinstance(valores, (list, tuple)):
        raise ValueError("'valores' debe ser una lista de números.")

    limpios: list[float] = []
    filtrados = 0
    for v in valores:
        if v is None:
            filtrados += 1
            continue
        if isinstance(v, bool):
            raise ValueError(f"'{v}' no es un valor numérico válido en 'valores'.")
        if isinstance(v, (int, float)):
            numero = float(v)
        elif isinstance(v, str) and v.strip():
            try:
                numero = float(v.strip())
            except ValueError:
                raise ValueError(f"'{v}' no es un valor numérico válido en 'valores'.") from None
        else:
            raise ValueError(f"'{v}' no es un valor numérico válido en 'valores'.")

        if math.isnan(numero):
            filtrados += 1
            continue
        if math.isinf(numero):
            raise ValueError(f"'{v}' no es un número finito válido en 'valores'.")
        limpios.append(numero)

    if len(limpios) < minimo:
        raise ValueError(
            f"Se necesitan al menos {minimo} valores numéricos válidos para esta serie "
            f"(quedaron {len(limpios)} tras descartar nulos/NaN)."
        )
    if len(limpios) > maximo:
        raise ValueError(f"Como máximo se aceptan {maximo} valores (llegaron {len(limpios)}).")

    aviso = (
        f"Se ignoraron {filtrados} valor(es) nulo(s)/NaN antes de analizar la serie."
        if filtrados
        else None
    )
    return limpios, aviso


def _validar_etiquetas(etiquetas: Any, n_esperado: int) -> tuple[list[str] | None, str | None]:
    """`(etiquetas_limpias_o_None, error_o_None)`. `None, None` si no
    vinieron (uso normal). Error claro si vinieron pero no son una lista de
    exactamente `n_esperado` elementos (uno por valor de la serie)."""
    if etiquetas is None:
        return None, None
    if not isinstance(etiquetas, list) or len(etiquetas) != n_esperado:
        return None, (
            f"'etiquetas', si viene, debe ser una lista de exactamente {n_esperado} "
            "elemento(s) (uno por valor)."
        )
    return [str(e) for e in etiquetas], None


# ---------------------------------------------------------------------------
# Métodos de predicción — funciones puras
# ---------------------------------------------------------------------------


def media_movil(valores: Sequence[float], ventana: int) -> list[float]:
    """Media móvil simple de `valores` con ventana `ventana`.

    Devuelve una lista de longitud `len(valores) - ventana + 1`: el elemento
    `i` es el promedio de `valores[i : i + ventana]` (ventana deslizante,
    sin relleno). El ÚLTIMO elemento es la "predicción ingenua" del
    siguiente período — el promedio de los `ventana` valores más recientes—
    y así es como este módulo la usa para pronosticar: SIEMPRE plana (el
    mismo valor repetido para todos los períodos futuros), porque una media
    móvil no modela tendencia.
    """
    if ventana < 1:
        raise ValueError("'ventana' debe ser un entero positivo.")
    if ventana > len(valores):
        raise ValueError(
            f"'ventana' ({ventana}) no puede ser mayor que la cantidad de valores ({len(valores)})."
        )
    return [sum(valores[i : i + ventana]) / ventana for i in range(len(valores) - ventana + 1)]


def regresion_lineal(valores: Sequence[float]) -> tuple[float, float, float]:
    """Regresión lineal OLS clásica de `valores` contra su índice (0, 1, 2, …).

    Devuelve `(pendiente, intercepto, r2)`. Caso especial: `r2 = 1.0` para
    una serie constante (ajuste trivial perfecto: pendiente 0, todos los
    residuos cero) en vez de la división 0/0 indefinida que daría la fórmula
    general.
    """
    n = len(valores)
    if n < 2:
        raise ValueError("Se necesitan al menos 2 valores para una regresión lineal.")

    xs = list(range(n))
    x_media = statistics.mean(xs)
    y_media = statistics.mean(valores)

    numerador = sum((x - x_media) * (y - y_media) for x, y in zip(xs, valores, strict=True))
    denominador = sum((x - x_media) ** 2 for x in xs)
    # denominador > 0 siempre para n >= 2: los índices 0..n-1 nunca son todos iguales.
    pendiente = numerador / denominador
    intercepto = y_media - pendiente * x_media

    ss_tot = sum((y - y_media) ** 2 for y in valores)
    if ss_tot == 0:
        r2 = 1.0
    else:
        ss_res = sum(
            (y - (pendiente * x + intercepto)) ** 2 for x, y in zip(xs, valores, strict=True)
        )
        r2 = 1.0 - ss_res / ss_tot

    return pendiente, intercepto, r2


def suavizado_exponencial_simple(valores: Sequence[float], alfa: float) -> list[float]:
    """Suavizado exponencial simple (SES): `S[0] = valores[0]`,
    `S[t] = alfa·valores[t] + (1-alfa)·S[t-1]`.

    Devuelve la lista `S` completa, misma longitud que `valores`. Sin
    componente de tendencia: el pronóstico hacia adelante es plano en
    `S[-1]` para cualquier horizonte (ver `holt` para la variante con
    tendencia).
    """
    if not (0 < alfa <= 1):
        raise ValueError("'alfa' debe estar en el rango (0, 1].")
    if len(valores) < 1:
        raise ValueError("Se necesita al menos 1 valor para suavizar.")

    suavizados = [float(valores[0])]
    for v in valores[1:]:
        suavizados.append(alfa * v + (1 - alfa) * suavizados[-1])
    return suavizados


def holt(valores: Sequence[float], alfa: float, beta: float) -> tuple[list[float], list[float]]:
    """Suavizado exponencial doble de Holt (nivel + tendencia lineal).

    `niveles[0] = valores[0]`, `tendencias[0] = valores[1] - valores[0]`
    (necesita al menos 2 valores para arrancar). Para `t >= 1`:

        niveles[t]    = alfa·valores[t] + (1-alfa)·(niveles[t-1] + tendencias[t-1])
        tendencias[t] = beta·(niveles[t] - niveles[t-1]) + (1-beta)·tendencias[t-1]

    Devuelve `(niveles, tendencias)`, dos listas de la misma longitud que
    `valores`. El pronóstico a `h` períodos hacia adelante es
    `niveles[-1] + h · tendencias[-1]` — a diferencia de `media_movil`/
    `suavizado_exponencial_simple`, SÍ proyecta una pendiente en vez de una
    línea plana.
    """
    if not (0 < alfa <= 1):
        raise ValueError("'alfa' debe estar en el rango (0, 1].")
    if not (0 <= beta <= 1):
        raise ValueError("'beta' debe estar en el rango [0, 1].")
    n = len(valores)
    if n < 2:
        raise ValueError("Se necesitan al menos 2 valores para el método de Holt.")

    niveles = [float(valores[0])]
    tendencias = [float(valores[1]) - float(valores[0])]
    for t in range(1, n):
        nivel_t = alfa * valores[t] + (1 - alfa) * (niveles[t - 1] + tendencias[t - 1])
        tendencia_t = beta * (nivel_t - niveles[t - 1]) + (1 - beta) * tendencias[t - 1]
        niveles.append(nivel_t)
        tendencias.append(tendencia_t)
    return niveles, tendencias


def _pronosticar_con_metodo(
    entrenamiento: Sequence[float], metodo: str, horizonte: int
) -> list[float]:
    """Pronostica `horizonte` puntos hacia adelante de `entrenamiento` con
    `metodo` (uno de `_ORDEN_METODOS`). Usado tanto por `backtest` (sobre el
    tramo de entrenamiento) como por `predecir` (sobre la serie completa)."""
    if metodo == "media_movil":
        ventana = min(_VENTANA_MEDIA_MOVIL, len(entrenamiento))
        ultimo = media_movil(entrenamiento, ventana)[-1]
        return [ultimo] * horizonte
    if metodo == "suavizado_exponencial":
        ultimo = suavizado_exponencial_simple(entrenamiento, _ALFA_SES_DEFECTO)[-1]
        return [ultimo] * horizonte
    if metodo == "regresion_lineal":
        pendiente, intercepto, _r2 = regresion_lineal(entrenamiento)
        n = len(entrenamiento)
        return [intercepto + pendiente * (n - 1 + h) for h in range(1, horizonte + 1)]
    if metodo == "holt":
        niveles, tendencias = holt(entrenamiento, _ALFA_HOLT_DEFECTO, _BETA_HOLT_DEFECTO)
        nivel_final, tendencia_final = niveles[-1], tendencias[-1]
        return [nivel_final + h * tendencia_final for h in range(1, horizonte + 1)]
    raise ValueError(f"método desconocido: '{metodo}'.")


def _tamano_holdout(n: int, minimo_train: int) -> int:
    """Tamaño del holdout para `backtest`: el 20% de los puntos con un
    mínimo de 3 — pero SIEMPRE deja al menos `minimo_train` puntos de
    entrenamiento. Para series muy cerca del piso elegible de cada método
    (`_MIN_TOTAL`) esto puede recortar el holdout por debajo de 3; es un
    compromiso deliberado (documentado aquí, no un fallo): sin él, un
    método como `regresion_lineal` en su propio piso de 4 puntos se
    quedaría con apenas 1 punto de entrenamiento, insuficiente para ajustar
    una recta.
    """
    crudo = max(3, round(n * 0.2))
    techo_train = max(minimo_train, 1)
    return max(1, min(crudo, n - techo_train))


def backtest(valores: Sequence[float], metodo: str, horizonte: int) -> dict[str, Any]:
    """Back-test de `metodo` sobre `valores`: aparta el tramo final como
    "holdout" (`_tamano_holdout`, recortado a `horizonte` si el holdout
    natural resultara más largo — útil para no penalizar un método por
    errores lejos del horizonte real de interés), entrena `metodo` con el
    resto ("entrenamiento") y pronostica hacia adelante tantos puntos como
    tenga el holdout.

    Devuelve `{"mae": float, "residuos": list[float], "n_holdout": int,
    "n_entrenamiento": int}`. `residuos[i] = pronóstico[i] - real[i]`
    (positivo = sobreestimó, negativo = subestimó), en orden cronológico
    del holdout.
    """
    if metodo not in _MIN_TOTAL:
        raise ValueError(f"método desconocido: '{metodo}'.")
    if horizonte < 1:
        raise ValueError("'horizonte' debe ser un entero positivo.")
    n = len(valores)
    minimo_total = _MIN_TOTAL[metodo]
    if n < minimo_total:
        raise ValueError(
            f"'{metodo}' necesita al menos {minimo_total} valores en la serie (llegaron {n})."
        )

    n_holdout = min(_tamano_holdout(n, _MIN_TRAIN[metodo]), horizonte)
    entrenamiento = list(valores[: n - n_holdout])
    real = list(valores[n - n_holdout :])

    pronostico = _pronosticar_con_metodo(entrenamiento, metodo, len(real))
    residuos = [p - r for p, r in zip(pronostico, real, strict=True)]
    mae = sum(abs(r) for r in residuos) / len(residuos)

    return {
        "mae": _round(mae),
        "residuos": [_round(r) for r in residuos],
        "n_holdout": n_holdout,
        "n_entrenamiento": len(entrenamiento),
    }


def elegir_mejor_metodo(valores: Sequence[float]) -> dict[str, Any]:
    """Corre `backtest` sobre los métodos aplicables según el tamaño de la
    serie (`holt` exige ≥8 puntos, `regresion_lineal` ≥4, `media_movil` y
    `suavizado_exponencial` ≥3 — `_MIN_TOTAL`) y devuelve el de menor MAE.

    Devuelve `{"metodo": str, "candidatos": {metodo: resultado_de_backtest}}`.
    Desempate determinista si dos métodos empatan en MAE exacto (típico en
    una serie constante, donde los cuatro métodos predicen perfecto): gana
    el primero en `_ORDEN_METODOS` (el más simple).
    """
    n = len(valores)
    aplicables = [m for m in _ORDEN_METODOS if n >= _MIN_TOTAL[m]]
    if not aplicables:
        minimo = min(_MIN_TOTAL.values())
        raise ValueError(
            f"Se necesitan al menos {minimo} valores para elegir un método de predicción "
            f"(llegaron {n})."
        )

    candidatos = {m: backtest(valores, m, horizonte=n) for m in aplicables}
    mejor = min(aplicables, key=lambda m: (candidatos[m]["mae"], _ORDEN_METODOS.index(m)))
    return {"metodo": mejor, "candidatos": candidatos}


def predecir(valores: Any, periodos: int) -> dict[str, Any]:
    """Pronostica `periodos` puntos futuros de `valores` con el mejor método
    (`elegir_mejor_metodo`) y un intervalo aproximado alrededor de cada
    punto: `±1.96 · desviación_estándar(residuos_del_backtest)` — heurística
    del ~95% asumiendo errores aproximadamente normales, **no** un intervalo
    de confianza riguroso derivado del modelo (ver docstring del módulo).
    Serie constante ⇒ residuos del backtest todos en cero ⇒ desviación 0 ⇒
    intervalo de ancho 0 (no un error).

    Bordes: filtra `None`/NaN de `valores` (con aviso), exige entre 3 y 500
    valores numéricos válidos tras filtrar (`ValueError` claro si no).
    `periodos` debe ser un `int` entre 1 y 24.

    Devuelve un `dict` con `metodo`, `metodo_legible`, `mae` (del método
    elegido), `candidatos_mae` (MAE de cada método evaluado), `n_holdout`,
    `predicciones`, `intervalo_inferior`, `intervalo_superior`, `margen`,
    `pendiente`/`intercepto`/`r2` (solo si `metodo == "regresion_lineal"`,
    si no `None`), `tendencia` (solo si `metodo == "holt"`, si no `None`) y
    `aviso` (o `None`).
    """
    limpios, aviso = _normalizar_valores(valores, minimo=3, maximo=500)

    if isinstance(periodos, bool) or not isinstance(periodos, int):
        raise ValueError("'periodos' debe ser un número entero.")
    if not (_PERIODOS_MIN <= periodos <= _PERIODOS_MAX):
        raise ValueError(f"'periodos' debe estar entre {_PERIODOS_MIN} y {_PERIODOS_MAX}.")

    seleccion = elegir_mejor_metodo(limpios)
    metodo = seleccion["metodo"]
    info = seleccion["candidatos"][metodo]
    residuos = info["residuos"]

    desviacion = statistics.pstdev(residuos) if residuos else 0.0
    margen = _round(1.96 * desviacion)

    predicciones = [_round(p) for p in _pronosticar_con_metodo(limpios, metodo, periodos)]

    resultado: dict[str, Any] = {
        "metodo": metodo,
        "metodo_legible": _METODOS_LEGIBLES[metodo],
        "mae": info["mae"],
        "candidatos_mae": {m: c["mae"] for m, c in seleccion["candidatos"].items()},
        "n_holdout": info["n_holdout"],
        "predicciones": predicciones,
        "intervalo_inferior": [_round(p - margen) for p in predicciones],
        "intervalo_superior": [_round(p + margen) for p in predicciones],
        "margen": margen,
        "pendiente": None,
        "intercepto": None,
        "r2": None,
        "tendencia": None,
        "aviso": aviso,
    }

    if metodo == "regresion_lineal":
        pendiente, intercepto, r2 = regresion_lineal(limpios)
        resultado["pendiente"] = _round(pendiente)
        resultado["intercepto"] = _round(intercepto)
        resultado["r2"] = _round(r2)
    elif metodo == "holt":
        _niveles, tendencias = holt(limpios, _ALFA_HOLT_DEFECTO, _BETA_HOLT_DEFECTO)
        resultado["tendencia"] = _round(tendencias[-1])

    return resultado


# ---------------------------------------------------------------------------
# Anomalías — funciones puras
# ---------------------------------------------------------------------------


def detectar_outliers_iqr(valores: Sequence[float], umbral: float = 1.5) -> list[dict[str, Any]]:
    """Outliers por rango intercuartílico. Mismos cuartiles "a la Tukey"
    (mediana de cada mitad de la lista ordenada, excluyendo la mediana
    global cuando `n` es impar) que usa `edecan_docanalysis.tablas`.

    Un valor es atípico si cae fuera de `[Q1 - umbral·IQR, Q3 + umbral·IQR]`.
    `score = (valor - mediana) / IQR` (positivo = por encima, negativo = por
    debajo, en unidades de IQR). Serie sin dispersión (`IQR == 0`) ⇒ sin
    atípicos (`[]`) — nunca división por cero.
    """
    if umbral <= 0:
        raise ValueError("'umbral' debe ser un número positivo.")
    n = len(valores)
    if n < 4:
        raise ValueError("Se necesitan al menos 4 valores para calcular cuartiles (IQR).")

    ordenados = sorted(valores)
    mitad = n // 2
    inferior = ordenados[:mitad]
    superior = ordenados[mitad + 1 :] if n % 2 else ordenados[mitad:]
    q1 = statistics.median(inferior)
    q3 = statistics.median(superior)
    iqr = q3 - q1
    if iqr <= 0:
        return []

    mediana = statistics.median(ordenados)
    piso = q1 - umbral * iqr
    techo = q3 + umbral * iqr
    return [
        {"indice": i, "valor": v, "score": _round((v - mediana) / iqr)}
        for i, v in enumerate(valores)
        if v < piso or v > techo
    ]


def detectar_outliers_zscore(valores: Sequence[float], umbral: float = 3.0) -> list[dict[str, Any]]:
    """Outliers por z-score: `score = (valor - media) / desviación_estándar`
    (desviación MUESTRAL, `statistics.stdev` — mismo criterio que
    `tablas.py`). Serie sin dispersión (`desviación == 0`) ⇒ sin atípicos.
    """
    if umbral <= 0:
        raise ValueError("'umbral' debe ser un número positivo.")
    n = len(valores)
    if n < 2:
        raise ValueError("Se necesitan al menos 2 valores para calcular un z-score.")

    media = statistics.mean(valores)
    desviacion = statistics.stdev(valores)
    if desviacion == 0:
        return []

    resultado: list[dict[str, Any]] = []
    for i, v in enumerate(valores):
        z = (v - media) / desviacion
        if abs(z) > umbral:
            resultado.append({"indice": i, "valor": v, "score": _round(z)})
    return resultado


def detectar_rachas(valores: Sequence[float], minimo: int = _MINIMO_RACHA) -> list[dict[str, Any]]:
    """Rachas de al menos `minimo` valores CONSECUTIVOS todos por encima, o
    todos por debajo, de la mediana de toda la serie (un valor exactamente
    igual a la mediana no cuenta para ningún lado y corta la racha en
    curso) — señal simple de cambio de régimen sostenido. Lectura básica
    honesta: es una heurística de racha estadística, NO un modelo de
    detección de fraude (ver docstring del módulo).

    Devuelve una lista de `{"inicio": int, "fin": int, "longitud": int,
    "direccion": "por_encima" | "por_debajo"}` (índices 0-based inclusivos,
    en orden de aparición).
    """
    if minimo < 1:
        raise ValueError("'minimo' debe ser un entero positivo.")
    n = len(valores)
    if n == 0:
        return []

    mediana = statistics.median(valores)
    lados: list[str | None] = [
        "por_encima" if v > mediana else "por_debajo" if v < mediana else None for v in valores
    ]

    rachas: list[dict[str, Any]] = []
    i = 0
    while i < n:
        lado = lados[i]
        if lado is None:
            i += 1
            continue
        j = i
        while j < n and lados[j] == lado:
            j += 1
        longitud = j - i
        if longitud >= minimo:
            rachas.append({"inicio": i, "fin": j - 1, "longitud": longitud, "direccion": lado})
        i = j
    return rachas


def detectar_anomalias(
    valores: Any, metodo: str = "iqr", umbral: float | None = None
) -> dict[str, Any]:
    """Detecta outliers (`metodo`: `"iqr"` o `"zscore"`) y rachas en
    `valores`. `umbral` por defecto: 1.5 (iqr) o 3.0 (zscore) — el estándar
    de facto de cada método.

    Bordes: filtra `None`/NaN de `valores` (con aviso, mismo criterio que
    `predecir`), exige entre 4 y 1000 valores numéricos válidos tras
    filtrar.

    Devuelve `{"metodo", "umbral", "n", "outliers", "rachas", "aviso"}`.
    """
    limpios, aviso = _normalizar_valores(valores, minimo=4, maximo=1000)

    if metodo not in ("iqr", "zscore"):
        raise ValueError("'metodo' debe ser 'iqr' o 'zscore'.")

    if umbral is None:
        umbral_final = _UMBRAL_ANOMALIA_DEFECTO[metodo]
    else:
        try:
            umbral_final = float(umbral)
        except (TypeError, ValueError):
            raise ValueError("'umbral' debe ser un número.") from None
    if umbral_final <= 0:
        raise ValueError("'umbral' debe ser un número positivo.")

    if metodo == "iqr":
        outliers = detectar_outliers_iqr(limpios, umbral_final)
    else:
        outliers = detectar_outliers_zscore(limpios, umbral_final)

    rachas = detectar_rachas(limpios, _MINIMO_RACHA)

    return {
        "metodo": metodo,
        "umbral": umbral_final,
        "n": len(limpios),
        "outliers": outliers,
        "rachas": rachas,
        "aviso": aviso,
    }


# ---------------------------------------------------------------------------
# Tools del agente
# ---------------------------------------------------------------------------


def _resumen_prediccion(resultado: dict[str, Any], etiquetas: list[str] | None) -> str:
    lineas = [
        f"Elegí «{resultado['metodo_legible']}» para predecir (menor error absoluto medio "
        f"en un backtest interno sobre el tramo final de la serie: MAE={resultado['mae']})."
    ]
    otros = {m: mae for m, mae in resultado["candidatos_mae"].items() if m != resultado["metodo"]}
    if otros:
        detalle = ", ".join(f"{_METODOS_LEGIBLES[m]} MAE={mae}" for m, mae in otros.items())
        lineas.append(f"Otros métodos evaluados: {detalle}.")

    if etiquetas:
        lineas.append(f"Serie histórica hasta «{etiquetas[-1]}» ({len(etiquetas)} puntos).")

    lineas.append("")
    lineas.append("Predicciones:")
    for i, (p, lo, hi) in enumerate(
        zip(
            resultado["predicciones"],
            resultado["intervalo_inferior"],
            resultado["intervalo_superior"],
            strict=True,
        )
    ):
        lineas.append(f"- t+{i + 1}: {p} (intervalo aprox. [{lo}, {hi}])")

    if resultado["r2"] is not None:
        lineas.append("")
        lineas.append(
            f"Tendencia lineal de la serie: pendiente={resultado['pendiente']} por período, "
            f"r²={resultado['r2']}."
        )
    elif resultado["tendencia"] is not None:
        lineas.append("")
        lineas.append(f"Tendencia estimada (Holt): {resultado['tendencia']} por período.")

    if resultado["aviso"]:
        lineas.append("")
        lineas.append(resultado["aviso"])

    lineas.append("")
    lineas.append(DISCLAIMER_FORECAST)
    return "\n".join(lineas)


class PredecirSerieTool(Tool):
    name = "predecir_serie"
    description = (
        "Predice los próximos valores de una serie numérica (ventas, métricas de "
        "negocio, cualquier serie temporal) probando media móvil, regresión lineal, "
        "suavizado exponencial simple y de Holt, y usando el que mejor predijo un "
        "backtest interno sobre el tramo final de la propia serie. Incluye un "
        "intervalo aproximado alrededor de cada predicción. Es una proyección "
        "estadística informativa a partir de los datos dados, no asesoría "
        "financiera ni garantía de resultados futuros."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "valores": {
                "type": "array",
                "items": {"type": "number"},
                "minItems": 3,
                "maxItems": 500,
                "description": (
                    "Serie histórica de números en orden cronológico (el más "
                    "antiguo primero, el más reciente al final)."
                ),
            },
            "periodos": {
                "type": "integer",
                "minimum": _PERIODOS_MIN,
                "maximum": _PERIODOS_MAX,
                "default": _PERIODOS_DEFECTO,
                "description": "Cuántos períodos futuros predecir (1 a 24, por defecto 6).",
            },
            "etiquetas": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Etiquetas opcionales de cada valor histórico (misma longitud "
                    "que 'valores'; p. ej. nombres de mes). Los períodos futuros "
                    "siempre se etiquetan 't+1'…'t+n': no hay forma general de "
                    "inferir qué etiqueta sigue después de una etiqueta arbitraria."
                ),
            },
        },
        "required": ["valores"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        valores = args.get("valores")
        if not isinstance(valores, list) or not valores:
            return ToolResult(content="Necesito 'valores': una lista de al menos 3 números.")

        etiquetas, error_etiquetas = _validar_etiquetas(args.get("etiquetas"), len(valores))
        if error_etiquetas:
            return ToolResult(content=error_etiquetas)

        periodos = clamp_int(
            args.get("periodos"),
            default=_PERIODOS_DEFECTO,
            minimo=_PERIODOS_MIN,
            maximo=_PERIODOS_MAX,
        )

        try:
            resultado = predecir(valores, periodos)
        except ValueError as exc:
            return ToolResult(content=str(exc))

        return ToolResult(content=_resumen_prediccion(resultado, etiquetas), data=resultado)


def _resumen_anomalias(resultado: dict[str, Any], etiquetas: list[str] | None) -> str:
    n = resultado["n"]
    outliers = resultado["outliers"]
    nombre_metodo = "rango intercuartílico (IQR)" if resultado["metodo"] == "iqr" else "z-score"

    lineas = [
        f"Analicé {n} valores con {nombre_metodo} (umbral={resultado['umbral']}): "
        f"{len(outliers)} de {n} puntos se salen del patrón."
    ]
    for o in outliers:
        etiqueta = etiquetas[o["indice"]] if etiquetas else f"posición {o['indice'] + 1}"
        lineas.append(f"- {etiqueta}: valor={o['valor']}, score={o['score']}")

    rachas = resultado["rachas"]
    lineas.append("")
    if rachas:
        lineas.append("Rachas detectadas (posible cambio de régimen sostenido):")
        for r in rachas:
            lado = "por encima" if r["direccion"] == "por_encima" else "por debajo"
            ini = etiquetas[r["inicio"]] if etiquetas else f"posición {r['inicio'] + 1}"
            fin = etiquetas[r["fin"]] if etiquetas else f"posición {r['fin'] + 1}"
            lineas.append(
                f"- {r['longitud']} valores seguidos {lado} de la mediana, de {ini} a {fin}."
            )
    else:
        lineas.append("Sin rachas de 5 o más valores consecutivos por el mismo lado de la mediana.")

    if resultado["aviso"]:
        lineas.append("")
        lineas.append(resultado["aviso"])

    return "\n".join(lineas)


class DetectarAnomaliasTool(Tool):
    name = "detectar_anomalias"
    description = (
        "Detecta valores atípicos en una serie numérica (rango intercuartílico o "
        "z-score) y rachas de al menos 5 valores seguidos por encima o por debajo "
        "de la mediana — señal simple de cambio de régimen sostenido. Es una "
        "heurística estadística general de 'algo raro en los datos', NO un modelo "
        "de detección de fraude con aprendizaje automático."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "valores": {
                "type": "array",
                "items": {"type": "number"},
                "minItems": 4,
                "maxItems": 1000,
                "description": "Serie de números a analizar.",
            },
            "metodo": {
                "type": "string",
                "enum": ["iqr", "zscore"],
                "default": "iqr",
                "description": "'iqr' (rango intercuartílico, por defecto) o 'zscore'.",
            },
            "umbral": {
                "type": "number",
                "description": "Umbral de sensibilidad. Por defecto 1.5 (iqr) o 3.0 (zscore).",
            },
            "etiquetas": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Etiquetas opcionales de cada valor (misma longitud que 'valores').",
            },
        },
        "required": ["valores"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        valores = args.get("valores")
        if not isinstance(valores, list) or not valores:
            return ToolResult(content="Necesito 'valores': una lista de al menos 4 números.")

        etiquetas, error_etiquetas = _validar_etiquetas(args.get("etiquetas"), len(valores))
        if error_etiquetas:
            return ToolResult(content=error_etiquetas)

        metodo = str(args.get("metodo") or "iqr").strip().lower()
        umbral_arg = args.get("umbral")

        try:
            resultado = detectar_anomalias(valores, metodo, umbral_arg)
        except ValueError as exc:
            return ToolResult(content=str(exc))

        return ToolResult(content=_resumen_anomalias(resultado, etiquetas), data=resultado)
