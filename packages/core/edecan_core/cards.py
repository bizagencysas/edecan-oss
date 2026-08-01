"""Helper reutilizable para armar `GenericCardBlock` (MVP de Server-Driven UI,
paso 5 del plan: el piloto backend-only).

Hasta ahora, cualquier tool que quería mostrar una UI rica tenía que escribir
a mano el `dict` de `presentation` con la forma exacta de las 6 primitivas
(`edecan_creative.social._decidir_destino`/`empaquetar_borrador_social` es el
ejemplo real). `construir_card_generica` es la capa que faltaba: describe la
card con datos simples -- kicker, título, badge, imagen, líneas de cuerpo,
botones -- y arma el árbol `StackNode` correcto por debajo, sin que el
llamador tenga que conocer la forma de `TextoNode`/`ImagenNode`/`BotonNode` ni
en qué orden anidarlos.

Los límites duros (profundidad ≤ `MAX_PROFUNDIDAD_CARD`, ≤ `MAX_NODOS_CARD`
nodos, ≤ `MAX_BOTONES_CARD` botones) NO se reimplementan aquí: los valida
`GenericCardBlock.validate_limites` (`edecan_schemas.chat`) al construir el
bloque, así que un árbol patológico revienta con la MISMA `ValidationError`
de siempre en vez de arrastrar una segunda copia de la regla que pueda
divergir con el tiempo.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from edecan_schemas import (
    ArtifactRef,
    BadgeNode,
    BotonNode,
    ChatAction,
    DivisorNode,
    GenericCardBlock,
    ImagenNode,
    NodoCard,
    StackNode,
    TextoNode,
)

ColorTexto = Literal["primario", "secundario", "acento", "advertencia", "exito"]
EstiloBoton = Literal["primario", "secundario", "discreto", "destructivo"]
TonoBadge = Literal["neutro", "acento", "advertencia", "exito"]
# Mismo enum que `ImagenNode.aspecto` (`edecan_schemas.chat`): "cuadrada" -> 1:1,
# "panoramica" -> 16:9, "libre" -> sin forzar proporción. Ver `imagen_aspecto` más abajo.
AspectoImagen = Literal["cuadrada", "panoramica", "libre"]


@dataclass(frozen=True)
class LineaCuerpo:
    """Una línea de texto de cuerpo con color/énfasis propios.

    Pasar un `str` suelto en `cuerpo=` (en vez de esta clase) alcanza para el
    caso común -- texto primario sin énfasis; esta clase existe solo para
    cuando una línea puntual necesita destacar (p. ej. una advertencia en
    `advertencia` + `fuerte`) sin forzar ese estilo a las demás.
    """

    texto: str
    color: ColorTexto = "primario"
    enfasis: Literal["normal", "fuerte"] = "normal"


@dataclass(frozen=True)
class BotonCard:
    """Un botón de la card: una `ChatAction` ya construida por el llamador
    (del allowlist de `edecan_schemas.chat.ChatAction`) más su estilo visual.
    """

    accion: ChatAction
    estilo: EstiloBoton = "primario"


@dataclass(frozen=True)
class BadgeCard:
    """El remate visual de `BadgeNode`: contenido corto + tono semántico."""

    contenido: str
    tono: TonoBadge = "neutro"


def construir_card_generica(
    *,
    card_id: str,
    fallback_text: str,
    kicker: str | None = None,
    titulo: str | None = None,
    badge: BadgeCard | None = None,
    imagen: ArtifactRef | None = None,
    imagen_alt: str = "",
    imagen_aspecto: AspectoImagen = "panoramica",
    cuerpo: tuple[str | LineaCuerpo, ...] | list[str | LineaCuerpo] = (),
    botones: tuple[BotonCard, ...] | list[BotonCard] = (),
) -> GenericCardBlock:
    """Arma una `GenericCardBlock` a partir de datos simples.

    `imagen_aspecto` ("panoramica" por defecto -- EL MISMO valor que este helper ya
    forzaba antes de que este parámetro existiera, así que ningún llamador que no lo
    pase cambia de comportamiento): el marco 16:9 le queda bien a una foto apaisada,
    pero le recorta arriba y abajo a una foto CUADRADA compuesta con titular (el
    titular vive en la franja inferior, y ahí es justo donde se pierde el recorte).
    `crear_post_linkedin` (`apps/worker/.../create_linkedin_post.py::_armar_card`)
    pasa `"cuadrada"` a propósito porque su imagen SÍ es cuadrada de punta a punta
    (Ada, corrección del 31-jul: "haz que la imagen se vea en 500x500 para poder
    verla bien, así se ve encogida en la app").

    Orden fijo y deliberado del árbol (el mismo para cualquier llamador, para
    que dos cards de dos tools distintas se vean consistentes sin que cada
    una tenga que decidir un layout): `kicker` (etiqueta) -> `titulo` ->
    `badge` -> `imagen` -> líneas de `cuerpo` -> (si hay `botones`) un
    `divisor` y una fila horizontal de botones.

    Todos los parámetros que arman un nodo son opcionales -- omitir `kicker`,
    `badge` o `imagen` simplemente no agrega ese nodo, no dejan un hueco vacío.
    `card_id`/`fallback_text` sí son obligatorios: son los dos campos que
    `GenericCardBlock` exige siempre (identidad para telemetría, y la red
    final de un cliente sin el renderer genérico -- ver `edecan_schemas.chat`).

    No atrapa `ValidationError`: un árbol que exceda los límites duros (más
    de `MAX_BOTONES_CARD` botones, por ejemplo) debe reventar aquí mismo,
    fuerte y temprano, en vez de que el llamador crea que construyó una card
    válida.
    """

    hijos: list[NodoCard] = []
    if kicker:
        hijos.append(TextoNode(contenido=kicker, rol="etiqueta", color="secundario"))
    if titulo:
        hijos.append(TextoNode(contenido=titulo, rol="titulo", enfasis="fuerte"))
    if badge is not None:
        hijos.append(BadgeNode(contenido=badge.contenido, tono=badge.tono))
    if imagen is not None:
        hijos.append(
            ImagenNode(artifact=imagen, alt=imagen_alt, aspecto=imagen_aspecto, esquinas="m")
        )
    for linea in cuerpo:
        if isinstance(linea, LineaCuerpo):
            hijos.append(
                TextoNode(
                    contenido=linea.texto,
                    rol="cuerpo",
                    color=linea.color,
                    enfasis=linea.enfasis,
                )
            )
        else:
            hijos.append(TextoNode(contenido=str(linea), rol="cuerpo"))
    if botones:
        hijos.append(DivisorNode())
        hijos.append(
            StackNode(
                eje="horizontal",
                espaciado="s",
                hijos=[BotonNode(estilo=b.estilo, accion=b.accion) for b in botones],
            )
        )

    raiz = StackNode(eje="vertical", espaciado="m", relleno="tarjeta", hijos=hijos)
    return GenericCardBlock(card_id=card_id, fallback_text=fallback_text, raiz=raiz)
