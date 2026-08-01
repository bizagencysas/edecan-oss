"""Bloques ricos del IDE: el contrato tipado que viaja del companion al teléfono.

El chat ya tenía seis bloques (`edecan_schemas.chat`); el IDE no tenía
ninguno -- su flujo de eventos solo emitía texto, y una tabla o una gráfica
llegaban al teléfono como barras y guiones. Este módulo es el contrato de los
dos primeros bloques del IDE, y el patrón es EL MISMO que el del chat: un
bloque solo puede nacer del canal deliberado (`presentation`), nunca del
resultado que una herramienta le devuelve al modelo. Ese resultado es texto
que el modelo produjo o leyó de un archivo; si pudiera acuñar UI, cualquier
archivo del repo con la forma correcta pintaría una tarjeta en el teléfono de
la persona.

Por qué es una unión propia (`IDEBlock`) y no dos variantes más de
`ChatBlock`: son superficies distintas con clientes distintos. El hilo del
IDE es quien sabe dibujar esto; el chat todavía no. Agregarlos a la unión del
chat haría que el servidor le mandara a los clientes de chat un `type` que no
conocen, a cambio de nada. La base (`schema_version` + `fallback_text`) es
deliberadamente la misma que la de `ChatBlockBase` para que ese día sea un
cambio de una línea, no una migración.

Por qué la gráfica es tan pobre a propósito -- series con nombre y puntos
etiquetados, sin escalas, ejes, colores ni tipos de marca: cada superficie
dibuja con lo suyo (Swift Charts en iOS, otra librería en la web). Un
"lenguaje de gráficas" propio en el medio solo garantiza que ninguna de las
dos pueda usar lo que ya sabe hacer bien, y que cada mejora del dibujo tenga
que pasar primero por este archivo.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter, field_validator, model_validator

# Topes pensados para una pantalla de teléfono, no para un reporte. Una tabla
# de 200 filas en un hilo de chat no se lee: se hace scroll hasta perderla. El
# emisor recorta y lo dice (ver `TableBlock.note`), que es honesto; lo que no
# se puede es mandar el volcado entero y esperar que el cliente lo resuelva.
MAX_TABLE_COLUMNS = 8
MAX_TABLE_ROWS = 30
MAX_TABLE_CELL_CHARS = 120
MAX_CHART_SERIES = 6
MAX_CHART_POINTS = 60
# Mínimo de puntos de UNA serie sola. Dos puntos son una diferencia, no una
# forma: se dicen mejor en una frase ("subió de 420 ms a 510 ms") o en una
# tabla. Ver `motivo_grafica_degenerada`.
MIN_SINGLE_SERIES_POINTS = 3
# Tope de bloques que puede acuñar UN evento. Tres tarjetas seguidas ya son
# más de lo que cabe en una pantalla; más que eso es un volcado disfrazado.
MAX_BLOCKS_PER_EVENT = 3


def recortar_celda(valor: str) -> str:
    """Recorta a `MAX_TABLE_CELL_CHARS` DEJANDO la marca del recorte.

    Sin el "…", una ruta larga, una URL o un mensaje de error cortado se leen
    como el valor completo: la celda no revienta, se ve perfecta y dice otra
    cosa -- el mismo fracaso que las filas por clave (y no por posición)
    vienen a evitar. El resultado queda en exactamente `MAX_TABLE_CELL_CHARS`
    para que los clientes, que recortan al mismo tope, no se coman justo la
    marca que avisa del corte.
    """
    if len(valor) <= MAX_TABLE_CELL_CHARS:
        return valor
    return valor[: MAX_TABLE_CELL_CHARS - 1] + "…"


class IDEBlockBase(BaseModel):
    """Campos comunes para evolución y degradación a texto.

    `fallback_text` es OBLIGATORIO aquí (en el chat es opcional) porque el
    flujo de eventos del IDE es de texto por diseño: el historial que se le
    reinyecta al modelo, el `/export` a Markdown y el estimador de costos
    leen `event["text"]` y nada más. Un bloque sin texto equivalente sería
    invisible para todos ellos y para cualquier cliente que todavía no sepa
    dibujarlo.
    """

    schema_version: Literal[1] = 1
    fallback_text: str = Field(min_length=1, max_length=4000)


class TableColumn(BaseModel):
    """Una columna: `key` es la que buscan las filas, `title` la que se ve."""

    key: str = Field(min_length=1, max_length=40, pattern=r"^[a-zA-Z0-9_][a-zA-Z0-9_.-]*$")
    title: str = Field(min_length=1, max_length=60)
    # La alineación no es cosmética: una columna de números alineada a la
    # izquierda obliga a comparar dígito por dígito, que es justo lo que una
    # tabla viene a evitar.
    align: Literal["left", "center", "right"] = "left"


class TableBlock(IDEBlockBase):
    """Tabla de columnas fijas y filas indexadas POR CLAVE, no por posición.

    Las celdas van en un `dict` (`{"clave_de_columna": "valor"}`) y no en una
    lista posicional a propósito, y es la decisión que hace que una tabla con
    celdas faltantes no reviente ni mienta:

    - Una celda ausente significa "sin dato" y solo afecta a SU columna. El
      cliente pinta ahí su marcador de vacío ("—"): nunca un cero, que sería
      un dato inventado.
    - Con filas posicionales, en cambio, una celda faltante corre todas las
      siguientes una posición a la izquierda: la fila se sigue dibujando, se
      ve perfecta, y cada valor queda bajo la columna equivocada. Un render
      que no revienta pero miente es peor que uno que revienta.

    Una clave que no corresponde a ninguna columna se DESCARTA en validación
    (no hay dónde dibujarla, y dejarla pasar convertiría el canal de UI en un
    transporte de datos arbitrarios). Sobrar claves no invalida la tabla: la
    fila sigue siendo legible sin ellas. Una celda vacía ("") se normaliza a
    ausente para que "sin dato" tenga UNA sola representación en el cable y el
    cliente no necesite dos ramas para pintar lo mismo.
    """

    type: Literal["table"] = "table"
    title: str | None = Field(default=None, max_length=120)
    columns: list[TableColumn] = Field(min_length=1, max_length=MAX_TABLE_COLUMNS)
    rows: list[dict[str, str]] = Field(min_length=1, max_length=MAX_TABLE_ROWS)
    # Para lo que el emisor tenga que confesar: "se muestran 30 de 128 filas",
    # de dónde salieron los números, cuándo se midieron.
    note: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def validate_shape(self) -> TableBlock:
        keys = [column.key for column in self.columns]
        if len(keys) != len(set(keys)):
            raise ValueError("las columnas de una tabla no pueden repetir 'key'")
        allowed = set(keys)
        self.rows = [
            {
                key: recortar_celda(value)
                for key, value in row.items()
                if key in allowed and value != ""
            }
            for row in self.rows
        ]
        if not any(row for row in self.rows):
            raise ValueError("la tabla no tiene ni una celda con datos")
        return self


class ChartPoint(BaseModel):
    """Un punto: una etiqueta del eje y un número. Nada más.

    `label` es siempre texto, incluso para fechas o versiones, para que el eje
    sea categórico y ordenado por el emisor. Un eje numérico "de verdad"
    obligaría a acordar escalas, formatos de fecha y husos horarios entre
    Python, Swift y TypeScript -- tres formas distintas de equivocarse en lo
    mismo -- a cambio de un espaciado más fiel que en una pantalla de teléfono
    casi nunca se nota.
    """

    label: str = Field(min_length=1, max_length=40)
    value: float

    @field_validator("value")
    @classmethod
    def validate_finite(cls, value: float) -> float:
        # NaN e infinito atraviesan JSON como `NaN`/`Infinity` (JSON inválido)
        # o revientan el cálculo del dominio del eje en el cliente. Es un dato
        # que no se puede dibujar, así que no puede existir en el bloque.
        if not math.isfinite(value):
            raise ValueError("un punto de la gráfica no puede ser NaN ni infinito")
        return value


class ChartSeries(BaseModel):
    """Una serie con nombre. Todas las series comparten las mismas etiquetas."""

    name: str = Field(min_length=1, max_length=60)
    points: list[ChartPoint] = Field(min_length=2, max_length=MAX_CHART_POINTS)

    @model_validator(mode="after")
    def validate_labels(self) -> ChartSeries:
        labels = [point.label for point in self.points]
        if len(labels) != len(set(labels)):
            # Dos barras con la misma etiqueta son indistinguibles: quien lea
            # la gráfica no puede saber cuál es cuál.
            raise ValueError(f"la serie «{self.name}» repite una etiqueta del eje")
        return self


def motivo_grafica_degenerada(series: Sequence[ChartSeries]) -> str | None:
    """Por qué estos datos NO forman una gráfica, o `None` si sí la forman.

    Existe como función con nombre, y no como un `if` escondido en el
    validador, porque tiene dos consumidores con necesidades distintas:
    `ChartBlock` la usa para que un bloque degenerado no pueda construirse, y
    la herramienta del agente la usa para devolverle al modelo el motivo
    exacto y decirle qué hacer en su lugar. Un "argumento inválido" pelado no
    le enseña nada al modelo; "dos puntos son una diferencia, usa la tabla"
    sí.

    Una gráfica que no se entiende es peor que una tabla: ocupa más pantalla,
    se lee más lento y encima parece un análisis. El modelo no tiene forma de
    saberlo -- para él graficar siempre se ve como el gesto más presentable --
    así que la regla vive acá, en código, y no en un consejo del prompt que se
    obedece a veces.
    """
    if len(series) == 1 and len(series[0].points) < MIN_SINGLE_SERIES_POINTS:
        return (
            f"una sola serie con {len(series[0].points)} puntos no es una gráfica: dos "
            "puntos son una diferencia, no una forma. Dilo en una frase o muéstralo con "
            "una tabla."
        )

    referencia = [point.label for point in series[0].points]
    for serie in series[1:]:
        if [point.label for point in serie.points] != referencia:
            return (
                f"la serie «{serie.name}» no usa las mismas etiquetas del eje que "
                f"«{series[0].name}». Varias series solo se pueden comparar sobre el mismo "
                "eje; si miden cosas distintas, son gráficas distintas (o una tabla)."
            )

    valores = {point.value for serie in series for point in serie.points}
    if len(valores) == 1:
        return (
            "todos los valores son idénticos: la gráfica sería una línea plana o barras "
            "iguales, y no muestra nada que una frase no diga mejor."
        )
    return None


class ChartBlock(IDEBlockBase):
    """Barras o líneas. Series con nombre y puntos etiquetados, y ya.

    `title` es obligatorio: una gráfica sin título es un acertijo -- se ve la
    forma pero no qué se está midiendo, y quien la mira termina preguntando lo
    que la gráfica venía a responder.
    """

    type: Literal["chart"] = "chart"
    chart_kind: Literal["bar", "line"]
    title: str = Field(min_length=1, max_length=120)
    series: list[ChartSeries] = Field(min_length=1, max_length=MAX_CHART_SERIES)
    x_label: str | None = Field(default=None, max_length=60)
    y_label: str | None = Field(default=None, max_length=60)
    note: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def validate_shape(self) -> ChartBlock:
        nombres = [serie.name for serie in self.series]
        if len(nombres) != len(set(nombres)):
            raise ValueError("dos series de la misma gráfica no pueden llamarse igual")
        motivo = motivo_grafica_degenerada(self.series)
        if motivo is not None:
            raise ValueError(motivo)
        return self


IDEBlock = Annotated[TableBlock | ChartBlock, Field(discriminator="type")]
"""Bloque rico del IDE, allowlisted: lo que el hilo del IDE sabe dibujar."""

IDEBlockAdapter: TypeAdapter[IDEBlock] = TypeAdapter(IDEBlock)


def ide_blocks_from_presentation(
    presentation: Any,
    *,
    max_blocks: int = MAX_BLOCKS_PER_EVENT,
) -> list[IDEBlock]:
    """Proyecta el canal `presentation` de un evento a bloques válidos.

    Mismo criterio que `edecan_core.agent.rich_blocks_from_tool_data` para el
    chat: lo que no valida se DESCARTA en silencio en vez de tumbar el evento
    completo. El texto del evento (`event["text"]`) siempre sobrevive, así que
    el peor caso de un bloque mal formado -- o de un companion más nuevo que
    este servidor -- es que la persona vea el texto en vez de la tarjeta, no
    que pierda el mensaje.
    """
    if not isinstance(presentation, list):
        return []
    blocks: list[IDEBlock] = []
    seen: set[str] = set()
    for raw in presentation[: max_blocks * 4]:
        try:
            block = IDEBlockAdapter.validate_python(raw)
        except ValueError:
            continue
        key = block.model_dump_json()
        if key in seen:
            continue
        seen.add(key)
        blocks.append(block)
        if len(blocks) >= max_blocks:
            break
    return blocks
