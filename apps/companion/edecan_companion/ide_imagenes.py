"""Imágenes como contexto del compositor del IDE (T2.1 del plan de paridad).

Pegar o arrastrar una captura al compositor y que el agente la vea -- "arregla
este error" con una foto de la pantalla, o "haz que se vea así" con una
referencia visual. Este módulo resuelve las DOS mitades del encargo, en
orden, porque la segunda depende de la primera:

1. ``validar_y_normalizar_imagen`` -- ¿la imagen que llegó es de verdad una
   imagen, de un tipo permitido, dentro del tope de tamaño, y lista para
   viajar? No se confía en el ``content_type`` que declara el navegador (un
   ``<input type=file>`` o un ``data:`` URL puede declarar cualquier cosa):
   se huele la firma binaria real y, si Pillow está instalado, se decodifica
   y se vuelve a guardar -- lo que de paso descarta archivos que pasan la
   firma pero están corruptos o son otra cosa disfrazada de imagen.

2. ``verificar_modelo_ve`` -- no todos los modelos de ``modelos_ide`` en
   ``config/modelos.yml`` tienen la capacidad ``vision`` (hoy: Kimi K2.7 Code
   y Llama 4 Scout). Adjuntar una imagen y mandarla igual a un modelo ciego es
   peor que rechazarla: el modelo responde con confianza sobre algo que nunca
   vio. Por la regla ``sin_nombres_de_modelo_en_el_codigo`` de ese archivo,
   este módulo NUNCA compara ``model_id`` contra un nombre conocido -- solo
   consulta la lista de ``capacidades`` que declara el propio YAML (o, en
   pruebas, la lista inyectada por quien llama), igual que ya hace
   ``ide_contexto.analizar_contexto`` con la ventana de contexto por modelo.

``preparar_imagen_para_turno`` encadena ambas: primero confirma que el modelo
activo ve (barato, sin tocar bytes de imagen) y solo entonces valida/normaliza
-- así el error, cuando lo hay, sale antes de gastar tiempo decodificando.

El bloque final (``ImagenPreparada.bloque``) usa la MISMA forma que ya valida
``edecan_llm.multimodal.image_source``: ``{"type": "image", "source":
{"type": "base64", "media_type": ..., "data": ...}}``. No se reimplementa esa
validación ni se importa (es de ``edecan_llm``, no de este paquete) -- se
imita su contrato a propósito para que, en el lado que arma el
``CompletionRequest``, el bloque de este módulo sea intercambiable con
cualquier otro que ya use ``image_sources()``/``materialize_request_images()``
sin cambios.

Como ``ide_contexto.py``/``ide_modos.py``, este archivo no importa
``ide_sessions.py`` ni los cuellos de botella (``routers/ide.py``,
``apps/web/.../ide/page.tsx``) -- es puro cálculo/validación en memoria; quien
integre decide de dónde saca los bytes (WebSocket, subida HTTP) y el
``model_id`` vigente de la sesión.
"""

from __future__ import annotations

import base64
import io
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from edecan_llm.task_router import modelos_ide_disponibles


class IDEImagenError(ValueError):
    """Imagen inválida, demasiado grande/corrupta, o modelo activo sin
    capacidad de visión."""


# --------------------------------------------------------------------------- #
# 1. Validación y normalización de la imagen
# --------------------------------------------------------------------------- #

MAX_IMAGEN_BYTES = 10 * 1024 * 1024
"""10 MiB -- el mismo tope que ``edecan_llm.multimodal`` aplica del lado del
proveedor. Rechazar acá, antes de codificar en base64 y armar el
``CompletionRequest``, evita gastar esa vuelta completa para descubrir el
mismo rechazo más tarde."""

# Firmas binarias (magic bytes) de los cuatro tipos que se aceptan -- el
# MISMO conjunto que ``edecan_llm.multimodal`` conoce (gif, jpeg, png, webp).
# Se sniffea el contenido real en vez de confiar en lo declarado: un
# navegador (o un archivo renombrado a mano) puede declarar cualquier
# ``content_type``.
_FIRMAS_SIMPLES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)

_PIL_FORMATO: dict[str, str] = {
    "image/png": "PNG",
    "image/jpeg": "JPEG",
    "image/gif": "GIF",
    "image/webp": "WEBP",
}

TIPOS_PERMITIDOS: frozenset[str] = frozenset(_PIL_FORMATO)


def _detectar_mime(data: bytes) -> str | None:
    """Firma real de ``data``, o ``None`` si no calza con ningún tipo
    permitido. WEBP no tiene una firma de prefijo simple: es un contenedor
    RIFF (``RIFF????WEBP``), por eso se revisa aparte de la tabla."""
    for firma, mime in _FIRMAS_SIMPLES:
        if data.startswith(firma):
            return mime
    if len(data) >= 12 and data[0:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


@dataclass(frozen=True)
class ImagenPreparada:
    """Resultado de validar/normalizar una imagen: metadatos para pintar en
    la UI (``public``) y el bloque ya listo para el proveedor (``bloque``,
    que SÍ lleva los datos en base64 -- se pide aparte a propósito, para no
    inflar el resumen de estado con el peso completo de la imagen)."""

    mime: str
    tamano_bytes: int
    ancho: int | None
    alto: int | None
    recodificada: bool

    def bloque(self, datos_b64: str) -> dict[str, Any]:
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": self.mime, "data": datos_b64},
        }

    def public(self) -> dict[str, Any]:
        return {
            "mime": self.mime,
            "tamano_bytes": self.tamano_bytes,
            "ancho": self.ancho,
            "alto": self.alto,
            "recodificada": self.recodificada,
        }


@dataclass(frozen=True)
class ImagenLista:
    """``ImagenPreparada`` más el bloque ya armado -- lo que de verdad
    consume quien construye el ``CompletionRequest`` del turno."""

    preparada: ImagenPreparada
    bloque: dict[str, Any]

    def public(self) -> dict[str, Any]:
        return self.preparada.public()


def validar_y_normalizar_imagen(
    data: bytes,
    *,
    content_type_declarado: str | None = None,
    max_bytes: int = MAX_IMAGEN_BYTES,
) -> ImagenLista:
    """Valida ``data`` como imagen y la deja lista para el proveedor.

    Pasos, en orden -- cada uno puede rechazar antes de que el siguiente
    haga trabajo más caro:

    1. No vacía, no más pesada que ``max_bytes``.
    2. Firma binaria real dentro de ``TIPOS_PERMITIDOS`` (PNG/JPEG/GIF/WEBP).
    3. Si se declaró un ``content_type`` de imagen concreto, debe coincidir
       con la firma real -- si no, se rechaza por seguridad (el navegador
       miente, o el archivo fue renombrado).
    4. Si Pillow está instalado, se decodifica y se vuelve a guardar --
       ``ide_recodificada`` en ``True`` -- lo que además da ancho/alto reales
       y descarta payloads corruptos o disfrazados que solo pasaron la
       firma. Sin Pillow (mismo caso que ``actions.py`` sin el extra
       ``remote-control``), se acepta la imagen tal cual llegó: valida menos,
       pero sigue funcionando.
    """
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise IDEImagenError("La imagen viene vacía o no es un contenido binario.")
    crudo = bytes(data)

    if len(crudo) > max_bytes:
        raise IDEImagenError(
            f"La imagen pesa {len(crudo)} bytes; el tope permitido es {max_bytes} bytes "
            f"(~{max_bytes // (1024 * 1024)} MiB)."
        )

    mime = _detectar_mime(crudo)
    if mime is None:
        raise IDEImagenError(
            "Tipo de imagen no reconocido: solo se aceptan PNG, JPEG, GIF o WEBP. "
            "Revisa que el archivo no sea otra cosa renombrada con esa extensión."
        )

    if content_type_declarado:
        declarado = content_type_declarado.split(";", 1)[0].strip().lower()
        if declarado.startswith("image/") and declarado != mime:
            raise IDEImagenError(
                f"El tipo declarado ({declarado}) no coincide con el contenido real de "
                f"la imagen ({mime}); se rechaza por seguridad."
            )

    ancho: int | None = None
    alto: int | None = None
    datos_finales = crudo
    recodificada = False

    try:
        from PIL import Image  # type: ignore[import-not-found]
    except ImportError:
        Image = None  # type: ignore[assignment]

    if Image is not None:
        try:
            with Image.open(io.BytesIO(crudo)) as sonda:
                sonda.verify()
            with Image.open(io.BytesIO(crudo)) as imagen:
                ancho, alto = imagen.size
                formato_pil = _PIL_FORMATO[mime]
                convertida = imagen.convert("RGB") if formato_pil == "JPEG" else imagen
                buffer = io.BytesIO()
                convertida.save(buffer, format=formato_pil, optimize=True)
                datos_finales = buffer.getvalue()
                recodificada = True
        except Exception as exc:
            # Pillow lanza tipos distintos según qué salga mal (formato
            # corrupto, bomba de descompresión, paleta inválida...); el
            # criterio acá es de seguridad, no de precisión de excepción:
            # cualquier fallo al decodificar/recodificar es motivo de
            # rechazo, nunca de dejar pasar bytes sin confirmar que son una
            # imagen real.
            raise IDEImagenError(
                f"La imagen tiene una firma {mime} válida pero el contenido está "
                f"corrupto o no se pudo procesar como imagen: {exc}"
            ) from exc

    if len(datos_finales) > max_bytes:
        raise IDEImagenError(
            "La imagen, ya normalizada, sigue superando el tope de tamaño permitido."
        )

    preparada = ImagenPreparada(
        mime=mime,
        tamano_bytes=len(datos_finales),
        ancho=ancho,
        alto=alto,
        recodificada=recodificada,
    )
    datos_b64 = base64.b64encode(datos_finales).decode("ascii")
    return ImagenLista(preparada=preparada, bloque=preparada.bloque(datos_b64))


# --------------------------------------------------------------------------- #
# 2. ¿El modelo activo ve?
# --------------------------------------------------------------------------- #


def _buscar_modelo(
    model_id: str,
    *,
    modelos: Sequence[Mapping[str, Any]] | None,
    ruta_yaml: Path | str | None,
) -> Mapping[str, Any] | None:
    tabla = modelos if modelos is not None else modelos_ide_disponibles(ruta_yaml)
    for fila in tabla:
        if str(fila.get("id")) == model_id:
            return fila
    return None


def modelo_soporta_vision(
    model_id: str,
    *,
    modelos: Sequence[Mapping[str, Any]] | None = None,
    ruta_yaml: Path | str | None = None,
) -> bool:
    """``True`` si ``model_id`` está en la lista de modelos del IDE Y trae
    ``vision`` en sus ``capacidades``. Un modelo desconocido cuenta como sin
    visión -- nunca se asume capacidad de un modelo que ni siquiera está
    declarado."""
    fila = _buscar_modelo(model_id, modelos=modelos, ruta_yaml=ruta_yaml)
    if fila is None:
        return False
    capacidades = fila.get("capacidades")
    return isinstance(capacidades, list) and "vision" in capacidades


def verificar_modelo_ve(
    model_id: str,
    *,
    modelos: Sequence[Mapping[str, Any]] | None = None,
    ruta_yaml: Path | str | None = None,
) -> Mapping[str, Any]:
    """Confirma que ``model_id`` ve imágenes; devuelve su ModelCard si sí.

    Lanza ``IDEImagenError`` con un mensaje claro para mostrar en la UI si
    el modelo no está declarado en ``modelos_ide`` o no trae ``vision`` --
    la alternativa (mandar la imagen igual) es peor: el modelo responde
    como si la hubiera visto.
    """
    fila = _buscar_modelo(model_id, modelos=modelos, ruta_yaml=ruta_yaml)
    if fila is None:
        raise IDEImagenError(
            f"El modelo «{model_id}» no está en la lista de modelos del IDE; no se puede "
            "confirmar que vea imágenes, así que esta no se envía."
        )
    capacidades = fila.get("capacidades")
    if not (isinstance(capacidades, list) and "vision" in capacidades):
        nombre = fila.get("nombre") or model_id
        raise IDEImagenError(
            f"{nombre} no tiene capacidad de visión: no puede ver la imagen adjunta. "
            "Cambia a un modelo con la insignia «Visión» (o cualquiera cuyas capacidades "
            "incluyan «vision») antes de adjuntarla, o continúa la conversación solo con texto."
        )
    return fila


# --------------------------------------------------------------------------- #
# 3. Las dos mitades juntas
# --------------------------------------------------------------------------- #


def preparar_imagen_para_turno(
    data: bytes,
    model_id: str,
    *,
    content_type_declarado: str | None = None,
    max_bytes: int = MAX_IMAGEN_BYTES,
    modelos: Sequence[Mapping[str, Any]] | None = None,
    ruta_yaml: Path | str | None = None,
) -> ImagenLista:
    """``verificar_modelo_ve`` + ``validar_y_normalizar_imagen``, en ese
    orden: confirmar que el modelo ve es más barato que decodificar la
    imagen, así que el rechazo por modelo ciego sale primero."""
    verificar_modelo_ve(model_id, modelos=modelos, ruta_yaml=ruta_yaml)
    return validar_y_normalizar_imagen(
        data, content_type_declarado=content_type_declarado, max_bytes=max_bytes
    )
