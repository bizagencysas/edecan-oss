"""Bloques ricos del IDE: las herramientas que los emiten y el portero del canal.

Qué resuelve
------------
El agente del IDE solo sabía hablar en texto. Cuando comparaba tres opciones o
medía algo a lo largo del tiempo, escribía una tabla en Markdown -- que en el
teléfono se ve como barras y guiones, porque el hilo del IDE pinta texto plano
y ``AttributedString(markdown:)`` tampoco renderiza tablas. Este módulo le da
dos formas de mostrar datos de verdad: ``mostrar_tabla`` y ``mostrar_grafica``.

El canal (lo que de verdad importa acá)
---------------------------------------
Un bloque NUNCA nace del resultado que una herramienta le devuelve al modelo.
Ese resultado es texto que el modelo produjo o leyó de un archivo del repo; si
pudiera acuñar UI, bastaría con que un README trajera la forma correcta para
pintarle una tarjeta a la persona. El único camino es el canal deliberado: la
clave ``presentation`` del evento de sesión (ver
``ide_sessions.Session.append``), y todo lo que entra por ahí pasa antes por
``validar_bloques`` de este módulo. Es el mismo patrón que
``edecan_core.tools.base.ToolResult.presentation`` en el chat.

Por qué se valida acá y no con Pydantic
---------------------------------------
``edecan_schemas.ide_blocks`` es el contrato en el cable, y el servidor lo
aplica de nuevo antes de que nada llegue al teléfono (ver
``edecan_api.routers.ide``). Pero el companion se instala SOLO en la máquina de
la persona y no depende de ningún otro paquete del monorepo a propósito (ver
su ``pyproject.toml``), así que acá la misma regla vive en Python puro. La
duplicación es deliberada y está cubierta: ``tests/test_ide_bloques.py``
compara ambas implementaciones caso por caso cuando ``edecan_schemas`` está
instalado, y falla si alguna se mueve sin la otra.
"""

from __future__ import annotations

import math
import re
from typing import Any

from edecan_llm.base import ToolSpec

# Espejo EXACTO de ``edecan_schemas.ide_blocks``. Los nombres se repiten letra
# por letra para que una diferencia salte a la vista en el test de espejo.
MAX_TABLE_COLUMNS = 8
MAX_TABLE_ROWS = 30
MAX_TABLE_CELL_CHARS = 120
MAX_CHART_SERIES = 6
MAX_CHART_POINTS = 60
MIN_SINGLE_SERIES_POINTS = 3
MAX_BLOCKS_PER_EVENT = 3

MAX_TITULO_CHARS = 120
MAX_COLUMNA_TITULO_CHARS = 60
MAX_ETIQUETA_CHARS = 40
MAX_NOTA_CHARS = 300
MAX_FALLBACK_CHARS = 4000
# Filas/puntos que entran en el texto de respaldo. El respaldo existe para que
# un cliente que todavía no dibuja bloques -- y el historial que se le
# reinyecta al modelo, y el /export -- vean los datos reales; no para
# reproducir la tabla entera dos veces en el mismo evento.
MAX_FILAS_EN_RESPALDO = 12
MAX_PUNTOS_EN_RESPALDO = 12

ALINEACIONES = ("left", "center", "right")
_TIPOS_GRAFICA = {"barras": "bar", "lineas": "line", "líneas": "line"}
# Idéntico al `pattern` de `TableColumn.key` en `edecan_schemas.ide_blocks`.
# ASCII a propósito, aunque el título de la columna sí lleve acentos: la clave
# es un identificador que viaja como llave de objeto JSON entre Python, Swift y
# TypeScript, y "año" contra "ano" es la clase de diferencia que solo se nota
# cuando la celda ya salió vacía en el teléfono de alguien.
_CLAVE_COLUMNA = re.compile(r"[a-zA-Z0-9_][a-zA-Z0-9_.-]*")

NOMBRES_TOOLS = frozenset({"mostrar_tabla", "mostrar_grafica"})


class IDEBloqueError(ValueError):
    """Bloque que no se puede dibujar, con el motivo escrito para el modelo.

    El mensaje viaja tal cual al resultado de la herramienta: es lo único que
    el modelo va a leer para decidir qué hacer distinto, así que dice qué está
    mal Y qué hacer en su lugar. Un "argumento inválido" pelado le enseñaría a
    reintentar lo mismo.
    """


# ---------------------------------------------------------------------------
# La regla dura de la gráfica
# ---------------------------------------------------------------------------


def motivo_grafica_degenerada(series: list[dict[str, Any]]) -> str | None:
    """Por qué estos datos NO forman una gráfica, o ``None`` si sí la forman.

    Espejo de ``edecan_schemas.ide_blocks.motivo_grafica_degenerada``.

    Una gráfica que no se entiende es peor que una tabla: ocupa más pantalla,
    se lee más lento y encima parece un análisis. El modelo no tiene cómo
    saberlo -- graficar siempre se le ve como el gesto más presentable -- así
    que la regla vive en código y no en un consejo del prompt que se obedece a
    veces. ``series`` ya viene normalizada (``[{"name": str, "points":
    [{"label": str, "value": float}]}]``).
    """
    if len(series) == 1 and len(series[0]["points"]) < MIN_SINGLE_SERIES_POINTS:
        return (
            f"una sola serie con {len(series[0]['points'])} puntos no es una gráfica: dos "
            "puntos son una diferencia, no una forma. Dilo en una frase o muéstralo con "
            "una tabla."
        )

    referencia = [punto["label"] for punto in series[0]["points"]]
    for serie in series[1:]:
        if [punto["label"] for punto in serie["points"]] != referencia:
            return (
                f"la serie «{serie['name']}» no usa las mismas etiquetas del eje que "
                f"«{series[0]['name']}». Varias series solo se pueden comparar sobre el mismo "
                "eje; si miden cosas distintas, son gráficas distintas (o una tabla)."
            )

    valores = {punto["value"] for serie in series for punto in serie["points"]}
    if len(valores) == 1:
        return (
            "todos los valores son idénticos: la gráfica sería una línea plana o barras "
            "iguales, y no muestra nada que una frase no diga mejor."
        )
    return None


# ---------------------------------------------------------------------------
# Normalización de argumentos del modelo
# ---------------------------------------------------------------------------


def _texto(valor: Any, *, campo: str, maximo: int, obligatorio: bool = True) -> str:
    if valor is None and not obligatorio:
        return ""
    if not isinstance(valor, str):
        raise IDEBloqueError(f"'{campo}' debe ser texto.")
    limpio = " ".join(valor.split())
    if not limpio and obligatorio:
        raise IDEBloqueError(f"'{campo}' no puede ir vacío.")
    return limpio[:maximo]


def recortar_celda(valor: str) -> str:
    """Recorta a ``MAX_TABLE_CELL_CHARS`` DEJANDO la marca del recorte.

    Espejo de ``edecan_schemas.ide_blocks.recortar_celda``.

    Sin el "…", una ruta larga, una URL o un mensaje de error cortado se leen
    como el valor completo: la celda no revienta, se ve perfecta y dice otra
    cosa -- el mismo fracaso que las filas por posición vienen a evitar. El
    resultado queda en exactamente ``MAX_TABLE_CELL_CHARS`` para que las
    revalidaciones de aguas abajo (servidor, iPhone, web), que recortan al
    mismo tope, no se coman justo la marca que avisa del corte.
    """
    if len(valor) <= MAX_TABLE_CELL_CHARS:
        return valor
    return valor[: MAX_TABLE_CELL_CHARS - 1] + "…"


def _celda(valor: Any) -> str:
    """Convierte el valor de una celda a texto SIN inventar formato.

    Los números llegan como números cuando el modelo los toma de una medición
    real, y como texto cuando ya vienen formateados ("1.2 s", "3 %"). Un
    ``float`` se escribe con ``repr`` y no redondeado a propósito: redondear
    acá cambiaría el dato medido por uno más bonito, y quien lee la tabla no
    tendría forma de notarlo.
    """
    if valor is None:
        return ""
    if isinstance(valor, bool):
        return "sí" if valor else "no"
    if isinstance(valor, str):
        return recortar_celda(" ".join(valor.split()))
    if isinstance(valor, int):
        return str(valor)
    if isinstance(valor, float):
        if not math.isfinite(valor):
            raise IDEBloqueError("una celda no puede ser NaN ni infinito.")
        return repr(valor)
    raise IDEBloqueError("una celda solo puede ser texto, número o booleano.")


def _numero(valor: Any, *, campo: str) -> float:
    if isinstance(valor, bool) or not isinstance(valor, int | float):
        raise IDEBloqueError(f"'{campo}' debe ser un número.")
    numero = float(valor)
    if not math.isfinite(numero):
        raise IDEBloqueError(f"'{campo}' no puede ser NaN ni infinito.")
    return numero


def _bloque_tabla(args: dict[str, Any]) -> dict[str, Any]:
    columnas_crudas = args.get("columnas")
    if not isinstance(columnas_crudas, list) or not columnas_crudas:
        raise IDEBloqueError("'columnas' debe traer al menos una columna.")
    if len(columnas_crudas) > MAX_TABLE_COLUMNS:
        raise IDEBloqueError(
            f"una tabla no puede tener más de {MAX_TABLE_COLUMNS} columnas en una pantalla "
            "de teléfono. Quédate con las que de verdad se comparan."
        )

    columnas: list[dict[str, str]] = []
    claves: list[str] = []
    for cruda in columnas_crudas:
        if not isinstance(cruda, dict):
            raise IDEBloqueError("cada columna es un objeto con 'clave' y 'titulo'.")
        clave = _texto(cruda.get("clave"), campo="clave", maximo=40)
        if not _CLAVE_COLUMNA.fullmatch(clave):
            raise IDEBloqueError(
                f"la clave «{clave}» no sirve como identificador: usa letras ASCII sin "
                "acentos, números, '_', '-' o '.' (es la clave con la que las filas la "
                "buscan; el acento va en 'titulo', que es lo que se ve)."
            )
        if clave in claves:
            raise IDEBloqueError(f"la clave «{clave}» está repetida en dos columnas.")
        alineacion = cruda.get("alineacion") or "left"
        if alineacion not in ALINEACIONES:
            raise IDEBloqueError(f"'alineacion' solo puede ser {', '.join(ALINEACIONES)}.")
        claves.append(clave)
        columnas.append(
            {
                "key": clave,
                "title": _texto(
                    cruda.get("titulo") or clave, campo="titulo", maximo=MAX_COLUMNA_TITULO_CHARS
                ),
                "align": alineacion,
            }
        )

    filas_crudas = args.get("filas")
    if not isinstance(filas_crudas, list) or not filas_crudas:
        raise IDEBloqueError("'filas' debe traer al menos una fila con datos.")

    permitidas = set(claves)
    filas: list[dict[str, str]] = []
    for cruda in filas_crudas[:MAX_TABLE_ROWS]:
        if not isinstance(cruda, dict):
            raise IDEBloqueError(
                "cada fila es un objeto con las claves de las columnas "
                '(ej. {"proveedor": "Workers AI", "costo": "0.11"}), no una lista.'
            )
        # Una clave que no es de ninguna columna se descarta: no hay dónde
        # dibujarla, y dejarla pasar convertiría el canal de UI en transporte
        # de datos arbitrarios. Una celda ausente o vacía es "sin dato" y solo
        # afecta a SU columna -- ver el docstring de `TableBlock` en
        # `edecan_schemas.ide_blocks` sobre por qué las filas van por clave y
        # no por posición.
        fila: dict[str, str] = {}
        for clave, valor in cruda.items():
            if clave not in permitidas:
                continue
            celda = _celda(valor)
            if celda:
                fila[clave] = celda
        filas.append(fila)
    if not any(filas):
        raise IDEBloqueError(
            "ninguna fila trae una celda con datos para las columnas que declaraste: "
            "revisa que las claves de las filas sean las mismas de 'columnas'."
        )

    omitidas = max(0, len(filas_crudas) - len(filas))
    nota = _texto(args.get("nota"), campo="nota", maximo=MAX_NOTA_CHARS, obligatorio=False)
    if omitidas:
        aviso = f"Se muestran {len(filas)} de {len(filas_crudas)} filas."
        nota = f"{aviso} {nota}".strip()[:MAX_NOTA_CHARS]

    bloque: dict[str, Any] = {
        "schema_version": 1,
        "type": "table",
        "title": _texto(args.get("titulo"), campo="titulo", maximo=MAX_TITULO_CHARS)
        if args.get("titulo")
        else None,
        "columns": columnas,
        "rows": filas,
        "note": nota or None,
        "fallback_text": "",
    }
    bloque["fallback_text"] = _respaldo_tabla(bloque)
    return bloque


def _bloque_grafica(args: dict[str, Any]) -> dict[str, Any]:
    tipo_crudo = str(args.get("tipo") or "").strip().lower()
    tipo = _TIPOS_GRAFICA.get(tipo_crudo)
    if tipo is None:
        raise IDEBloqueError("'tipo' solo puede ser 'barras' o 'lineas'.")

    series_crudas = args.get("series")
    if not isinstance(series_crudas, list) or not series_crudas:
        raise IDEBloqueError("'series' debe traer al menos una serie con puntos.")
    if len(series_crudas) > MAX_CHART_SERIES:
        raise IDEBloqueError(
            f"más de {MAX_CHART_SERIES} series no se distinguen en una pantalla de "
            "teléfono; agrupa o parte la gráfica."
        )

    series: list[dict[str, Any]] = []
    nombres: list[str] = []
    for cruda in series_crudas:
        if not isinstance(cruda, dict):
            raise IDEBloqueError("cada serie es un objeto con 'nombre' y 'puntos'.")
        nombre = _texto(cruda.get("nombre"), campo="nombre", maximo=MAX_COLUMNA_TITULO_CHARS)
        if nombre in nombres:
            raise IDEBloqueError(f"dos series se llaman «{nombre}»; ponles nombres distintos.")
        nombres.append(nombre)
        puntos_crudos = cruda.get("puntos")
        if not isinstance(puntos_crudos, list):
            raise IDEBloqueError(f"'puntos' de la serie «{nombre}» debe ser una lista.")
        if len(puntos_crudos) > MAX_CHART_POINTS:
            raise IDEBloqueError(
                f"la serie «{nombre}» trae {len(puntos_crudos)} puntos y el máximo es "
                f"{MAX_CHART_POINTS}; agrega por periodo o quédate con el tramo que importa."
            )
        puntos: list[dict[str, Any]] = []
        etiquetas: list[str] = []
        for punto in puntos_crudos:
            if not isinstance(punto, dict):
                raise IDEBloqueError("cada punto es un objeto con 'etiqueta' y 'valor'.")
            etiqueta = _texto(punto.get("etiqueta"), campo="etiqueta", maximo=MAX_ETIQUETA_CHARS)
            if etiqueta in etiquetas:
                raise IDEBloqueError(
                    f"la serie «{nombre}» repite la etiqueta «{etiqueta}»: dos barras con el "
                    "mismo nombre no se pueden distinguir."
                )
            etiquetas.append(etiqueta)
            puntos.append({"label": etiqueta, "value": _numero(punto.get("valor"), campo="valor")})
        if len(puntos) < 2:
            raise IDEBloqueError(
                f"la serie «{nombre}» tiene {len(puntos)} punto(s): una gráfica de un solo "
                "punto no dibuja nada. Dilo en una frase."
            )
        series.append({"name": nombre, "points": puntos})

    motivo = motivo_grafica_degenerada(series)
    if motivo is not None:
        raise IDEBloqueError(
            f"{motivo} Si igual quieres que la persona vea los números, llama "
            "'mostrar_tabla' con estos mismos datos."
        )

    bloque: dict[str, Any] = {
        "schema_version": 1,
        "type": "chart",
        "chart_kind": tipo,
        "title": _texto(args.get("titulo"), campo="titulo", maximo=MAX_TITULO_CHARS),
        "series": series,
        "x_label": _texto(
            args.get("eje_x"), campo="eje_x", maximo=MAX_COLUMNA_TITULO_CHARS, obligatorio=False
        )
        or None,
        "y_label": _texto(
            args.get("eje_y"), campo="eje_y", maximo=MAX_COLUMNA_TITULO_CHARS, obligatorio=False
        )
        or None,
        "note": _texto(args.get("nota"), campo="nota", maximo=MAX_NOTA_CHARS, obligatorio=False)
        or None,
        "fallback_text": "",
    }
    bloque["fallback_text"] = _respaldo_grafica(bloque)
    return bloque


# ---------------------------------------------------------------------------
# Texto de respaldo
# ---------------------------------------------------------------------------


def _respaldo_tabla(bloque: dict[str, Any]) -> str:
    """Rinde la tabla como texto legible con los datos REALES.

    Se arma acá y no se le pide al modelo a propósito: un resumen que escribe
    el modelo puede no coincidir con lo que muestra la tarjeta, y entonces la
    persona con un cliente viejo lee otra cosa que la persona con uno nuevo.
    """
    columnas = bloque["columns"]
    lineas: list[str] = []
    if bloque.get("title"):
        lineas.append(str(bloque["title"]))
    lineas.append(" | ".join(str(columna["title"]) for columna in columnas))
    lineas.append(" | ".join("---" for _ in columnas))
    for fila in bloque["rows"][:MAX_FILAS_EN_RESPALDO]:
        lineas.append(" | ".join(str(fila.get(columna["key"], "—")) for columna in columnas))
    restantes = len(bloque["rows"]) - MAX_FILAS_EN_RESPALDO
    if restantes > 0:
        lineas.append(f"(+{restantes} filas más en la tabla)")
    if bloque.get("note"):
        lineas.append(str(bloque["note"]))
    return "\n".join(lineas)[:MAX_FALLBACK_CHARS]


def _respaldo_grafica(bloque: dict[str, Any]) -> str:
    tipo = "barras" if bloque["chart_kind"] == "bar" else "líneas"
    lineas = [f"Gráfica de {tipo}: {bloque['title']}"]
    if bloque.get("y_label"):
        lineas.append(f"Eje vertical: {bloque['y_label']}")
    for serie in bloque["series"]:
        puntos = ", ".join(
            f"{punto['label']} {_valor_legible(punto['value'])}"
            for punto in serie["points"][:MAX_PUNTOS_EN_RESPALDO]
        )
        restantes = len(serie["points"]) - MAX_PUNTOS_EN_RESPALDO
        if restantes > 0:
            puntos = f"{puntos} (+{restantes} más)"
        lineas.append(f"{serie['name']}: {puntos}")
    if bloque.get("note"):
        lineas.append(str(bloque["note"]))
    return "\n".join(lineas)[:MAX_FALLBACK_CHARS]


def _valor_legible(valor: float) -> str:
    return str(int(valor)) if float(valor).is_integer() else repr(valor)


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------


def construir_bloque(nombre: str, args: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(bloque, resultado_para_el_modelo)`` para una llamada a una tool de acá.

    Lanza ``IDEBloqueError`` si los datos no se pueden dibujar -- y entonces NO
    se escribe ningún evento: la persona no ve una tarjeta a medias, y el
    modelo recibe el motivo con qué hacer en su lugar.
    """
    if nombre == "mostrar_tabla":
        bloque = _bloque_tabla(args)
        resultado = {
            "ok": True,
            "mostrado": "tabla",
            "columnas": len(bloque["columns"]),
            "filas": len(bloque["rows"]),
        }
    elif nombre == "mostrar_grafica":
        bloque = _bloque_grafica(args)
        resultado = {
            "ok": True,
            "mostrado": "grafica",
            "series": len(bloque["series"]),
            "puntos": sum(len(serie["points"]) for serie in bloque["series"]),
        }
    else:
        raise IDEBloqueError(f"Herramienta de presentación desconocida: {nombre}")
    if bloque.get("note"):
        resultado["nota"] = bloque["note"]
    resultado["aviso"] = (
        "La persona ya lo está viendo en pantalla. No repitas estos datos en texto: "
        "en tu respuesta final di qué significan y qué harías con eso."
    )
    return bloque, resultado


def validar_bloques(
    presentation: Any, *, max_bloques: int = MAX_BLOCKS_PER_EVENT
) -> list[dict[str, Any]]:
    """Portero del canal: deja pasar solo bloques que se pueden dibujar.

    Corre en ``ide_sessions.Session.append``, o sea en el único punto por el
    que un bloque puede llegar al hilo, al JSONL y al teléfono. Lo que no
    valida se descarta en silencio en vez de tumbar el evento: el texto del
    evento siempre sobrevive, así que el peor caso es que la persona lea el
    texto en vez de ver la tarjeta.
    """
    if not isinstance(presentation, list):
        return []
    validos: list[dict[str, Any]] = []
    vistos: set[str] = set()
    for crudo in presentation[: max_bloques * 4]:
        if not isinstance(crudo, dict):
            continue
        try:
            bloque = _revalidar(crudo)
        except IDEBloqueError:
            continue
        firma = repr(sorted(bloque.items(), key=lambda item: item[0]))
        if firma in vistos:
            continue
        vistos.add(firma)
        validos.append(bloque)
        if len(validos) >= max_bloques:
            break
    return validos


def _revalidar(bloque: dict[str, Any]) -> dict[str, Any]:
    """Vuelve a construir un bloque YA armado desde su forma en el cable.

    Rearmarlo con los mismos constructores (en vez de revisar campo por campo)
    es lo que garantiza que el portero y el emisor no puedan opinar distinto:
    si algo pasa por acá, es porque ``construir_bloque`` lo habría producido
    igual.
    """
    tipo = bloque.get("type")
    if tipo == "table":
        columnas = bloque.get("columns")
        filas = bloque.get("rows")
        if not isinstance(columnas, list) or not isinstance(filas, list):
            raise IDEBloqueError("tabla mal formada.")
        rearmado = _bloque_tabla(
            {
                "titulo": bloque.get("title"),
                "nota": bloque.get("note"),
                "columnas": [
                    {
                        "clave": columna.get("key"),
                        "titulo": columna.get("title"),
                        "alineacion": columna.get("align") or "left",
                    }
                    for columna in columnas
                    if isinstance(columna, dict)
                ],
                "filas": [fila for fila in filas if isinstance(fila, dict)],
            }
        )
    elif tipo == "chart":
        series = bloque.get("series")
        if not isinstance(series, list):
            raise IDEBloqueError("gráfica mal formada.")
        # Explícito y sin default: un ``chart_kind`` que no se reconoce se
        # rechaza en vez de dibujarse como líneas "por si acaso" -- cambiar el
        # tipo de gráfica en silencio es cambiar lo que la gráfica dice.
        tipo_grafica = {"bar": "barras", "line": "lineas"}.get(str(bloque.get("chart_kind")))
        if tipo_grafica is None:
            raise IDEBloqueError(f"tipo de gráfica no permitido: {bloque.get('chart_kind')!r}")
        rearmado = _bloque_grafica(
            {
                "tipo": tipo_grafica,
                "titulo": bloque.get("title"),
                "eje_x": bloque.get("x_label"),
                "eje_y": bloque.get("y_label"),
                "nota": bloque.get("note"),
                "series": [
                    {
                        "nombre": serie.get("name"),
                        "puntos": [
                            {"etiqueta": punto.get("label"), "valor": punto.get("value")}
                            for punto in serie.get("points") or []
                            if isinstance(punto, dict)
                        ],
                    }
                    for serie in series
                    if isinstance(serie, dict)
                ],
            }
        )
    else:
        raise IDEBloqueError(f"tipo de bloque no permitido: {tipo!r}")
    # El respaldo se recalcula siempre desde los datos ya normalizados: si
    # viniera de afuera, sería texto arbitrario entrando por el canal de UI.
    return rearmado


# ---------------------------------------------------------------------------
# Herramientas del agente
# ---------------------------------------------------------------------------

_DESCRIPCION_TABLA = (
    "Muestra una TABLA de verdad en la pantalla de la persona (se ve como tabla en el "
    "teléfono; una tabla en Markdown ahí se ve como barras y guiones). Úsala cuando haya "
    "3 o más filas que la persona necesite COMPARAR entre sí sobre los mismos campos: "
    "opciones con su costo y su latencia, archivos con su tamaño y su cobertura, endpoints "
    "con su estado. NO la uses para una o dos filas -- eso se dice mejor en una frase, y "
    "una tabla de dos filas ocupa más pantalla de la que informa --, ni para un texto largo "
    "por celda (una tabla no es un lugar donde meter párrafos), ni para volcar un archivo "
    "entero. Los datos tienen que salir de algo que MEDISTE en este turno (la salida de un "
    "comando, un archivo que leíste): una tabla inventada se ve igual de convincente que "
    "una real. La tabla no reemplaza tu respuesta final: ella muestra los datos, tú dices "
    "qué significan."
)

_DESCRIPCION_GRAFICA = (
    "Muestra una GRÁFICA de barras o líneas en la pantalla de la persona. Úsala solo cuando "
    "lo que importa es la FORMA de los datos --una tendencia, un salto, un caso fuera de "
    "serie-- sobre un eje ordenado (tiempo, versiones, tamaños) o comparando la MISMA "
    "medida entre categorías. Si lo que importan son los números exactos, o si son pocos "
    "datos, usa 'mostrar_tabla': una gráfica que no se entiende es peor que una tabla. "
    "Todas las series comparten las mismas etiquetas del eje (si miden cosas distintas, son "
    "gráficas distintas). Una sola serie necesita al menos 3 puntos, y si todos los valores "
    "son iguales la herramienta se niega y te dice por qué. Los números tienen que salir de "
    "algo que MEDISTE en este turno, no de tu memoria: una gráfica inventada se ve igual de "
    "convincente que una real."
)

TOOLS: list[ToolSpec] = [
    ToolSpec(
        name="mostrar_tabla",
        description=_DESCRIPCION_TABLA,
        input_schema={
            "type": "object",
            "properties": {
                "titulo": {"type": "string", "maxLength": MAX_TITULO_CHARS},
                "columnas": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_TABLE_COLUMNS,
                    "items": {
                        "type": "object",
                        "properties": {
                            "clave": {
                                "type": "string",
                                "description": (
                                    "Identificador corto con el que las filas buscan esta "
                                    "columna (ej. 'costo')."
                                ),
                            },
                            "titulo": {
                                "type": "string",
                                "description": "Lo que se ve en el encabezado.",
                            },
                            "alineacion": {"type": "string", "enum": list(ALINEACIONES)},
                        },
                        "required": ["clave", "titulo"],
                        "additionalProperties": False,
                    },
                },
                "filas": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_TABLE_ROWS,
                    "description": (
                        "Cada fila es un objeto con las CLAVES de las columnas, no una "
                        'lista: [{"opcion": "Workers AI", "costo": "0.11"}]. Si un dato no '
                        "existe, omite esa clave (se dibuja vacía); no pongas 0 ni '-'."
                    ),
                    "items": {"type": "object"},
                },
                "nota": {
                    "type": "string",
                    "maxLength": MAX_NOTA_CHARS,
                    "description": "De dónde salieron los datos o qué queda fuera.",
                },
            },
            "required": ["columnas", "filas"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="mostrar_grafica",
        description=_DESCRIPCION_GRAFICA,
        input_schema={
            "type": "object",
            "properties": {
                "tipo": {"type": "string", "enum": ["barras", "lineas"]},
                "titulo": {
                    "type": "string",
                    "maxLength": MAX_TITULO_CHARS,
                    "description": "Qué se está midiendo. Sin esto la gráfica es un acertijo.",
                },
                "series": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_CHART_SERIES,
                    "items": {
                        "type": "object",
                        "properties": {
                            "nombre": {"type": "string"},
                            "puntos": {
                                "type": "array",
                                "minItems": 2,
                                "maxItems": MAX_CHART_POINTS,
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "etiqueta": {
                                            "type": "string",
                                            "maxLength": MAX_ETIQUETA_CHARS,
                                            "description": (
                                                "Lo que va en el eje horizontal: fecha, "
                                                "versión, nombre del caso."
                                            ),
                                        },
                                        "valor": {"type": "number"},
                                    },
                                    "required": ["etiqueta", "valor"],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": ["nombre", "puntos"],
                        "additionalProperties": False,
                    },
                },
                "eje_x": {"type": "string", "maxLength": MAX_COLUMNA_TITULO_CHARS},
                "eje_y": {
                    "type": "string",
                    "maxLength": MAX_COLUMNA_TITULO_CHARS,
                    "description": "La unidad de los valores (ms, MB, %, USD).",
                },
                "nota": {"type": "string", "maxLength": MAX_NOTA_CHARS},
            },
            "required": ["tipo", "titulo", "series"],
            "additionalProperties": False,
        },
    ),
]
